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

from packages.python.javdb_platform import db as _db
from packages.python.javdb_platform.db import (
    close_db,
    db_find_in_progress_sessions,
    db_get_session_status,
    db_resume_finalizing_session,
    db_rollback_session,
    init_db,
    get_db,
)
from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
)


logger = get_logger(__name__)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="apps.cli.cleanup_stale_in_progress",
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


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)

    logger.info(
        "StaleSessionCleanup invoked: max_age_hours=%s scope=%s dry_run=%s "
        "include_legacy=%s",
        args.max_age_hours, args.scope, args.dry_run, args.include_legacy,
    )

    try:
        init_db()
    except Exception as e:
        logger.error("Failed to init DB: %s", e)
        return 3

    # Phase 3: walk both in_progress AND finalizing sessions.  The new
    # helper returns (Id, Status, WriteMode) so we can dispatch per-row:
    #   in_progress → db_rollback_session (audit replay or DELETE pending)
    #   finalizing  → db_resume_finalizing_session (idempotent commit drive)
    try:
        from packages.python.javdb_platform.db import (
            db_find_stale_pending_sessions,
        )
        rows = db_find_stale_pending_sessions(
            max_age_hours=args.max_age_hours,
            require_run_identity=not args.include_legacy,
        )
    except Exception as e:
        logger.error("Failed to query stale sessions: %s", e)
        close_db()
        return 3

    if not rows:
        logger.info(
            "No stale in_progress / finalizing sessions older than %s "
            "hour(s) — nothing to do.",
            args.max_age_hours,
        )
        close_db()
        return 0

    in_progress_sessions = [r for r in rows if r[1] == 'in_progress']
    finalizing_sessions = [r for r in rows if r[1] == 'finalizing']
    sessions = [r[0] for r in rows]

    logger.info(
        "Found %d stale session(s) (older than %sh): "
        "in_progress=%d finalizing=%d",
        len(rows), args.max_age_hours,
        len(in_progress_sessions), len(finalizing_sessions),
    )

    summaries: List[dict] = []
    failed: List[int] = []
    drift_total = 0
    orphan_pruned_total = 0
    resume_successes = 0
    resume_failures = 0
    for sid, status, write_mode in rows:
        meta = _read_session_meta(sid)
        if args.dry_run:
            # Mirror the apply-path branching: only pending+finalizing
            # gets resumed; audit-mode finalizing is refused (would_apply
            # False) since the apply path also refuses to rollback it.
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
            logger.info(
                "[dry-run] Would handle session %s status=%s mode=%s",
                sid, status, write_mode,
            )
            continue

        if status == 'finalizing' and write_mode == 'pending':
            # Phase 3: drive the half-applied session to committed
            # rather than rolling it back.  resume is idempotent so
            # repeated cron runs converge.
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
                logger.info(
                    "Resumed finalizing session %s: %s",
                    sid, json.dumps(counts, ensure_ascii=False),
                )
            except Exception as e:
                logger.error(
                    "Resume of finalizing session %s failed: %s", sid, e,
                )
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
            # Audit-mode finalizing should not exist (only pending uses
            # finalizing).  Refuse to rollback so we don't lose data.
            logger.warning(
                "Refusing to clean up audit-mode finalizing session %s — "
                "WriteMode=%s; manual investigation required.",
                sid, write_mode,
            )
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
                scope=args.scope,
                force=False,
                # No --run-started-at for the cron — it processes all
                # stale sessions and orphan_pruned should not kick in
                # based on a single bogus reference time.
                run_started_at=None,
                failure_reason="stale_timeout",
                # Phase 3 belt-and-braces: should never fire here
                # because we routed finalizing above, but keep the
                # default-on flag explicit so any race that lands a
                # finalizing row in this branch still resumes.
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
            history = counts.get("history", {})
            drift_total += int(history.get("drift_skipped", 0) or 0)
            orphan_pruned_total += int(history.get("orphan_pruned", 0) or 0)
            logger.info(
                "Cleaned up session %s: %s",
                sid, json.dumps(counts, ensure_ascii=False),
            )
        except ValueError as e:
            logger.error("Refused to clean up session %s: %s", sid, e)
            failed.append(sid)
            summaries.append({
                "session_id": sid,
                "status": status,
                "write_mode": write_mode,
                "action": "rollback",
                "error": str(e),
            })
        except Exception as e:
            logger.error("Cleanup of session %s failed: %s", sid, e)
            failed.append(sid)
            summaries.append({
                "session_id": sid,
                "status": status,
                "write_mode": write_mode,
                "action": "rollback",
                "error": str(e),
            })

    summary = {
        "kind": "stale_session_cleanup",
        "ts": datetime.now(timezone.utc).isoformat(),
        "max_age_hours": args.max_age_hours,
        "scope": args.scope,
        "dry_run": args.dry_run,
        "session_count": len(rows),
        "in_progress_count": len(in_progress_sessions),
        "finalizing_count": len(finalizing_sessions),
        "stale_resume_successes": resume_successes,
        "stale_resume_failures": resume_failures,
        "drift_total": drift_total,
        "orphan_pruned_total": orphan_pruned_total,
        "failed_sessions": failed,
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

    close_db()
    return 4 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
