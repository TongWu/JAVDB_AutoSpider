"""Mark in-progress ReportSessions rows as ``committed`` after a successful run.

Called by the ``mark-sessions-as-committed`` step at the end of
``run-pipeline`` once Spider + Uploader + PikPak Bridge all succeed. A
session in ``Status='committed'`` is permanently shielded from any
future ``apps.cli.rollback`` cleanup pass — even the manual workflow
will refuse to touch it without the explicit ``--force`` flag.

Two lookup modes:

* ``--session-id <id>``: highest priority; flips that one row.
* ``--run-started-at <ISO>`` (the workflow's setup-job output): flips
  every ``Status='in_progress'`` session created on or after that
  timestamp. This catches the (rare) case where the spider finishes a
  Phase but the surrounding job records the session id in a way that
  isn't visible to this step (e.g. Phase 2 creates additional sessions).

Both flags are accepted simultaneously; the union of the two sets is
committed.

Exit codes
----------
* 0 — at least one row updated, or no in_progress rows in the window
  (success).
* 1 — DB connection failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Set

from packages.python.javdb_platform.db import (
    close_db,
    db_find_in_progress_sessions,
    db_mark_session_committed,
    init_db,
)
from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
)


logger = get_logger(__name__)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="apps.cli.commit_session",
        description=(
            "Flip Status='in_progress' → 'committed' for the given session "
            "and/or every in-progress session created on or after "
            "--run-started-at. Idempotent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--session-id",
        type=int,
        default=None,
        help="Specific ReportSessions.Id to commit (e.g. the spider's "
             "primary session). Optional.",
    )
    parser.add_argument(
        "--run-started-at",
        type=str,
        default=None,
        help="ISO timestamp of the workflow run start. Used to commit "
             "every in_progress session created on or after this point — "
             "covers all sessions belonging to this run.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser.parse_args(argv)


def _normalize_run_started_at(raw: Optional[str]) -> Optional[str]:
    """Same conversion as ``apps.cli.rollback`` — keep the two CLIs aligned."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1]
    if "+" in s and "T" in s:
        s = s.split("+", 1)[0]
    s = s.replace("T", " ")
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)

    logger.info(
        "Commit-session CLI invoked: session_id=%s run_started_at=%s",
        args.session_id, args.run_started_at,
    )

    try:
        init_db()
    except Exception as e:
        logger.error("Failed to init DB: %s", e)
        return 1

    targets: Set[int] = set()
    if args.session_id is not None:
        targets.add(int(args.session_id))

    since = _normalize_run_started_at(args.run_started_at)
    if since:
        try:
            window_sessions = db_find_in_progress_sessions(since=since)
        except Exception as e:
            if args.session_id is not None:
                logger.warning(
                    "Failed to look up in-progress sessions since %s: %s; "
                    "continuing with explicit session_id=%s",
                    since, e, args.session_id,
                )
                window_sessions = []
            else:
                logger.error(
                    "Failed to look up in-progress sessions since %s: %s",
                    since, e,
                )
                close_db()
                return 1
        for sid in window_sessions:
            targets.add(int(sid))

    if not targets:
        logger.info(
            "No in-progress sessions to commit (none provided and none "
            "found in window since %s)", since,
        )
        close_db()
        return 0

    committed: List[int] = []
    skipped: List[int] = []
    failed_commits: List[int] = []
    for sid in sorted(targets):
        try:
            n = db_mark_session_committed(sid)
        except Exception as e:
            logger.error("Failed to commit session %s: %s", sid, e)
            failed_commits.append(sid)
            continue
        if n > 0:
            committed.append(sid)
        else:
            # Already committed — that's fine, idempotent.
            skipped.append(sid)

    summary = {
        "run_started_at": args.run_started_at,
        "session_id": args.session_id,
        "committed": committed,
        "already_committed_or_missing": skipped,
        "failed_commits": failed_commits,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    logger.info(
        "Commit done: committed=%d already_committed_or_missing=%d failed=%d",
        len(committed), len(skipped), len(failed_commits),
    )

    close_db()
    if failed_commits:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
