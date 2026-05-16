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
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from apps.cli._session_helpers import (
    append_jsonl_record,
    attach_run_identity,
    fanout_movie_claim,
    find_run_sessions,
    find_window_sessions,
    normalize_run_started_at,
    read_session_pre_state,
    write_github_output,
)
from packages.python.javdb_platform import db as _db
from packages.python.javdb_platform.db_connection import close_db, get_db
from packages.python.javdb_platform.db_reports import db_pending_session_stats
from packages.python.javdb_platform.db_rollback import db_rollback_session
from packages.python.javdb_platform.db_migrations import init_db
from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
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
        targets.add(args.session_id)

    if args.run_id is not None:
        # ``--attempt`` is now ``type=int`` so argparse rejects malformed
        # input at parse time; ``None`` here means the caller explicitly
        # opted into all-attempts lookup.
        for sid in find_run_sessions(args.run_id, args.attempt):
            targets.add(sid)

    # Legacy / fallback: --run-started-at window scan.
    if args.run_started_at is not None:
        if args.include_orphaned or not targets:
            for sid in find_window_sessions(run_started_at_normalized):
                targets.add(sid)

    return sorted(targets)


def _detect_cross_day(
    session_id: str,
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


def _emit_pending_verify_for_session(
    session_id: str,
    *,
    pre_status: Optional[str],
    pre_write_mode: Optional[str],
    counts: Optional[dict],
    duration_ms: Optional[int],
    error: Optional[str] = None,
    cleanup_path_mismatch: bool = False,
    worker_stage_rollback_failed: int = 0,
) -> None:
    """Emit one ``pending_session_verify`` JSONL line for a rollback-CLI session.

    Phase 2 — every pending-mode session must be observable from the
    email pipeline regardless of whether it landed in ``committed``
    (resume_commit branch) or ``failed`` (rollback_pending branch).
    The rollback CLI is the second emit point alongside
    :mod:`apps.cli.commit_session`; the email helper consumes the union
    of every ``pending_session_verify`` record produced during a run.

    *cleanup_path_mismatch* — set to True when a finalizing session was
    forced into the rollback-pending branch (this is the new Phase 3
    metric exercised by the failure SOP).  Defaults to False.
    """
    if pre_write_mode != "pending":
        return
    counts = counts or {}
    mode = counts.get("mode")
    if mode == "resume_commit":
        final_status = "committed" if not error else "finalizing"
    elif mode == "rollback_pending":
        final_status = "failed" if not error else pre_status or "finalizing"
    elif mode == "audit_replay":
        # Should never happen for write_mode='pending', but stay defensive.
        final_status = pre_status or "failed"
    else:
        final_status = pre_status or "in_progress"

    try:
        stats = db_pending_session_stats(session_id)
    except Exception as exc:  # noqa: BLE001 — emission must stay best-effort
        logger.warning(
            "db_pending_session_stats failed for session_id=%s: %s; "
            "emitting pending_session_verify with default counts.",
            session_id, exc,
        )
        stats = {}
    pending_applied = int(counts.get("pending_marked_applied", 0) or 0)
    pending_staged = (
        pending_applied
        + int(stats.get("pending_residual_count", 0) or 0)
        + int(counts.get("PendingMovieHistoryWrites", 0) or 0)
        + int(counts.get("PendingTorrentHistoryWrites", 0) or 0)
    )
    record = {
        "kind": "pending_session_verify",
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "rollback",
        "session_id": session_id,
        "write_mode": pre_write_mode,
        "final_status": final_status,
        "rollback_mode": mode,
        "pending_staged_count": pending_staged,
        "pending_applied_count": pending_applied,
        "pending_residual_count": int(
            stats.get("pending_residual_count", 0) or 0,
        ),
        # commit_attempts in the rollback CLI: 1 for the original commit
        # attempt that failed + 1 for the resume_commit we're driving now.
        # rollback_pending sessions never went past in_progress, so they
        # log 0 commit attempts.
        "commit_attempts": 2 if mode == "resume_commit" else 0,
        "commit_duration_ms": duration_ms,
        "hrefs_processed": int(counts.get("hrefs_processed", 0) or 0),
        "torrents_upserted": int(counts.get("torrents_upserted", 0) or 0),
        "torrents_deleted": int(counts.get("torrents_deleted", 0) or 0),
        "movies_upserted": int(counts.get("movies_upserted", 0) or 0),
        "worker_stage_rollback_failed": int(worker_stage_rollback_failed),
        "cleanup_path_mismatch_count": 1 if cleanup_path_mismatch else 0,
        "shadow_audit_enabled": False,
        "derived_recompute_drift": 0,
        "derived_drift_samples": [],
    }
    if error is not None:
        record["error"] = error
    attach_run_identity(record, session_id)
    append_jsonl_record(record)


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
    append_jsonl_record(record)

    write_github_output(
        drift_total=record["drift_total"],
        orphan_pruned_total=record["orphan_pruned_total"],
        session_count=len(record["session_ids"]),
    )


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

    run_started_at_normalized = normalize_run_started_at(args.run_started_at)
    failure_reason = _resolve_failure_reason(args)
    if args.run_started_at is not None and run_started_at_normalized is None:
        logger.error(
            "Invalid --run-started-at value %r; refusing rollback so the "
            "fallback window scan cannot expand to every in-progress session.",
            args.run_started_at,
        )
        close_db()
        return 2

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
    pending_verify_records: List[dict] = []
    for sid in sessions:
        # Capture (WriteMode, Status) BEFORE rollback so the verify
        # record sees the pre-state — _rollback_reports might delete
        # the ReportSessions row and obliterate the snapshot.
        pre_state = read_session_pre_state(sid)
        pre_write_mode = pre_state.write_mode
        pre_status = pre_state.status
        rollback_started = time.monotonic()
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
            if pre_write_mode == "pending" and not args.dry_run:
                duration_ms = int((time.monotonic() - rollback_started) * 1000)
                _emit_pending_verify_for_session(
                    sid,
                    pre_status=pre_status,
                    pre_write_mode=pre_write_mode,
                    counts=None,
                    duration_ms=duration_ms,
                    error=str(e),
                )
            continue
        except Exception as e:
            logger.error("Rollback of session %s failed: %s", sid, e)
            failed_sessions.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})
            if pre_write_mode == "pending" and not args.dry_run:
                duration_ms = int((time.monotonic() - rollback_started) * 1000)
                _emit_pending_verify_for_session(
                    sid,
                    pre_status=pre_status,
                    pre_write_mode=pre_write_mode,
                    counts=None,
                    duration_ms=duration_ms,
                    error=str(e),
                )
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
        # Phase 2 verify — emit one pending_session_verify line per
        # pending-mode session the rollback CLI handled.  Skipped on
        # dry-run so we don't pollute the metric stream with hypothetical
        # numbers.
        if pre_write_mode == "pending" and not args.dry_run:
            duration_ms = int((time.monotonic() - rollback_started) * 1000)
            verify_record = {
                "session_id": sid,
                "pre_status": pre_status,
                "rollback_mode": history_counts.get("mode"),
            }
            pending_verify_records.append(verify_record)
            _emit_pending_verify_for_session(
                sid,
                pre_status=pre_status,
                pre_write_mode=pre_write_mode,
                counts=history_counts,
                duration_ms=duration_ms,
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
        claim_rollback_summaries = fanout_movie_claim(
            rollback_targets,
            operation="rollback",
            shard_date=args.shard_date,
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
