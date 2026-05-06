"""Roll back D1 / SQLite writes from an in-progress workflow run.

Used in two contexts:

1. **Automated cleanup-on-failure job** (DailyIngestion / AdHocIngestion):
   passes ``--run-id`` + ``--attempt`` + ``--run-started-at`` so this CLI
   discovers every ``ReportSessions.Status='in_progress'`` row created
   inside the failed run's window and unwinds them.

2. **Manual recovery** (RollbackD1.yml workflow_dispatch): pass
   ``--session-id <id>`` to target a specific session, optionally
   combined with ``--scope`` to restrict the cleanup to one logical DB.

The default mode is dry-run so an operator can inspect what would be
deleted before passing ``--apply``.

Exit codes
----------
* 0 — success (or dry-run finished with no errors)
* 2 — session_id refers to a committed run; refused without ``--force``
* 3 — could not connect to D1 / SQLite
* 4 — partial failure (one or more sessions left ``Status='failed'``
  with non-zero ``drift_skipped``); operator should investigate the
  drift log and re-run with ``--scope`` if needed
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List, Optional

from packages.python.javdb_platform.db import (
    close_db,
    db_find_in_progress_sessions,
    db_rollback_session,
    init_db,
)
from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
)


logger = get_logger(__name__)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="apps.cli.rollback",
        description=(
            "Undo D1 writes from an in-progress workflow run by replaying "
            "audit logs and deleting session-tagged rows. Supports both "
            "auto-cleanup (find by run window) and manual targeted mode."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typical usage:\n"
            "  # Cleanup-on-failure (no specific session known):\n"
            "  python -m apps.cli.rollback \\\n"
            "    --run-id 12345 --attempt 1 --run-started-at 2026-05-04T19:30:00Z\n\n"
            "  # Manual targeted rollback (preferred when you know the id):\n"
            "  python -m apps.cli.rollback --session-id 42\n"
            "  python -m apps.cli.rollback --session-id 42 --apply\n\n"
            "  # Partial scope:\n"
            "  python -m apps.cli.rollback --session-id 42 --scope history\n"
        ),
    )
    parser.add_argument(
        "--session-id",
        type=int,
        default=None,
        help="ReportSessions.Id to roll back. By itself this targets only "
             "that session; when --run-started-at is also set, this id is "
             "unioned with the in-progress session lookup.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="GITHUB_RUN_ID of the failed run (for audit trail / logging).",
    )
    parser.add_argument(
        "--attempt",
        type=str,
        default=None,
        help="GITHUB_RUN_ATTEMPT (for audit trail / logging).",
    )
    parser.add_argument(
        "--run-started-at",
        type=str,
        default=None,
        help="ISO timestamp of the failed run's start. Used as the lower "
             "bound when discovering in-progress sessions to clean up.",
    )
    parser.add_argument(
        "--scope",
        choices=["reports", "operations", "history", "all"],
        default="all",
        help="Limit cleanup to one logical DB. Default: all.",
    )
    parser.set_defaults(dry_run=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would be deleted but do not modify any DB (default).",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually perform the rollback; omit to run in dry-run mode.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow rolling back a session whose Status='committed'. "
             "Off by default to prevent accidental data loss.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser.parse_args(argv)


def _normalize_run_started_at(raw: Optional[str]) -> Optional[str]:
    """Convert an ISO timestamp into the SQLite-friendly UTC form.

    GitHub passes timestamps like ``2026-05-04T19:30:00Z``; ``ReportSessions
    .DateTimeCreated`` stores UTC as ``2026-05-04 19:30:00``. Offset-aware
    inputs are normalized to naive UTC before lexicographic comparison.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _resolve_target_sessions(args: argparse.Namespace) -> List[int]:
    targets = set()
    if args.session_id is not None:
        targets.add(int(args.session_id))

    if args.run_started_at is not None or args.session_id is None:
        since = _normalize_run_started_at(args.run_started_at)
        sessions = db_find_in_progress_sessions(since=since)
        for sid in sessions:
            targets.add(int(sid))
    return sorted(targets)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)

    logger.info(
        "Rollback CLI invoked: session_id=%s run_id=%s attempt=%s "
        "run_started_at=%s scope=%s dry_run=%s force=%s",
        args.session_id, args.run_id, args.attempt,
        args.run_started_at, args.scope, args.dry_run, args.force,
    )

    try:
        init_db()
    except Exception as e:
        logger.error("Failed to init DB: %s", e)
        return 3

    try:
        sessions = _resolve_target_sessions(args)
    except Exception as e:
        logger.error("Failed to resolve target sessions: %s", e)
        close_db()
        return 3

    if not sessions:
        logger.info(
            "No in-progress ReportSessions found in the lookup window — "
            "nothing to roll back. (This is the normal outcome when a "
            "run failed before the spider could create its session.)"
        )
        close_db()
        return 0

    logger.info(
        "Targeting %d session(s) for %s rollback: %s",
        len(sessions),
        "dry-run" if args.dry_run else "real",
        sessions,
    )

    drift_total = 0
    failed_sessions: List[int] = []
    refused_sessions: List[int] = []
    summaries: List[dict] = []
    for sid in sessions:
        try:
            counts = db_rollback_session(
                sid,
                dry_run=args.dry_run,
                scope=args.scope,
                force=args.force,
            )
        except ValueError as e:
            logger.error("Refused to roll back session %s: %s", sid, e)
            failed_sessions.append(sid)
            refused_sessions.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})
            continue
        except Exception as e:
            logger.error("Rollback of session %s failed: %s", sid, e)
            failed_sessions.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})
            continue
        summaries.append({"session_id": sid, "counts": counts})
        history_counts = counts.get("history", {})
        drift = history_counts.get("drift_skipped", 0)
        drift_total += int(drift or 0)
        logger.info(
            "Session %s rollback %s: %s",
            sid,
            "(dry-run)" if args.dry_run else "applied",
            json.dumps(counts, ensure_ascii=False),
        )

    summary = {
        "dry_run": args.dry_run,
        "scope": args.scope,
        "run_id": args.run_id,
        "attempt": args.attempt,
        "run_started_at": args.run_started_at,
        "sessions": summaries,
        "drift_total": drift_total,
        "failed_sessions": failed_sessions,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    close_db()

    if failed_sessions:
        logger.error(
            "Rollback partially failed or was refused for sessions %s — "
            "check the per-session summary for details.",
            failed_sessions,
        )
        if len(refused_sessions) == len(failed_sessions):
            return 2
        return 4
    if drift_total > 0 and not args.dry_run:
        logger.warning(
            "Rollback completed but %d audit row(s) skipped due to "
            "concurrent-run drift; check the logs above and consider "
            "re-running with --scope history once the conflicting run "
            "settles.",
            drift_total,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
