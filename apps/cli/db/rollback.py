"""Roll back D1 / SQLite writes from an in-progress workflow run.

Used in two contexts:

1. **Automated cleanup-on-failure job** (DailyIngestion / AdHocIngestion):
   passes ``--run-id`` + ``--attempt`` + ``--run-started-at`` so this CLI
   discovers every session belonging to that workflow run and unwinds
   them.  When the workflow's spider step succeeded long enough to print
   ``SPIDER_SESSION_ID``, the workflow also passes ``--session-id`` as a
   redundant pointer; otherwise the ``(RunId, RunAttempt)`` lookup is
   the sole source.

2. **Manual recovery** (RollbackD1.yml workflow_dispatch): pass
   ``--session-id <id>`` to target a specific session.  By default this
   targets *only* that session; pass ``--include-orphaned`` to also pull
   in window-matching ``in_progress`` sessions (legacy behaviour).

Lookup precedence (most specific wins; results are unioned):
* ``--session-id`` (when given)
* ``(RunId, RunAttempt)`` lookup (when ``--run-id`` and ``--attempt``
  are both given)
* ``DateTimeCreated >= --run-started-at`` window scan, **only** when
  ``--include-orphaned`` is set (legacy behaviour kept opt-in to avoid
  the 2026-05-08 incident where a manual ``--session-id`` cleanup
  silently expanded to also clean a sibling session in the same window).

The default mode is dry-run so an operator can inspect what would be
deleted before passing ``--apply``.

Cross-day guard
---------------
If a candidate session's ``DateTimeCreated`` is more than one hour
earlier than the supplied ``--run-started-at``, the CLI refuses it
(exit code 2) — this is the signature of a stale id from a long-ago
run that the caller almost certainly does NOT want to clean up.

Exit codes
----------
* 0 — success (or dry-run finished with no errors)
* 2 — session_id refers to a committed run, or to a session that is
  much older than ``--run-started-at`` (cross-day reject); refused
  without ``--force``
* 3 — could not connect to D1 / SQLite
* 4 — partial failure (one or more sessions left ``Status='failed'``
  with non-zero ``drift_skipped``); operator should investigate the
  drift log and re-run with ``--scope`` if needed

Implementation note
-------------------
The pipeline logic (resolve → plan → apply → emit metrics) lives in
:mod:`javdb.storage.rollback.core`.  This CLI module adds only argparse
and exit-code semantics.
"""

from __future__ import annotations

import argparse
import json
from typing import List, Optional

from javdb.storage.db.db_connection import close_db
from javdb.storage.db.db_migrations import init_db
from javdb.storage.rollback.core import _drive_rollback
from javdb.infra.logging import (
    get_logger,
    setup_logging,
)


