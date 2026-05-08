"""Daily cron: roll back ReportSessions stuck in ``in_progress`` for too long.

Used by the ``StaleSessionCleanup.yml`` workflow. Walks every
``ReportSessions`` row whose ``Status='in_progress'`` and whose
``DateTimeCreated`` is older than ``--max-age-hours`` (default 48), and
unwinds each one via :func:`db_rollback_session` with
``failure_reason='stale_timeout'``.

Default mode is dry-run; the cron passes ``--apply`` after a manual
review of the first run's output.

Exit codes
----------
* 0 — success (or dry-run finished with no errors)
* 3 — could not connect to the local DB / D1
* 4 — at least one session failed to roll back cleanly
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
    return p.parse_args(argv)


def _read_session_meta(session_id: int) -> dict:
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
        "StaleSessionCleanup invoked: max_age_hours=%s scope=%s dry_run=%s",
        args.max_age_hours, args.scope, args.dry_run,
    )

    try:
        init_db()
    except Exception as e:
        logger.error("Failed to init DB: %s", e)
        return 3

    try:
        sessions = db_find_in_progress_sessions(
            max_age_hours=args.max_age_hours,
        )
    except Exception as e:
        logger.error("Failed to query in_progress sessions: %s", e)
        close_db()
        return 3

    if not sessions:
        logger.info(
            "No in_progress sessions older than %s hour(s) — nothing to do.",
            args.max_age_hours,
        )
        close_db()
        return 0

    logger.info(
        "Found %d stale in_progress session(s) (older than %sh): %s",
        len(sessions), args.max_age_hours, sessions,
    )

    summaries: List[dict] = []
    failed: List[int] = []
    drift_total = 0
    orphan_pruned_total = 0
    for sid in sessions:
        meta = _read_session_meta(sid)
        if args.dry_run:
            summaries.append({
                "session_id": sid,
                "meta": meta,
                "would_apply": True,
            })
            logger.info(
                "[dry-run] Would clean up session %s (%s)", sid, meta,
            )
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
            )
            summaries.append({
                "session_id": sid,
                "meta": meta,
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
            # e.g. attempted to roll back a committed session — should
            # not happen because we only fetched in_progress, but cope.
            logger.error("Refused to clean up session %s: %s", sid, e)
            failed.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})
        except Exception as e:
            logger.error("Cleanup of session %s failed: %s", sid, e)
            failed.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})

    summary = {
        "kind": "stale_session_cleanup",
        "ts": datetime.now(timezone.utc).isoformat(),
        "max_age_hours": args.max_age_hours,
        "scope": args.scope,
        "dry_run": args.dry_run,
        "session_count": len(sessions),
        "drift_total": drift_total,
        "orphan_pruned_total": orphan_pruned_total,
        "failed_sessions": failed,
        "sessions": summaries,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Append metric line so the parent workflow can grep it.
    try:
        reports_dir = os.environ.get("REPORTS_DIR", "reports")
        path = os.path.join(reports_dir, "d1_drift.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(
            "Failed to append stale-cleanup metric to d1_drift.jsonl: %s",
            exc,
        )

    close_db()
    return 4 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
