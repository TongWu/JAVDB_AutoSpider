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
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import time

from packages.python.javdb_platform import db as _db
from packages.python.javdb_platform.db import (
    close_db,
    db_find_in_progress_sessions,
    db_find_sessions_by_run,
    db_rollback_session,
    init_db,
    get_db,
)
from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
)
from packages.python.javdb_platform.movie_claim_client import (
    MovieClaimUnavailable,
    create_movie_claim_client_from_env,
    current_shard_date,
)


logger = get_logger(__name__)

# Cross-day reject window. A candidate session whose DateTimeCreated is
# more than this far before --run-started-at is treated as stale and
# refused.  One hour gives generous headroom for clock skew while still
# catching the obvious "want to clean today's run, accidentally pointed
# at a session from 36h ago" mistake.
_CROSS_DAY_REJECT_HOURS = 1


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="apps.cli.rollback",
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
            "  python -m apps.cli.rollback \\\n"
            "    --run-id 12345 --attempt 1 \\\n"
            "    --run-started-at 2026-05-04T19:30:00Z\n\n"
            "  # Manual targeted rollback (preferred when you know the id):\n"
            "  python -m apps.cli.rollback --session-id 42\n"
            "  python -m apps.cli.rollback --session-id 42 --apply\n\n"
            "  # Partial scope:\n"
            "  python -m apps.cli.rollback --session-id 42 --scope history\n\n"
            "  # Legacy 'sweep this window' behaviour (opt-in):\n"
            "  python -m apps.cli.rollback --session-id 42 \\\n"
            "    --run-started-at 2026-05-04T19:30:00Z --include-orphaned\n"
        ),
    )
    parser.add_argument(
        "--session-id",
        type=int,
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
        type=str,
        default=None,
        help="GITHUB_RUN_ATTEMPT (used with --run-id for primary lookup).",
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
    # Ingestion Perfect Rollback (Phase 2/3): pending-mode sessions
    # caught in Status='finalizing' are driven to committed via
    # db_resume_finalizing_session by default; pass
    # ``--no-auto-resume-finalizing`` to refuse and surface them as
    # failed_sessions instead.
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


def _rollback_movie_claim_stages(
    session_ids: List[int],
    shard_date: Optional[str],
    *,
    max_attempts: int,
) -> List[dict]:
    """Drop staged completions for *session_ids* on the coordinator.

    Best-effort with bounded retries: a coordinator outage MUST NOT
    block the DB-side rollback.  Each session is retried up to
    *max_attempts* times with exponential backoff (1s, 2s, 4s, ...);
    persistent failures are recorded so the StaleSessionCleanup orphan
    sweep can reconcile the leftover stages.

    Returns one summary record per session for the CLI's JSON output;
    an empty list when no client is configured.
    """
    if not session_ids:
        return []
    client = create_movie_claim_client_from_env()
    if client is None:
        logger.info(
            "MovieClaim coordinator not configured — skipping "
            "rollback_staged_movies (DB-side rollback unaffected)",
        )
        return []
    attempts = max(1, int(max_attempts))
    target_date = shard_date or current_shard_date()
    summaries: List[dict] = []
    try:
        for sid in session_ids:
            removed: Optional[int] = None
            last_error: Optional[str] = None
            for attempt in range(1, attempts + 1):
                try:
                    result = client.rollback_staged_movies(
                        str(sid), date=target_date,
                    )
                    removed = result.removed
                    last_error = None
                    break
                except MovieClaimUnavailable as exc:
                    last_error = str(exc)
                    logger.warning(
                        "MovieClaim rollback attempt %d/%d failed for "
                        "session=%s shard=%s: %s",
                        attempt, attempts, sid, target_date, exc,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logger.warning(
                        "Unexpected MovieClaim rollback error attempt %d/%d "
                        "for session=%s shard=%s",
                        attempt, attempts, sid, target_date, exc_info=True,
                    )
                if attempt < attempts:
                    time.sleep(2 ** (attempt - 1))
            if removed is None:
                logger.error(
                    "MovieClaim rollback gave up for session=%s shard=%s "
                    "after %d attempts — orphan sweep will reconcile",
                    sid, target_date, attempts,
                )
                summaries.append({
                    "session_id": sid,
                    "shard_date": target_date,
                    "removed": 0,
                    "ok": False,
                    "error": last_error or "unknown",
                    "attempts": attempts,
                })
            else:
                logger.info(
                    "MovieClaim rollback: session=%s shard=%s removed=%s",
                    sid, target_date, removed,
                )
                summaries.append({
                    "session_id": sid,
                    "shard_date": target_date,
                    "removed": removed,
                    "ok": True,
                })
    finally:
        client.close()
    return summaries


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


def _resolve_target_sessions(
    args: argparse.Namespace,
    run_started_at_normalized: Optional[str],
) -> List[int]:
    """Build the set of session ids to act on, honouring scope flags.

    Order of precedence (all sources are unioned, with later sources
    only consulted if the earlier ones leave the set empty unless
    explicitly opted-in via --include-orphaned):

    1. ``--session-id`` (when supplied)
    2. ``(--run-id, --attempt)`` lookup against ReportSessions+audit
    3. ``--run-started-at`` window scan over in_progress sessions
       (only when ``--include-orphaned`` is set OR when no other source
       has yielded any id — the auto-cleanup workflow needs this so a
       run that died before printing its session id can still be cleaned
       by date window).
    """
    targets: set = set()
    if args.session_id is not None:
        targets.add(int(args.session_id))

    if args.run_id is not None:
        attempt: Optional[int]
        if args.attempt is not None:
            try:
                attempt = int(args.attempt)
            except ValueError:
                attempt = None
        else:
            attempt = None
        try:
            run_sessions = db_find_sessions_by_run(args.run_id, attempt)
        except Exception as e:
            logger.warning(
                "db_find_sessions_by_run(run_id=%s attempt=%s) failed: %s",
                args.run_id, attempt, e,
            )
            run_sessions = []
        for sid in run_sessions:
            targets.add(int(sid))

    # Legacy / fallback: --run-started-at window scan.
    if args.run_started_at is not None:
        if args.include_orphaned or not targets:
            sessions = db_find_in_progress_sessions(
                since=run_started_at_normalized,
            )
            for sid in sessions:
                targets.add(int(sid))

    return sorted(targets)


def _detect_cross_day(
    session_id: int,
    run_started_at_normalized: Optional[str],
) -> bool:
    """True if session's DateTimeCreated is far older than run start.

    Stale-id mistakes (the 2026-05-08 incident) are characterised by a
    candidate session that was actually created more than an hour
    before the failed run began.  Reject those before any DELETE.
    """
    if not run_started_at_normalized:
        return False
    try:
        run_started_dt = datetime.strptime(
            run_started_at_normalized, "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return False
    cutoff = run_started_dt - timedelta(hours=_CROSS_DAY_REJECT_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    # Resolve REPORTS_DB_PATH dynamically so conftest fixtures that
    # mutate ``_db.REPORTS_DB_PATH`` are honoured by this lookup too.
    with get_db(_db.REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT DateTimeCreated FROM ReportSessions WHERE Id=?",
            (session_id,),
        ).fetchone()
    if not row:
        return False
    created = row["DateTimeCreated"]
    if not created:
        return False
    return created < cutoff_str


def _resolve_failure_reason(args: argparse.Namespace) -> Optional[str]:
    """Pick a FailureReason annotation from CLI / env."""
    if args.failure_reason:
        return args.failure_reason
    explicit = os.environ.get("ROLLBACK_REASON")
    if explicit:
        return explicit
    # Best-effort heuristic: GitHub workflow_run cancelled vs failed.
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "workflow_dispatch":
        return "manual_dispatch"
    if event:
        return f"workflow_{event}"
    return None


def _emit_metrics(summary: dict) -> None:
    """Append rollback metrics to reports/D1/d1_drift.jsonl + GITHUB_OUTPUT."""
    claim_rollbacks = summary.get("movie_claim_rollbacks", []) or []
    claim_failed = [
        c for c in claim_rollbacks if not c.get("ok", False)
    ]
    record = {
        "kind": "rollback_summary",
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": summary.get("dry_run"),
        "scope": summary.get("scope"),
        "run_id": summary.get("run_id"),
        "attempt": summary.get("attempt"),
        "drift_total": summary.get("drift_total", 0),
        "orphan_pruned_total": summary.get("orphan_pruned_total", 0),
        "refused_sessions": summary.get("refused_sessions", []),
        "failed_sessions": summary.get("failed_sessions", []),
        "session_ids": [
            s.get("session_id") for s in summary.get("sessions", [])
            if s.get("session_id") is not None
        ],
        # Phase-1 — record any MovieClaim rollbacks that failed so the
        # StaleSessionCleanup orphan sweep can reconcile.  Empty list when
        # everything succeeded (or the coordinator was not configured).
        "movie_claim_rollback_failures": claim_failed,
    }
    try:
        reports_dir = os.environ.get("REPORTS_DIR", "reports")
        path = os.path.join(reports_dir, "D1", "d1_drift.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - metric emission must never fail rollback
        logger.warning("Failed to append rollback metric to d1_drift.jsonl: %s", exc)

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        try:
            with open(gh_output, "a", encoding="utf-8") as f:
                f.write(f"drift_total={record['drift_total']}\n")
                f.write(f"orphan_pruned_total={record['orphan_pruned_total']}\n")
                f.write(
                    "session_count={}\n".format(
                        len(record["session_ids"])
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write GITHUB_OUTPUT metrics: %s", exc)


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

    run_started_at_normalized = _normalize_run_started_at(args.run_started_at)
    failure_reason = _resolve_failure_reason(args)

    try:
        sessions = _resolve_target_sessions(args, run_started_at_normalized)
    except Exception as e:
        logger.error("Failed to resolve target sessions: %s", e)
        close_db()
        return 3

    if not sessions:
        logger.info(
            "No target sessions found — nothing to roll back. (This is the "
            "normal outcome when a run failed before the spider could "
            "create its session.)"
        )
        close_db()
        return 0

    # Cross-day sanity filter: refuse sessions older than the run window.
    refused_sessions: List[int] = []
    if run_started_at_normalized and not args.force:
        valid_targets: List[int] = []
        for sid in sessions:
            if _detect_cross_day(sid, run_started_at_normalized):
                logger.error(
                    "Refusing session %s: DateTimeCreated predates "
                    "--run-started-at (%s) by more than %dh — looks like "
                    "a stale id from an unrelated run. Pass --force to "
                    "override.",
                    sid, run_started_at_normalized,
                    _CROSS_DAY_REJECT_HOURS,
                )
                refused_sessions.append(sid)
            else:
                valid_targets.append(sid)
        sessions = valid_targets

    if not sessions:
        summary = {
            "dry_run": args.dry_run,
            "scope": args.scope,
            "run_id": args.run_id,
            "attempt": args.attempt,
            "run_started_at": args.run_started_at,
            "sessions": [],
            "drift_total": 0,
            "orphan_pruned_total": 0,
            "failed_sessions": [],
            "refused_sessions": refused_sessions,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        _emit_metrics(summary)
        close_db()
        return 2

    logger.info(
        "Targeting %d session(s) for %s rollback: %s%s",
        len(sessions),
        "dry-run" if args.dry_run else "real",
        sessions,
        f" (refused {len(refused_sessions)} as stale)" if refused_sessions else "",
    )

    drift_total = 0
    orphan_pruned_total = 0
    failed_sessions: List[int] = []
    refused_committed_sessions: List[int] = []
    summaries: List[dict] = []
    for sid in sessions:
        try:
            counts = db_rollback_session(
                sid,
                dry_run=args.dry_run,
                scope=args.scope,
                force=args.force,
                run_started_at=run_started_at_normalized,
                failure_reason=failure_reason,
                auto_resume_finalizing=args.auto_resume_finalizing,
            )
        except ValueError as e:
            logger.error("Refused to roll back session %s: %s", sid, e)
            failed_sessions.append(sid)
            refused_committed_sessions.append(sid)
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
        orphan = history_counts.get("orphan_pruned", 0)
        drift_total += int(drift or 0)
        orphan_pruned_total += int(orphan or 0)
        logger.info(
            "Session %s rollback %s: %s",
            sid,
            "(dry-run)" if args.dry_run else "applied",
            json.dumps(counts, ensure_ascii=False),
        )

    # Phase-1 — drop staged MovieClaim entries for every session we
    # actually rolled back (i.e. exclude refused/failed sessions where
    # the DB-side state was preserved).  Skipped during --dry-run to
    # match the DB-side semantics; --no-claim-rollback also skips for
    # tests / one-off CLIs.
    claim_rollback_summaries: List[dict] = []
    if not args.dry_run and not args.no_claim_rollback:
        rollback_targets = [
            sid for sid in sessions
            if sid not in failed_sessions
        ]
        claim_rollback_summaries = _rollback_movie_claim_stages(
            rollback_targets,
            args.shard_date,
            max_attempts=args.claim_rollback_attempts,
        )

    summary = {
        "dry_run": args.dry_run,
        "scope": args.scope,
        "run_id": args.run_id,
        "attempt": args.attempt,
        "run_started_at": args.run_started_at,
        "sessions": summaries,
        "drift_total": drift_total,
        "orphan_pruned_total": orphan_pruned_total,
        "failed_sessions": failed_sessions,
        "refused_sessions": refused_sessions,
        "movie_claim_rollbacks": claim_rollback_summaries,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    _emit_metrics(summary)
    close_db()

    if failed_sessions:
        logger.error(
            "Rollback partially failed or was refused for sessions %s — "
            "check the per-session summary for details.",
            failed_sessions,
        )
        if (
            len(refused_committed_sessions) == len(failed_sessions)
            and not refused_sessions
        ):
            return 2
        return 4
    if refused_sessions:
        # Cross-day rejects are themselves a refusal even when no other
        # session failed — surface as exit 2 so the workflow notices.
        return 2
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
