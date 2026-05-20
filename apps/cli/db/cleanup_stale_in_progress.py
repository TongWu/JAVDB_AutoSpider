"""Daily cron: roll back ReportSessions stuck in ``in_progress`` / drive ``finalizing`` to ``committed``.

Used by the ``StaleSessionCleanup.yml`` workflow. Walks every
``ReportSessions`` row whose ``Status IN ('in_progress', 'finalizing')``
and whose ``DateTimeCreated`` is older than ``--max-age-hours``
(default 48), and dispatches per-session:

* ``Status='in_progress'`` (audit or pending) — rolled back via
  :func:`db_rollback_session` with ``failure_reason='stale_timeout'``.
* ``Status='finalizing'`` (pending only) — driven to ``committed`` via
  :func:`db_resume_finalizing_session`.  The legacy behaviour was to
  rollback these too, but the Phase 3 contract is that finalizing
  sessions must NEVER lose their already-half-applied writes — they
  are idempotent and resume cleanly.

Default mode is dry-run; the cron passes ``--apply`` after a manual
review of the first run's output.

Exit codes
----------
* 0 — success (or dry-run finished with no errors)
* 3 — could not connect to the local DB / D1
* 4 — at least one session failed to roll back / resume cleanly
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from javdb.storage.db import db as _db
from javdb.storage.db.db_connection import close_db, get_db
from javdb.storage.db.db_reports import (
    db_find_in_progress_sessions,
    db_get_session_status,
)
from javdb.storage.db.db_rollback import (
    db_resume_finalizing_session,
    db_rollback_session,
)
from javdb.storage.db.db_migrations import init_db
from javdb.infra.logging import (
    get_logger,
    setup_logging,
)


logger = get_logger(__name__)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="apps.cli.db.cleanup_stale_in_progress",
        description=(
            "Roll back ReportSessions whose Status='in_progress' for "
            "longer than --max-age-hours. Default dry-run."
        ),
    )
    p.add_argument(
        "--max-age-hours",
        type=float,
        default=48.0,
        help="Sessions older than now()-max_age_hours are eligible "
             "(default: 48).",
    )
    p.set_defaults(dry_run=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="(default) Print what would be cleaned up without modifying "
             "any DB.",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually perform the cleanup.",
    )
    p.add_argument(
        "--scope",
        choices=["reports", "operations", "history", "all"],
        default="all",
        help="Scope passed through to db_rollback_session. Default: all.",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    p.add_argument(
        "--include-legacy",
        action="store_true",
        default=False,
        help=(
            "Also include legacy in_progress sessions with empty RunId. "
            "Default false to avoid sweeping pre-run-identity history."
        ),
    )
    return p.parse_args(argv)


def _read_session_meta(session_id: str) -> dict:
    with get_db(_db.REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Id, ReportType, ReportDate, DisplayName, Status, "
            "DateTimeCreated, RunId, RunAttempt FROM ReportSessions "
            "WHERE Id=?",
            (session_id,),
        ).fetchone()
    if row is None:
        return {"Id": session_id}
    return {k: row[k] for k in row.keys()}


def run_stale_cleanup(
    max_age_hours: float = 48.0,
    scope: str = "all",
    dry_run: bool = True,
    include_legacy: bool = False,
) -> dict:
    """Programmatic entry point for stale-session cleanup.

    Returns a structured dict with cleanup results so callers (e.g. the
    FastAPI router) can map the outcome to an HTTP response without having
    to parse exit codes.

    Keys:
        sessions_found (int): Total stale sessions found.
        sessions_cleaned (int): Sessions successfully rolled back or resumed.
        sessions_failed (int): Sessions that could not be cleaned.
        dry_run (bool): Whether this was a dry run.
        details (list[dict]): Per-session action summaries.
    """
    try:
        init_db()
    except Exception as e:
        raise RuntimeError(f"Failed to init DB: {e}") from e

    try:
        from javdb.storage.db.db_reports import db_find_stale_pending_sessions
        rows = db_find_stale_pending_sessions(
            max_age_hours=max_age_hours,
            require_run_identity=not include_legacy,
        )
    except Exception as e:
        close_db()
        raise RuntimeError(f"Failed to query stale sessions: {e}") from e

    if not rows:
        close_db()
        return {
            "sessions_found": 0,
            "sessions_cleaned": 0,
            "sessions_failed": 0,
            "dry_run": dry_run,
            "details": [],
        }

    summaries: List[dict] = []
    failed: List[str] = []
    resume_successes = 0
    resume_failures = 0

    for sid, status, write_mode in rows:
        meta = _read_session_meta(sid)
        if dry_run:
            if status == 'finalizing' and write_mode == 'pending':
                action = "resume_commit"
                would_apply = True
            elif status == 'finalizing':
                action = "skipped"
                would_apply = False
            else:
                action = "rollback"
                would_apply = True
            summaries.append({
                "session_id": sid,
                "status": status,
                "write_mode": write_mode,
                "meta": meta,
                "would_apply": would_apply,
                "action": action,
            })
            continue

        if status == 'finalizing' and write_mode == 'pending':
            try:
                counts = db_resume_finalizing_session(sid)
                summaries.append({
                    "session_id": sid,
                    "status": status,
                    "write_mode": write_mode,
                    "meta": meta,
                    "action": "resume_commit",
                    "counts": counts,
                })
                resume_successes += 1
            except Exception as e:
                failed.append(sid)
                resume_failures += 1
                summaries.append({
                    "session_id": sid,
                    "status": status,
                    "write_mode": write_mode,
                    "action": "resume_commit",
                    "error": str(e),
                })
            continue

        if status == 'finalizing':
            failed.append(sid)
            summaries.append({
                "session_id": sid,
                "status": status,
                "write_mode": write_mode,
                "action": "skipped",
                "error": "audit_finalizing_unexpected",
            })
            continue

        try:
            counts = db_rollback_session(
                sid,
                dry_run=False,
                scope=scope,
                force=False,
                run_started_at=None,
                failure_reason="stale_timeout",
                auto_resume_finalizing=True,
            )
            summaries.append({
                "session_id": sid,
                "status": status,
                "write_mode": write_mode,
                "meta": meta,
                "action": "rollback",
                "counts": counts,
            })
        except Exception as e:
            failed.append(sid)
            summaries.append({
                "session_id": sid,
                "status": status,
                "write_mode": write_mode,
                "action": "rollback",
                "error": str(e),
            })

    close_db()

    sessions_found = len(rows)
    sessions_failed = len(failed)
    sessions_cleaned = sessions_found - sessions_failed if not dry_run else 0

    return {
        "sessions_found": sessions_found,
        "sessions_cleaned": sessions_cleaned,
        "sessions_failed": sessions_failed,
        "dry_run": dry_run,
        "details": summaries,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)

    logger.info(
        "StaleSessionCleanup invoked: max_age_hours=%s scope=%s dry_run=%s "
        "include_legacy=%s",
        args.max_age_hours, args.scope, args.dry_run, args.include_legacy,
    )

    try:
        result = run_stale_cleanup(
            max_age_hours=args.max_age_hours,
            scope=args.scope,
            dry_run=args.dry_run,
            include_legacy=args.include_legacy,
        )
    except RuntimeError as e:
        logger.error("%s", e)
        return 3

    summaries = result["details"]
    sessions_found = result["sessions_found"]
    sessions_failed = result["sessions_failed"]

    if sessions_found == 0:
        logger.info(
            "No stale in_progress / finalizing sessions older than %s "
            "hour(s) — nothing to do.",
            args.max_age_hours,
        )

    in_progress_count = sum(1 for s in summaries if s.get("status") == 'in_progress')
    finalizing_count = sum(1 for s in summaries if s.get("status") == 'finalizing')
    resume_successes = sum(
        1 for s in summaries
        if s.get("action") == "resume_commit" and "error" not in s
    )
    resume_failures = sum(
        1 for s in summaries
        if s.get("action") == "resume_commit" and "error" in s
    )
    drift_total = 0
    orphan_pruned_total = 0
    for s in summaries:
        counts = s.get("counts", {})
        if isinstance(counts, dict):
            history = counts.get("history", {})
            if isinstance(history, dict):
                drift_total += int(history.get("drift_skipped", 0) or 0)
                orphan_pruned_total += int(history.get("orphan_pruned", 0) or 0)

    failed_sessions = [s["session_id"] for s in summaries if "error" in s]

    for s in summaries:
        if args.dry_run:
            logger.info(
                "[dry-run] Would handle session %s status=%s mode=%s",
                s.get("session_id"), s.get("status"), s.get("write_mode"),
            )
        elif "error" in s:
            if s.get("action") == "resume_commit":
                logger.error("Resume of finalizing session %s failed: %s", s.get("session_id"), s.get("error"))
            else:
                logger.error("Cleanup of session %s failed: %s", s.get("session_id"), s.get("error"))
        elif s.get("action") == "skipped":
            logger.warning(
                "Refusing to clean up audit-mode finalizing session %s — "
                "WriteMode=%s; manual investigation required.",
                s.get("session_id"), s.get("write_mode"),
            )
        elif s.get("action") == "resume_commit":
            logger.info(
                "Resumed finalizing session %s: %s",
                s.get("session_id"),
                json.dumps(s.get("counts", {}), ensure_ascii=False),
            )
        else:
            logger.info(
                "Cleaned up session %s: %s",
                s.get("session_id"),
                json.dumps(s.get("counts", {}), ensure_ascii=False),
            )

    if sessions_found > 0:
        logger.info(
            "Found %d stale session(s) (older than %sh): "
            "in_progress=%d finalizing=%d",
            sessions_found, args.max_age_hours,
            in_progress_count, finalizing_count,
        )

    summary = {
        "kind": "stale_session_cleanup",
        "ts": datetime.now(timezone.utc).isoformat(),
        "max_age_hours": args.max_age_hours,
        "scope": args.scope,
        "dry_run": args.dry_run,
        "session_count": sessions_found,
        "in_progress_count": in_progress_count,
        "finalizing_count": finalizing_count,
        "stale_resume_successes": resume_successes,
        "stale_resume_failures": resume_failures,
        "drift_total": drift_total,
        "orphan_pruned_total": orphan_pruned_total,
        "failed_sessions": failed_sessions,
        "sessions": summaries,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Append metric line so the parent workflow can grep it.  Dry-run
    # summaries go to a preview file so they don't pollute the live
    # Phase 3 aggregation stream.
    try:
        reports_dir = os.environ.get("REPORTS_DIR", "reports")
        if summary.get("dry_run"):
            path = os.path.join(
                reports_dir, "D1", "d1_drift_preview.jsonl",
            )
        else:
            path = os.path.join(reports_dir, "D1", "d1_drift.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(
            "Failed to append stale-cleanup metric to %s: %s",
            path, exc,
        )

    return 4 if sessions_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
