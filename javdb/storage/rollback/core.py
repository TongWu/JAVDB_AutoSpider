"""Rollback library — extracted from ``apps/cli/rollback.py``.

Public surface:
  :class:`RollbackRequest` — input shape (mirrors the CLI flags 1:1, plus
    semantic aliases ``include_pending`` / ``restore_from_audit`` that
    HTTP callers find easier to reason about).
  :class:`RollbackPlan`    — what would happen (returned by
    :func:`plan_rollback`).
  :class:`RollbackResult`  — what happened (returned by
    :func:`apply_rollback`).
  :func:`plan_rollback`    — resolve target sessions, run the rollback
    pipeline in dry-run, return a :class:`RollbackPlan`.  Raises
    :class:`LookupError` when an explicit ``session_id`` cannot be
    resolved to a known session.
  :func:`apply_rollback`   — same pipeline, but with ``dry_run=False``;
    returns a :class:`RollbackResult`.  Same :class:`LookupError`
    semantics.

The pipeline itself lives in :mod:`apps.cli.rollback._drive_rollback`
— the library is a thin adapter that builds an :class:`argparse.Namespace`
from a :class:`RollbackRequest` and invokes the driver.  This guarantees
HTTP callers and the CLI use literally the same code path, so a
behaviour change in one is automatically a behaviour change in the
other.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Public dataclasses ──────────────────────────────────────────────────


@dataclass
class RollbackRequest:
    """Inputs to :func:`plan_rollback` / :func:`apply_rollback`.

    Mirrors the CLI flags 1:1.  ``include_pending`` and
    ``restore_from_audit`` are HTTP-friendly aliases that map onto the
    underlying CLI semantics:

    * ``include_pending`` -> ``--auto-resume-finalizing``.  When True,
      pending-mode sessions stuck in ``Status='finalizing'`` are driven
      to committed instead of surfaced as ``failed_sessions``.
    * ``restore_from_audit`` is informational — the underlying
      ``db_rollback_session`` always replays audit rows for
      ``audit``-mode sessions when the scope includes history.  The
      flag exists so HTTP callers can declare intent and so the
      dataclass shape matches the Task 11 spec.
    """

    session_id: Optional[str] = None
    run_id: Optional[str] = None
    run_attempt: Optional[int] = None
    run_started_at: Optional[str] = None
    scope: str = "all"
    include_orphaned: bool = False
    failure_reason: Optional[str] = None
    dry_run: bool = True
    force: bool = False
    shard_date: Optional[str] = None
    no_claim_rollback: bool = False
    # See class docstring — these two are HTTP-friendly aliases.
    include_pending: bool = True
    restore_from_audit: bool = True
    claim_rollback_attempts: int = 3


@dataclass
class RollbackPlan:
    """Output of :func:`plan_rollback` (``dry_run=True``).

    ``session_id`` reflects the *first* resolved session id (or the
    explicit ``RollbackRequest.session_id``), or an empty string when
    the request resolved to zero sessions.  ``actions`` is the
    per-session ``summaries`` list produced by the rollback pipeline;
    ``summary`` is the top-level summary dict minus the per-session
    detail.
    """

    session_id: str
    actions: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RollbackResult:
    """Output of :func:`apply_rollback` (``dry_run=False``)."""

    session_id: str
    applied: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


# ── Internal helpers ────────────────────────────────────────────────────


def _build_namespace(
    req: RollbackRequest, *, dry_run: bool,
) -> argparse.Namespace:
    """Translate a :class:`RollbackRequest` into the
    :class:`argparse.Namespace` shape that
    :func:`apps.cli.rollback._drive_rollback` consumes."""
    return argparse.Namespace(
        session_id=req.session_id,
        run_id=req.run_id,
        attempt=req.run_attempt,
        run_started_at=req.run_started_at,
        scope=req.scope,
        include_orphaned=req.include_orphaned,
        failure_reason=req.failure_reason,
        dry_run=dry_run,
        force=req.force,
        shard_date=req.shard_date,
        no_claim_rollback=req.no_claim_rollback,
        auto_resume_finalizing=req.include_pending,
        claim_rollback_attempts=req.claim_rollback_attempts,
    )


def _session_exists(session_id: str) -> bool:
    """Probe ReportSessions for *session_id*.

    Used by the library entrypoints to convert "explicit unknown id" into
    :class:`LookupError` (HTTP 404).  Failures fall back to ``True`` so
    transient DB errors do not masquerade as 404s — the caller will get
    a proper exception from the rollback pipeline instead.
    """
    from javdb.storage.db import db as _db
    from javdb.storage.db.db_connection import get_db

    try:
        with get_db(_db.REPORTS_DB_PATH) as conn:
            row = conn.execute(
                "SELECT 1 FROM ReportSessions WHERE Id=? LIMIT 1",
                (session_id,),
            ).fetchone()
        return row is not None
    except Exception:
        return True


def _run(req: RollbackRequest, *, dry_run: bool) -> Dict[str, Any]:
    """Call into the CLI's rollback driver.

    The CLI module owns the canonical pipeline so that existing tests
    (which monkeypatch ``apps.cli.rollback._resolve_target_sessions``,
    ``db_rollback_session``, ``_emit_metrics`` etc.) still see their
    patches when the library is invoked.  This also guarantees the
    library and CLI never drift in behaviour.
    """
    # Late import to avoid pulling argparse / logging setup at module
    # import time (the FastAPI app imports this module on boot).
    from apps.cli import rollback as _rollback_cli

    # Validate an explicit session_id before resolving — gives HTTP
    # callers a clean 404 instead of an empty summary.
    if req.session_id is not None and not _session_exists(req.session_id):
        raise LookupError(
            f"Session not found: session_id={req.session_id!r}"
        )

    args = _build_namespace(req, dry_run=dry_run)
    summary, _exit_code = _rollback_cli._drive_rollback(args)

    # The CLI driver returns an empty/no-targets sentinel when a
    # run_id+attempt lookup yielded nothing.  Convert that to a
    # LookupError for explicit lookups so HTTP callers get 404.
    if summary.get("_no_targets") and req.run_id is not None:
        raise LookupError(
            f"No sessions matched the rollback request "
            f"(run_id={req.run_id!r}, run_attempt={req.run_attempt!r})"
        )

    # Strip internal hint keys before handing back to callers.
    return {k: v for k, v in summary.items() if not k.startswith("_")}


# ── Public entrypoints ──────────────────────────────────────────────────


def plan_rollback(req: RollbackRequest) -> RollbackPlan:
    """Resolve targets and run the rollback pipeline in dry-run.

    Always runs as ``dry_run=True`` regardless of ``req.dry_run`` —
    a plan never mutates state.  Raises :class:`LookupError` when the
    request specifies an explicit ``session_id`` (or ``run_id`` +
    ``run_attempt``) that cannot be resolved to any known session.
    """
    summary = _run(req, dry_run=True)
    sessions = list(summary.get("sessions") or [])
    session_id = (
        req.session_id
        or (sessions[0].get("session_id") if sessions else "")
    )
    rest = {k: v for k, v in summary.items() if k != "sessions"}
    return RollbackPlan(
        session_id=str(session_id) if session_id is not None else "",
        actions=sessions,
        summary=rest,
    )


def apply_rollback(req: RollbackRequest) -> RollbackResult:
    """Resolve targets and run the rollback for real.

    Always runs as ``dry_run=False`` regardless of ``req.dry_run`` —
    callers that want a preview should call :func:`plan_rollback`.
    Raises :class:`LookupError` when the request specifies an explicit
    ``session_id`` (or ``run_id`` + ``run_attempt``) that cannot be
    resolved to any known session.
    """
    summary = _run(req, dry_run=False)
    sessions = list(summary.get("sessions") or [])
    session_id = (
        req.session_id
        or (sessions[0].get("session_id") if sessions else "")
    )
    rest = {k: v for k, v in summary.items() if k != "sessions"}
    return RollbackResult(
        session_id=str(session_id) if session_id is not None else "",
        applied=sessions,
        summary=rest,
    )