logger = get_logger(__name__)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="apps.cli.db.rollback",
        description=(
            "Undo D1 writes from an in-progress workflow run by replaying "
            "audit logs and deleting session-tagged rows. Supports both "
            "auto-cleanup (find by run identity / window) and manual "
            "targeted mode."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typical usage:\n"
            "  # Cleanup-on-failure (no specific session known):\n"
            "  python -m apps.cli.db.rollback \\\n"
            "    --run-id 12345 --attempt 1 \\\n"
            "    --run-started-at 2026-05-04T19:30:00Z\n\n"
            "  # Manual targeted rollback (preferred when you know the id):\n"
            "  python -m apps.cli.db.rollback --session-id 42\n"
            "  python -m apps.cli.db.rollback --session-id 42 --apply\n\n"
            "  # Partial scope:\n"
            "  python -m apps.cli.db.rollback --session-id 42 --scope history\n\n"
            "  # Legacy 'sweep this window' behaviour (opt-in):\n"
            "  python -m apps.cli.db.rollback --session-id 42 \\\n"
            "    --run-started-at 2026-05-04T19:30:00Z --include-orphaned\n"
        ),
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="ReportSessions.Id to roll back. By itself this targets only "
             "that session; combine with --include-orphaned to also pull in "
             "in_progress sessions from the same window (legacy behaviour).",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="GITHUB_RUN_ID of the failed run. Combined with --attempt "
             "this becomes the primary lookup path: every session "
             "(ReportSessions or audit row) tagged with this run is "
             "rolled back.",
    )
    parser.add_argument(
        "--attempt",
        type=int,
        default=None,
        help="GITHUB_RUN_ATTEMPT (used with --run-id for primary lookup). "
             "Must be a valid integer; omit to target every attempt of the "
             "given --run-id.",
    )
    parser.add_argument(
        "--run-started-at",
        type=str,
        default=None,
        help="ISO timestamp of the failed run's start. Used for the cross-"
             "day sanity check on every candidate session and as the lower "
             "bound for the legacy window scan when --include-orphaned is "
             "supplied.",
    )
    parser.add_argument(
        "--scope",
        choices=["reports", "operations", "history", "all"],
        default="all",
        help="Limit cleanup to one logical DB. Default: all.",
    )
    parser.add_argument(
        "--include-orphaned",
        action="store_true",
        default=False,
        help="Additionally include any in_progress sessions whose "
             "DateTimeCreated falls inside the --run-started-at window. "
             "Off by default to avoid expanding a targeted rollback into "
             "a window-wide sweep.",
    )
    parser.add_argument(
        "--failure-reason",
        type=str,
        default=None,
        help="Annotation persisted to ReportSessions.FailureReason. "
             "Defaults to a value derived from $GITHUB_EVENT_NAME / "
             "$ROLLBACK_REASON env vars when running under GitHub Actions.",
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
        "--shard-date",
        type=str,
        default=None,
        help="YYYY-MM-DD (Asia/Singapore) shard date for the MovieClaim "
             "coordinator's rollback_staged_movies call. Defaults to today; "
             "long-running sessions that crossed midnight should pass the "
             "date the spider used at task dispatch time.",
    )
    parser.add_argument(
        "--no-claim-rollback",
        action="store_true",
        default=False,
        help="Skip the MovieClaim coordinator's rollback_staged_movies "
             "call.  The DB-side rollback is unaffected; the staged entries "
             "will be reaped by the StaleSessionCleanup orphan sweep.",
    )
    parser.set_defaults(auto_resume_finalizing=True)
    ar_group = parser.add_mutually_exclusive_group()
    ar_group.add_argument(
        "--auto-resume-finalizing",
        dest="auto_resume_finalizing",
        action="store_true",
        help="For pending-mode sessions in Status='finalizing', "
             "call db_resume_finalizing_session to drive them to "
             "committed (the default; explicit flag exists so the "
             "behaviour is documented in workflow YAML).",
    )
    ar_group.add_argument(
        "--no-auto-resume-finalizing",
        dest="auto_resume_finalizing",
        action="store_false",
        help="Refuse to act on a pending-mode session that is in "
             "Status='finalizing'; surface it as a failed_sessions "
             "entry instead of resuming the commit.",
    )
    parser.add_argument(
        "--claim-rollback-attempts",
        type=int,
        default=3,
        help="How many times to retry rollback_staged_movies on transient "
             "coordinator failures before giving up.  Failures are logged "
             "to d1_drift.jsonl for the orphan sweep to reconcile.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)

    logger.info(
        "Rollback CLI invoked: session_id=%s run_id=%s attempt=%s "
        "run_started_at=%s scope=%s dry_run=%s force=%s "
        "include_orphaned=%s",
        args.session_id, args.run_id, args.attempt,
        args.run_started_at, args.scope, args.dry_run, args.force,
        args.include_orphaned,
    )

    try:
        init_db()
    except Exception as e:
        logger.error("Failed to init DB: %s", e)
        return 3

    summary, exit_code = _drive_rollback(args)
    if summary and not summary.get("_no_targets"):
        printable = {k: v for k, v in summary.items() if not k.startswith("_")}
        print(json.dumps(printable, ensure_ascii=False, indent=2))

    close_db()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
