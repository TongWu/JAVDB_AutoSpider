"""Commit library — force a session to committed state.

Public surface:
  CommitRequest  — input shape (mirrors the API payload + CLI flags 1:1).
  CommitResult   — what happened.
  commit_session — commit a single session by ID.

Use case: force-committing a session that is stuck in in_progress or
finalizing state (e.g. via the API's POST /api/sessions/{id}/commit).

The core operation is always the DB mutation (drain pending writes +
flip the status row).  Optional side-effects — MovieClaim coordinator
fanout and ``pending_session_verify`` JSONL emission — are gated behind
``fanout_claims`` and ``emit_metrics`` flags on :class:`CommitRequest`.
The CLI sets both to True; the HTTP endpoint leaves them off by default.

Before draining, the commit is gated on the ADR-035 site-contract drift
sentinel (mirroring ``apps/cli/db/commit_session.py``): a critical-drift
verdict raises ``RuntimeError`` and refuses the commit, so the
operator-facing ``POST /api/sessions/{id}/commit`` path cannot bypass the
CLI gate. The gate is fail-open — a sentinel error never blocks the commit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CommitRequest:
    """Input to :func:`commit_session`.

    Parameters
    ----------
    session_id:
        The ReportSessions.Id to commit.  Required.
    force:
        When True, commit even if the session is not in an expected
        pre-committed state (in_progress, finalizing).  When False and
        the session is already committed, this is a no-op (idempotent).
    drop_pending:
        When True and the session is pending-mode, drop the staged writes
        from PendingMovieHistoryWrites / PendingTorrentHistoryWrites
        without promoting them (i.e. an intentional abort of the pending
        stage).  Mutually exclusive with promoting; if both force=True
        and drop_pending=True, pending writes are dropped and the session
        is marked committed.
    """

    session_id: str
    force: bool = False
    drop_pending: bool = False
    emit_metrics: bool = False
    fanout_claims: bool = False
    shard_date: Optional[str] = None


@dataclass
class CommitResult:
    """Output of :func:`commit_session`."""

    session_id: str
    new_state: str
    pending_dropped: int = 0
    error: Optional[str] = None
    claim_results: List[Dict[str, Any]] = field(default_factory=list)


def _emit_commit_metrics(
    session_id: str,
    *,
    drain: Optional[Dict[str, Any]],
    final_status: str,
    write_mode: str,
    commit_duration_ms: Optional[int],
) -> Dict[str, Any]:
    """Emit a ``pending_session_verify`` JSONL record after commit.

    Simplified version of the CLI's ``_emit_pending_verify``: skips
    shadow-audit comparison and GITHUB_OUTPUT (both CI-specific).
    """
    from datetime import datetime, timezone

    from javdb.storage.sessions.lifecycle_helpers import (
        append_jsonl_record,
        attach_run_identity,
    )
    from javdb.storage.db._db_reports import db_pending_session_stats

    try:
        stats = db_pending_session_stats(session_id)
    except Exception:
        stats = {}

    drain = drain or {}
    pending_applied = int(drain.get("pending_marked_applied", 0) or 0)
    pending_staged = (
        pending_applied
        + int(stats.get("pending_residual_count", 0) or 0)
    )
    record: Dict[str, Any] = {
        "kind": "pending_session_verify",
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "commit_session_lib",
        "session_id": session_id,
        "write_mode": write_mode,
        "final_status": final_status,
        "pending_staged_count": pending_staged,
        "pending_applied_count": pending_applied,
        "pending_residual_count": int(
            stats.get("pending_residual_count", 0) or 0,
        ),
        "commit_attempts": 1,
        "commit_duration_ms": commit_duration_ms,
        "hrefs_processed": int(drain.get("hrefs_processed", 0) or 0),
        "torrents_upserted": int(drain.get("torrents_upserted", 0) or 0),
        "torrents_deleted": int(drain.get("torrents_deleted", 0) or 0),
        "movies_upserted": int(drain.get("movies_upserted", 0) or 0),
        "worker_stage_rollback_failed": 0,
        "shadow_audit_enabled": False,
        "derived_recompute_drift": 0,
        "derived_drift_samples": [],
    }
    attach_run_identity(record, session_id)
    append_jsonl_record(record)
    return record


def commit_session(req: CommitRequest) -> CommitResult:
    """Commit a single session identified by ``req.session_id``.

    Raises
    ------
    LookupError
        If the session does not exist in ReportSessions.
    RuntimeError
        If the ADR-035 site-contract gate detects critical parser drift,
        or if the commit DB call fails.
    """
    from javdb.storage.db import (
        get_db,
        REPORTS_DB_PATH,
    )
    from javdb.storage.db._db_history_write import db_commit_session_history
    # Lazy import: lifecycle imports the _db_reports primitives at module top,
    # so importing it here (rather than at module top) avoids a circular import
    # while javdb.storage.db is still initializing.
    from javdb.storage.sessions.lifecycle import transition
    from javdb.infra.logging import get_logger

    logger = get_logger(__name__)

    reports_path = REPORTS_DB_PATH
    with get_db(reports_path) as conn:
        row = conn.execute(
            "SELECT Id, COALESCE(WriteMode,'audit') AS WriteMode, Status "
            "FROM ReportSessions WHERE Id = ?",
            (req.session_id,),
        ).fetchone()

    if row is None:
        raise LookupError(f"Session not found: session_id={req.session_id!r}")

    write_mode = row[1] if hasattr(row, '__getitem__') else row["WriteMode"]
    current_status = row[2] if hasattr(row, '__getitem__') else row["Status"]

    # Already committed — idempotent unless force is irrelevant here.
    if current_status == "committed" and not req.force:
        return CommitResult(
            session_id=req.session_id,
            new_state="committed",
            pending_dropped=0,
        )

    # ADR-035 site-contract gate: refuse to commit on critical parser drift,
    # mirroring the CLI gate in ``apps/cli/db/commit_session.py``. The
    # operator-facing ``POST /api/sessions/{id}/commit`` path would otherwise
    # bypass that CLI-only gate and promote drifted parse data into the live
    # history tables. Fail-open: a sentinel evaluation error must NEVER block
    # the commit (a sentinel bug cannot be allowed to wedge the commit path);
    # only a successful ``verdict.critical`` refuses. ``drop_pending`` is
    # exempt — it discards the staged rows instead of promoting them, so there
    # is nothing drifted to protect against. Imported lazily to preserve this
    # module's circular-import-safe import posture (sentinel persistence pulls
    # in ``javdb.storage.db`` at module top).
    from javdb.ops.sentinel.service import (
        evaluate_session as _sentinel_evaluate,
        mark_committed as _sentinel_mark_committed,
    )
    if not req.drop_pending:
        try:
            verdict = _sentinel_evaluate(str(req.session_id))
        except Exception:
            logger.warning(
                "Site-contract sentinel evaluation failed for session %s; "
                "treating as non-critical (fail-open).",
                req.session_id, exc_info=True,
            )
            verdict = None
        if verdict is not None and verdict.critical:
            logger.error(
                "Site-contract drift gate: critical drift for session %s "
                "(%d finding(s)); refusing commit.",
                req.session_id, len(verdict.findings),
            )
            raise RuntimeError(
                f"site-contract drift gate: critical parser drift for session "
                f"{req.session_id!r}; refusing commit "
                f"({len(verdict.findings)} finding(s))"
            )

    pending_dropped = 0
    drain: Optional[Dict[str, Any]] = None
    commit_duration_ms: Optional[int] = None
    drained_pending_session = False

    # For pending-mode sessions, drain staged writes (or drop them).
    if write_mode == "pending" and current_status != "committed":
        if req.drop_pending:
            # Drop pending writes without promoting them.
            with get_db(reports_path) as conn:
                pending_dropped = sum([
                    conn.execute(
                        "DELETE FROM PendingMovieHistoryWrites WHERE SessionId = ?",
                        (req.session_id,),
                    ).rowcount,
                    conn.execute(
                        "DELETE FROM PendingTorrentHistoryWrites WHERE SessionId = ?",
                        (req.session_id,),
                    ).rowcount,
                ])
            logger.info(
                "Dropped pending writes for session %s: %d rows",
                req.session_id, pending_dropped,
            )
        else:
            # Promote pending writes to live tables.
            try:
                t0 = time.monotonic()
                drain = db_commit_session_history(req.session_id)
                drained_pending_session = True
                commit_duration_ms = int((time.monotonic() - t0) * 1000)
                if drain.get("residual_cleanup"):
                    logger.info(
                        "Pending session cleanup (already committed): "
                        "id=%s residual_deleted=%d",
                        req.session_id,
                        drain.get("pending_deleted", 0),
                    )
                else:
                    logger.info(
                        "Pending session drained: id=%s drain=%s",
                        req.session_id, drain,
                    )
            except Exception as exc:
                raise RuntimeError(
                    f"db_commit_session_history failed for {req.session_id!r}: {exc}"
                ) from exc

    # Flip the status row. Routing through transition refuses illegal edges
    # per ADR-019 (e.g. a failed->committed source would raise
    # IllegalTransition rather than silently flip); the n==0 branch below
    # still handles the idempotent committed->committed no-op.
    try:
        n = transition(req.session_id, "committed")
    except Exception as exc:
        raise RuntimeError(
            f"transition to committed failed for {req.session_id!r}: {exc}"
        ) from exc

    if n == 0 and drained_pending_session:
        logger.info(
            "Session %s was marked committed by pending drain",
            req.session_id,
        )
    elif n == 0:
        logger.info("Session %s already committed (idempotent)", req.session_id)

    # ADR-035: a successful *promotion* makes this run's fills baseline-
    # eligible. Skip it on the ``drop_pending`` path — that discards the staged
    # parse data instead of promoting it, so those fill-rates must NOT enter the
    # soft-field baseline (an operator dropping a bad/failed parse would
    # otherwise pollute it). Symmetric with the gate's ``drop_pending`` exemption
    # above. Best-effort; never fail the commit on this (mirrors the CLI).
    if not req.drop_pending:
        try:
            _sentinel_mark_committed(str(req.session_id))
        except Exception:
            logger.warning(
                "Site-contract sentinel mark_committed failed for %s",
                req.session_id, exc_info=True,
            )

    claim_results: List[Dict[str, Any]] = []
    if req.fanout_claims:
        from javdb.storage.sessions.lifecycle_helpers import fanout_movie_claim
        claim_results = fanout_movie_claim(
            [req.session_id],
            operation="commit",
            shard_date=req.shard_date,
        )

    if req.emit_metrics and write_mode == "pending":
        _emit_commit_metrics(
            req.session_id,
            drain=drain,
            final_status="committed",
            write_mode=write_mode,
            commit_duration_ms=commit_duration_ms,
        )

    return CommitResult(
        session_id=req.session_id,
        new_state="committed",
        pending_dropped=pending_dropped,
        claim_results=claim_results,
    )
