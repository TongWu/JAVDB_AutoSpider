"""Commit library — force a session to committed state.

Public surface:
  CommitRequest  — input shape (mirrors the API payload + CLI flags 1:1).
  CommitResult   — what happened.
  commit_session — commit a single session by ID.

Use case: force-committing a session that is stuck in in_progress or
finalizing state (e.g. via the API's POST /api/sessions/{id}/commit).

The core operation is always the DB mutation (drain pending writes +
flip the status row). ADR-035 site-contract sentinel evaluation gates
non-idempotent commit attempts before that mutation; critical drift refuses
the commit, while sentinel errors fail open. After any successful or
idempotent commit that promotes parse data, sentinel fills are best-effort
marked committed so they can become baseline-eligible. ``drop_pending`` is
exempt from both evaluation and marking because it discards staged rows.
Optional side-effects — MovieClaim coordinator fanout and
``pending_session_verify`` JSONL emission — are gated behind
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


class SiteContractDriftError(RuntimeError):
    """Raised when critical site-contract drift refuses a session commit."""


def _row_value(row: Any, name: str, index: int) -> Any:
    """Read one column from a DB row, whatever shape the backend returned.

    D1 returns plain dicts (name access only); sqlite3.Row supports both name
    and index access; plain tuples support index access only. Same idiom as
    ``javdb.storage.repos.system_state_repo.SystemStateRepo.get``.
    """
    try:
        return row[name]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _sentinel_evaluate(session_id: str) -> Any:
    """Evaluate persisted site-contract fills for the session.

    Lazy import keeps the storage session library independent from ops import
    timing while javdb.storage.db is still initializing.
    """
    from javdb.ops.sentinel.service import evaluate_session

    return evaluate_session(session_id)


def _sentinel_mark_committed(session_id: str) -> None:
    """Mark committed site-contract fills as baseline-eligible."""
    from javdb.ops.sentinel.service import mark_committed

    mark_committed(session_id)


def _best_effort_mark_sentinel_committed(session_id: str, logger: Any) -> None:
    """Mark sentinel fills committed without making commit depend on it."""
    try:
        _sentinel_mark_committed(str(session_id))
    except Exception:
        logger.warning(
            "Site-contract sentinel mark_committed failed for %s",
            session_id,
            exc_info=True,
        )


def _gate_site_contract_drift(session_id: str, logger: Any) -> None:
    """Refuse commits on critical site-contract drift.

    Sentinel evaluation failures are fail-open: the commit path proceeds and
    mark_committed remains best-effort after a successful/idempotent commit.
    """
    try:
        verdict = _sentinel_evaluate(str(session_id))
    except Exception:
        logger.warning(
            "Site-contract sentinel evaluation failed for session %s; "
            "treating as non-critical (fail-open).",
            session_id,
            exc_info=True,
        )
        return
    if verdict is not None and verdict.critical:
        findings = verdict.findings or []
        raise SiteContractDriftError(
            "site-contract drift gate: critical drift for session "
            f"{session_id!r} ({len(findings)} finding(s)); "
            "refusing commit"
        )


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
    from javdb.storage.repos.history_repo import HistoryRepo
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

    write_mode = _row_value(row, "WriteMode", 1)
    current_status = _row_value(row, "Status", 2)

    # Already committed — idempotent unless force is irrelevant here.
    if current_status == "committed" and not req.force:
        if not req.drop_pending:
            _gate_site_contract_drift(req.session_id, logger)
            _best_effort_mark_sentinel_committed(req.session_id, logger)
        return CommitResult(
            session_id=req.session_id,
            new_state="committed",
            pending_dropped=0,
        )

    # ADR-035 Phase 1 site-contract gate: refuse non-idempotent commits on
    # critical parser drift before draining/dropping pending writes or flipping
    # the session status. Sentinel failures are fail-open to match the CLI.
    # ``drop_pending`` discards staged rows instead of promoting them, so it is
    # exempt from both drift evaluation and baseline marking.
    if not req.drop_pending:
        _gate_site_contract_drift(req.session_id, logger)

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
                drain = HistoryRepo().commit_session(req.session_id)
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
                    f"HistoryRepo().commit_session failed for {req.session_id!r}: {exc}"
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

    if not req.drop_pending:
        _best_effort_mark_sentinel_committed(req.session_id, logger)

    claim_results: List[Dict[str, Any]] = []
    if req.fanout_claims:
        from javdb.storage.sessions.lifecycle_helpers import fanout_movie_claim
        claim_results = fanout_movie_claim(
            [req.session_id],
            operation="commit",
            shard_date=req.shard_date,
        )

    if req.emit_metrics and write_mode == "pending":
        from javdb.storage.drift_io import append_jsonl_record
        from javdb.storage.sessions.lifecycle_helpers import attach_run_identity
        from javdb.storage.sessions.pending_verify import (
            build_pending_verify_record,
        )

        stats_read_error = False
        try:
            stats = HistoryRepo().pending_session_stats(req.session_id)
        except Exception:
            stats = {}
            stats_read_error = True
        record = build_pending_verify_record(
            req.session_id,
            source="commit_session_lib",
            write_mode=write_mode,
            final_status="committed",
            drain=drain,
            stats=stats,
            commit_attempts=1,
            commit_duration_ms=commit_duration_ms,
            shadow_audit_enabled=False,
            shadow_audit_result=None,
            stats_read_error=stats_read_error,
        )
        attach_run_identity(record, req.session_id)
        append_jsonl_record(record)

    return CommitResult(
        session_id=req.session_id,
        new_state="committed",
        pending_dropped=pending_dropped,
        claim_results=claim_results,
    )
