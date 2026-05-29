"""Single authority for legal ReportSessions status transitions (ADR-019).

A pipeline Session moves ``in_progress -> finalizing -> committed`` on the
happy path, or ``-> failed`` on error. The legal edges were historically
encoded inline in SQL ``WHERE`` clauses across the four primitives in
``javdb.storage.db._db_reports`` with *inconsistent* guards (two of them
allowed illegal terminal-state transitions, a latent data-corruption path).

This module centralises legality:

* :func:`can_transition` is the pure decision function (no DB).
* :func:`transition` validates the requested edge against the current
  status, then dispatches to the matching ``_db_reports`` primitive. Truly
  illegal edges raise :class:`IllegalTransition`; idempotent same-state
  edges are pure no-ops returning ``0``.

The legal transition graph::

    in_progress -> finalizing        (normal commit start)
    in_progress -> committed         (staging fast-path: rclone / empty commit)
    in_progress -> failed
    finalizing  -> committed
    finalizing  -> failed
    X -> X                           (same state, idempotent no-op)

``committed -> failed`` and ``failed -> committed`` are ILLEGAL. An unknown
or ``None`` source status (e.g. a non-existent session) is never allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from javdb.storage.db import (
    db_begin_finalize_session as _db_begin_finalize_session,
    db_finish_commit_session as _db_finish_commit_session,
    db_get_session_status as _db_get_session_status,
    db_mark_session_committed as _db_mark_session_committed,
    db_mark_session_failed as _db_mark_session_failed,
)

IN_PROGRESS, FINALIZING, COMMITTED, FAILED = (
    "in_progress",
    "finalizing",
    "committed",
    "failed",
)
_ALL = {IN_PROGRESS, FINALIZING, COMMITTED, FAILED}
_LEGAL = {
    (IN_PROGRESS, FINALIZING),
    (IN_PROGRESS, COMMITTED),
    (IN_PROGRESS, FAILED),
    (FINALIZING, COMMITTED),
    (FINALIZING, FAILED),
}


class IllegalTransition(RuntimeError):
    """Raised when a requested status transition is not in the legal graph."""


@dataclass(frozen=True)
class SessionState:
    """Snapshot of a session's ``(WriteMode, Status)`` pair.

    A non-existent session yields ``SessionState(None, None)``.
    """

    write_mode: Optional[str]
    status: Optional[str]


def can_transition(frm: Optional[str], to: str) -> bool:
    """Pure. ``True`` if ``frm -> to`` is legal or an idempotent no-op.

    A ``None`` source (unknown / non-existent session) and any unknown
    target status are always ``False``.
    """
    if to not in _ALL:
        return False
    if frm == to:
        return True
    if frm is None:
        return False
    return (frm, to) in _LEGAL


def get_state(session_id: str, *, db_path: Optional[str] = None) -> SessionState:
    """Read the current ``(WriteMode, Status)`` for *session_id*.

    Returns ``SessionState(None, None)`` if the session does not exist.
    """
    row = _db_get_session_status(session_id, db_path=db_path)
    if row is None:
        return SessionState(write_mode=None, status=None)
    write_mode, status = row
    return SessionState(write_mode=write_mode, status=status)


def transition(
    session_id: str,
    to: str,
    *,
    db_path: Optional[str] = None,
    reason: Optional[str] = None,
) -> int:
    """Validate, then dispatch to the matching ``_db_reports`` primitive.

    Args:
        session_id: Session identifier.
        to: Target status (one of ``in_progress``/``finalizing``/
            ``committed``/``failed``).
        db_path: Database path forwarded to the primitive.
        reason: Optional failure reason (only used for ``-> failed``).

    Returns:
        Number of ReportSessions rows updated by the primitive. An
        idempotent same-state transition returns ``0`` without any write.

    Raises:
        IllegalTransition: If ``current -> to`` is not in the legal graph.
    """
    frm = get_state(session_id, db_path=db_path).status
    if not can_transition(frm, to):
        raise IllegalTransition(f"{session_id}: {frm} -> {to} is not allowed")
    if frm == to:
        # Idempotent no-op: nothing to write.
        return 0
    if to == FINALIZING:
        return _db_begin_finalize_session(session_id, db_path=db_path)
    if to == FAILED:
        return _db_mark_session_failed(session_id, db_path=db_path, reason=reason)
    if to == COMMITTED:
        # finalizing->committed uses the strict primitive;
        # in_progress->committed (staging fast-path) uses the loose one.
        if frm == FINALIZING:
            return _db_finish_commit_session(session_id, db_path=db_path)
        return _db_mark_session_committed(session_id, db_path=db_path)
    # Unreachable: can_transition already rejected unknown targets.
    raise IllegalTransition(f"unknown target status {to!r}")
