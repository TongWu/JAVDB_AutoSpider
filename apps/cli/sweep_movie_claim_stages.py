"""Cron-friendly CLI for the MovieClaim ``sweep_orphan_stages`` route.

Phase-1 rollback safety relies on every staged completion being either
promoted (``apps.cli.commit_session``) or dropped
(``apps.cli.rollback``) by a session-end CLI.  Runners that crash
between the stage call and the session-end CLI leave behind
"orphan" stages that would otherwise block adhoc retries on the same
href forever.

This CLI calls ``GET /sweep_orphan_stages`` for one or more per-day
shards so the StaleSessionCleanup workflow can reap those orphans on
its existing 02:00 UTC cron — the Worker server-floors the
``older_than_ms`` window at 1h so a buggy operator can't accidentally
wipe live stages.

Exit codes
----------
* 0 — at least one sweep ran successfully (or the coordinator is not
  configured, which is a no-op success).
* 1 — every sweep failed (coordinator reachable but every per-shard
  call raised); operator should investigate before relying on the
  result.

Best-effort by design: a single shard failure does not abort the
remaining shards.  All failures are recorded to
``reports/D1/d1_drift.jsonl`` so the commit-results audit trail keeps
the same shape as :mod:`apps.cli.rollback`.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
)
from packages.python.javdb_platform.movie_claim_client import (
    MovieClaimUnavailable,
    create_movie_claim_client_from_env,
)


logger = get_logger(__name__)


# MR-5 (multi-runtime, 2026-05-12): 6 h, down from the original 48 h.
#
# The Worker caps a single MovieClaim TTL at ``MOVIE_CLAIM_TTL_MAX_MS``
# (2 h), so any legitimate in-flight session has refreshed its stage's
# ``ts`` heartbeat within the last 2 h. A stage whose ``ts`` is older
# than 6 h therefore belongs to a runner that crashed between
# ``stage_complete`` and the session-end commit / rollback — there is no
# live session it could still belong to. Reaping at 6 h instead of 48 h
# shrinks the window during which a crashed peer's orphan stage makes
# other sessions' ``stage_complete`` calls bounce (``staged=false``) and
# causes the shard to re-fetch that href on a later run.
#
# Still comfortably above the Worker's server-side floor
# (``MIN_SWEEP_ORPHAN_MS`` = 1 h) so a buggy ``older_than_ms`` can never
# wipe a live stage. The Worker's own ``DEFAULT_SWEEP_ORPHAN_MS`` (48 h)
# is unchanged — it is only the fallback when no ``older_than_ms`` query
# arg is sent, and this CLI always sends one explicitly.
_DEFAULT_OLDER_THAN_MS = 6 * 60 * 60 * 1000


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="apps.cli.sweep_movie_claim_stages",
        description=(
            "Reap orphaned MovieClaim staged completions across one or more "
            "per-day shards.  Designed for the StaleSessionCleanup cron; "
            "safe to run on demand."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--shard-date",
        action="append",
        default=None,
        help="Per-day shard date in YYYY-MM-DD (Asia/Singapore).  Repeat "
             "to walk multiple days; defaults to {today, yesterday, "
             "the day before yesterday}.",
    )
    parser.add_argument(
        "--older-than-hours",
        type=float,
        default=None,
        help="Sweep horizon in hours.  Default: 6 (MR-5 — was 48).  "
             "Server-floored at 1 h.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser.parse_args(argv)


def _default_shard_dates() -> List[str]:
    """Return the most recent three shard dates (today + 2 prior).

    Three days is enough to catch a stage from a session that crossed
    two midnights (rare but possible for an adhoc rerun started near
    23:59), without inflating the StaleSessionCleanup cron's runtime.
    The operational timezone (Asia/Singapore = UTC+08:00) matches
    :func:`movie_claim_client.current_shard_date`.
    """
    ops_tz = timezone(timedelta(hours=8))
    base = datetime.now(ops_tz)
    return [
        (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (0, 1, 2)
    ]


def _emit_metric(record: dict) -> None:
    """Append a sweep audit record to ``reports/D1/d1_drift.jsonl``."""
    try:
        reports_dir = os.environ.get("REPORTS_DIR", "reports")
        path = os.path.join(reports_dir, "D1", "d1_drift.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - audit must never break the cron
        logger.warning(
            "Failed to append movie-claim sweep metric to d1_drift.jsonl: %s",
            exc,
        )


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)

    shard_dates = args.shard_date or _default_shard_dates()
    older_than_ms = (
        int(args.older_than_hours * 3600 * 1000)
        if args.older_than_hours is not None
        else _DEFAULT_OLDER_THAN_MS
    )

    logger.info(
        "Movie-claim sweep CLI invoked: shards=%s older_than_ms=%s",
        shard_dates, older_than_ms,
    )

    client = create_movie_claim_client_from_env()
    if client is None:
        logger.info(
            "MovieClaim coordinator not configured — nothing to sweep "
            "(this is a no-op success path for clusters running with "
            "MOVIE_CLAIM_ENABLED=off)",
        )
        return 0

    successes: List[dict] = []
    failures: List[dict] = []
    try:
        for shard in shard_dates:
            try:
                result = client.sweep_orphan_stages(
                    older_than_ms=older_than_ms,
                    date=shard,
                )
                logger.info(
                    "Movie-claim sweep: shard=%s removed=%s cutoff_ms=%s",
                    shard, result.removed, result.cutoff_ms,
                )
                successes.append({
                    "shard_date": shard,
                    "removed": result.removed,
                    "cutoff_ms": result.cutoff_ms,
                })
            except MovieClaimUnavailable as exc:
                logger.warning(
                    "Movie-claim sweep unavailable for shard=%s: %s",
                    shard, exc,
                )
                failures.append({
                    "shard_date": shard,
                    "error": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Unexpected movie-claim sweep error for shard=%s",
                    shard, exc_info=True,
                )
                failures.append({
                    "shard_date": shard,
                    "error": str(exc),
                })
    finally:
        client.close()

    summary = {
        "shards": shard_dates,
        "older_than_ms": older_than_ms,
        "successes": successes,
        "failures": failures,
        "total_removed": sum(s.get("removed", 0) for s in successes),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    _emit_metric({
        "kind": "movie_claim_sweep_summary",
        "ts": datetime.now(timezone.utc).isoformat(),
        **summary,
    })

    if failures and not successes:
        logger.error(
            "Movie-claim sweep: every shard failed (%d failures)",
            len(failures),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
