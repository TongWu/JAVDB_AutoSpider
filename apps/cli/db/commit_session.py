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
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from apps.cli.db._session_helpers import (
    append_jsonl_record,
    attach_run_identity,
    fanout_movie_claim,
    normalize_run_started_at,
    read_session_pre_state,
    write_github_output,
)
from javdb.storage.db.db_connection import close_db
from javdb.storage.db.db_history_write import db_commit_session_history
from javdb.storage.db.db_reports import (
    db_find_in_progress_sessions,
    db_mark_session_committed,
    db_pending_session_stats,
)
from javdb.storage.db.db_migrations import init_db
from javdb.infra.logging import (
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
        type=str,
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
        "--shard-date",
        type=str,
        default=None,
        help="YYYY-MM-DD (Asia/Singapore) shard date for the MovieClaim "
             "coordinator's commit_completed_movies call. Defaults to today; "
             "long-running sessions that crossed midnight should pass the "
             "date the spider used at task dispatch time.",
    )
    parser.add_argument(
        "--no-claim-commit",
        action="store_true",
        default=False,
        help="Skip the MovieClaim coordinator's commit_completed_movies "
             "call (for tests / one-off CLIs that don't have a coordinator "
             "configured).  The DB-side commit is unaffected.",
    )
    parser.add_argument(
        "--shadow-audit",
        action="store_true",
        default=None,
        help="Force shadow-audit drift comparison ON for pending sessions "
             "(Phase 2 verify path).  Default reads the JAVDB_PENDING_"
             "SHADOW_AUDIT env var (1=on).  Phase 3 keeps this flag-gated "
             "so the comparison can be ramped down once 1 week of clean "
             "drift data is on file.",
    )
    parser.add_argument(
        "--no-shadow-audit",
        action="store_true",
        default=False,
        help="Force shadow-audit drift comparison OFF, overriding the "
             "JAVDB_PENDING_SHADOW_AUDIT env var.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser.parse_args(argv)


def _shadow_audit_enabled(args: argparse.Namespace) -> bool:
    """Resolve whether the Phase 2 shadow-audit comparison should run.

    Precedence: explicit ``--no-shadow-audit`` > explicit ``--shadow-audit``
    > ``JAVDB_PENDING_SHADOW_AUDIT`` env var > default off.

    The shadow comparison is NOT cheap (it walks every Href in the
    session and recomputes the audit-path derived indicators), so the
    default-off posture is intentional.  Phase 2 runs it explicitly
    via the env var on TestIngestion; Phase 3 disables it once the
    drift baseline holds at zero for a full week.
    """
    if getattr(args, "no_shadow_audit", False):
        return False
    if getattr(args, "shadow_audit", None):
        return True
    raw = os.environ.get("JAVDB_PENDING_SHADOW_AUDIT", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _shadow_audit_drift(
    session_id: str,
    drain: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare the live derived indicators against an audit-path replay.

    Returns ``{"derived_recompute_drift": int, "derived_drift_samples":
    [hrefs]}``.  The implementation walks every Href that appears in
    this session's pending tables (or in the drain dict, when the
    pending rows have already been deleted), and for each one re-runs
    the audit-path derived-indicator computation in-process.  Any Href
    whose recomputed (PerfectMatchIndicator, HiResIndicator) differs
    from the value that ``_commit_one_movie`` actually wrote to live
    counts as a drift sample.

    The comparison is best-effort: any exception is downgraded to
    drift=0 with the error captured in ``derived_drift_error`` so the
    metric still emits.
    """
    from javdb.storage.db.db_connection import (
        get_db,
        HISTORY_DB_PATH,
    )

    drift = 0
    samples: List[str] = []
    error: Optional[str] = None
    try:
        with get_db(HISTORY_DB_PATH) as conn:
            try:
                rows = conn.execute(
                    "SELECT mh.Id AS Id, mh.Href AS Href, "
                    "       mh.PerfectMatchIndicator AS pmi, "
                    "       mh.HiResIndicator AS hri "
                    "FROM MovieHistory mh "
                    "WHERE mh.SessionId=?",
                    (session_id,),
                ).fetchall()
            except Exception as exc:  # noqa: BLE001
                return {
                    "derived_recompute_drift": 0,
                    "derived_drift_samples": [],
                    "derived_drift_error": (
                        f"shadow_audit_select_failed: {exc}"
                    ),
                }
            for row in rows:
                movie_id = int(row["Id"])
                want_perfect = bool(
                    conn.execute(
                        "SELECT 1 FROM TorrentHistory t1 "
                        "JOIN TorrentHistory t2 "
                        "  ON t1.MovieHistoryId=t2.MovieHistoryId "
                        "WHERE t1.MovieHistoryId=? "
                        "AND t1.SubtitleIndicator=1 "
                        "AND t1.CensorIndicator=0 "
                        "AND t2.SubtitleIndicator=1 "
                        "AND t2.CensorIndicator=1",
                        (movie_id,),
                    ).fetchone()
                )
                want_hires = bool(
                    conn.execute(
                        "SELECT 1 FROM TorrentHistory "
                        "WHERE MovieHistoryId=? AND ResolutionType >= 2560",
                        (movie_id,),
                    ).fetchone()
                )
                got_perfect = bool(int(row["pmi"] or 0))
                got_hires = bool(int(row["hri"] or 0))
                if want_perfect != got_perfect or want_hires != got_hires:
                    drift += 1
                    if len(samples) < 5:
                        samples.append(row["Href"])
    except Exception as exc:  # noqa: BLE001
        error = f"shadow_audit_failed: {exc}"

    out: Dict[str, Any] = {
        "derived_recompute_drift": drift,
        "derived_drift_samples": samples,
    }
    if error is not None:
        out["derived_drift_error"] = error
    return out


def _emit_pending_verify(
    session_id: str,
    *,
    drain: Optional[Dict[str, Any]],
    final_status: Optional[str],
    write_mode: str,
    commit_attempts: int,
    commit_duration_ms: Optional[int],
    shadow_audit: bool,
) -> Dict[str, Any]:
    """Append a Phase 2 ``pending_session_verify`` line to d1_drift.jsonl.

    The verify line is consumed by:

    * :mod:`packages.python.javdb_integrations.email_notification` — to
      render the "Pending Mode Verification" section and decide if the
      subject prefix needs a ``[PENDING-ALERT]`` / ``[PENDING-ROLLBACK-
      AUTO]`` annotation.
    * :mod:`scripts.aggregate_pending_health` — to fold the most recent
      ``pending_session_verify`` records into a 24h Health Snapshot for
      the email body.

    The function never raises; metric emission MUST NOT block commit.
    Returns the record (for callers that want to print it / add it to
    their JSON summary).
    """
    stats = db_pending_session_stats(session_id)
    drain = drain or {}
    pending_applied_count = int(
        drain.get("pending_marked_applied", 0) or 0
    )
    pending_staged_count = (
        pending_applied_count
        + int(stats.get("pending_residual_count", 0) or 0)
    )
    record: Dict[str, Any] = {
        "kind": "pending_session_verify",
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "commit_session",
        "session_id": session_id,
        "write_mode": write_mode,
        "final_status": final_status,
        "pending_staged_count": pending_staged_count,
        "pending_applied_count": pending_applied_count,
        "pending_residual_count": int(
            stats.get("pending_residual_count", 0) or 0,
        ),
        "commit_attempts": int(commit_attempts),
        "commit_duration_ms": commit_duration_ms,
        "hrefs_processed": int(drain.get("hrefs_processed", 0) or 0),
        "torrents_upserted": int(drain.get("torrents_upserted", 0) or 0),
        "torrents_deleted": int(drain.get("torrents_deleted", 0) or 0),
        "movies_upserted": int(drain.get("movies_upserted", 0) or 0),
        "worker_stage_rollback_failed": 0,
        # Phase 2: shadow audit; Phase 3: gated on JAVDB_PENDING_SHADOW_AUDIT.
        "shadow_audit_enabled": bool(shadow_audit),
    }
    attach_run_identity(record, session_id)
    if shadow_audit and final_status == "committed":
        record.update(_shadow_audit_drift(session_id, drain))
    else:
        record["derived_recompute_drift"] = 0
        record["derived_drift_samples"] = []

    append_jsonl_record(record)
    write_github_output(
        pending_residual_count=record["pending_residual_count"],
        pending_applied_count=record["pending_applied_count"],
        commit_attempts=record["commit_attempts"],
    )
    return record


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
        targets.add(args.session_id)

    since = normalize_run_started_at(args.run_started_at)
    if since:
        # commit_session has stricter window-scan semantics than the
        # generic helper in _session_helpers: a DB hiccup with no
        # explicit ``--session-id`` is a hard error (exit 1) so an
        # operator notices, but with an explicit id we still try to
        # commit it. Call db_find_in_progress_sessions directly so the
        # helper's "swallow-and-warn" default doesn't downgrade the
        # no-explicit-id error path.
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
            targets.add(sid)

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
    pending_drains: List[dict] = []
    for sid in sorted(targets):
        # Ingestion Perfect Rollback (Phase 2): pending-mode sessions
        # need their staged rows promoted into the live MovieHistory /
        # TorrentHistory tables BEFORE the Status flip; otherwise the
        # downstream rollback CLI sees a 'committed' row with leftover
        # PendingMovie/TorrentHistoryWrites and the live tables miss
        # all of this session's writes.  ``db_commit_session_history``
        # itself walks in_progress → finalizing → committed and is
        # idempotent, so calling it on an already-committed pending
        # session is a no-op.  Audit-mode sessions skip the call so
        # the legacy upsert + audit log path is unaffected.
        pre = read_session_pre_state(sid)
        write_mode = pre.write_mode
        sess_status = pre.status
        drain: Optional[Dict[str, Any]] = None
        commit_started_at: Optional[float] = None
        commit_duration_ms: Optional[int] = None
        if write_mode == 'pending' and sess_status != 'committed':
            try:
                commit_started_at = time.monotonic()
                drain = db_commit_session_history(sid)
                commit_duration_ms = int(
                    (time.monotonic() - commit_started_at) * 1000,
                )
                drain['session_id'] = sid
                pending_drains.append(drain)
                logger.info(
                    "Pending session committed: id=%s mode=%s drain=%s",
                    sid, write_mode, drain,
                )
            except Exception as e:
                logger.error(
                    "db_commit_session_history failed for pending session "
                    "%s: %s", sid, e,
                )
                failed_commits.append(sid)
                # Phase 2 verify: emit a verify line on failure too so the
                # email pipeline knows commit attempted but did not finish.
                if write_mode == 'pending':
                    _emit_pending_verify(
                        sid,
                        drain=None,
                        final_status='finalizing',
                        write_mode=write_mode,
                        commit_attempts=1,
                        commit_duration_ms=(
                            int((time.monotonic() - commit_started_at) * 1000)
                            if commit_started_at else None
                        ),
                        shadow_audit=_shadow_audit_enabled(args),
                    )
                continue
        try:
            n = db_mark_session_committed(sid)
        except Exception as e:
            logger.error("Failed to commit session %s: %s", sid, e)
            failed_commits.append(sid)
            if write_mode == 'pending':
                _emit_pending_verify(
                    sid,
                    drain=drain,
                    final_status='finalizing',
                    write_mode=write_mode,
                    commit_attempts=1,
                    commit_duration_ms=commit_duration_ms,
                    shadow_audit=_shadow_audit_enabled(args),
                )
            continue
        if n > 0:
            committed.append(sid)
        else:
            # Already committed — that's fine, idempotent.
            skipped.append(sid)
        if write_mode == 'pending':
            # Phase 2 verify: emit one line per pending session whose
            # commit actually went through (committed or no-op idempotent).
            _emit_pending_verify(
                sid,
                drain=drain,
                final_status='committed',
                write_mode=write_mode,
                commit_attempts=1,
                commit_duration_ms=commit_duration_ms,
                shadow_audit=_shadow_audit_enabled(args),
            )

    # Phase-1 — promote each session's MovieClaim stages to committed
    # AFTER the DB row flips.  Run for the union of (committed,
    # skipped == already_committed) so a re-invocation of this CLI on
    # a partially-committed run still tidies up the coordinator side.
    claim_commit_summaries: List[dict] = []
    if not args.no_claim_commit:
        claim_commit_summaries = fanout_movie_claim(
            sorted(set(committed) | set(skipped)),
            operation="commit",
            shard_date=args.shard_date,
        )

    summary = {
        "run_started_at": args.run_started_at,
        "session_id": args.session_id,
        "committed": committed,
        "already_committed_or_missing": skipped,
        "failed_commits": failed_commits,
        "movie_claim_commits": claim_commit_summaries,
        "pending_session_drains": pending_drains,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    logger.info(
        "Commit done: committed=%d already_committed_or_missing=%d failed=%d "
        "claim_commits=%d",
        len(committed), len(skipped), len(failed_commits),
        len(claim_commit_summaries),
    )

    close_db()
    if failed_commits:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
