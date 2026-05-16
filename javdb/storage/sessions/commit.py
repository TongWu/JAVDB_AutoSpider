"""Commit library — force a session to committed state.

Public surface:
  CommitRequest  — input shape (mirrors the API payload + CLI flags 1:1).
  CommitResult   — what happened.
  commit_session — commit a single session by ID.

Use case: force-committing a session that is stuck in in_progress or
finalizing state (e.g. via the API's POST /api/sessions/{id}/commit).

This library calls the DB functions directly rather than delegating to
apps.cli.commit_session because the CLI is orchestration-heavy (GitHub
outputs, claim fanouts, JSONL records).  The API only needs the core
DB mutations: drain pending writes + flip the status row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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


@dataclass
class CommitResult:
    """Output of :func:`commit_session`."""

    session_id: str
    new_state: str
    pending_dropped: int = 0
    error: Optional[str] = None


def commit_session(req: CommitRequest) -> CommitResult:
    """Commit a single session identified by ``req.session_id``.

    Raises
    ------
    LookupError
        If the session does not exist in ReportSessions.
    RuntimeError
        If the commit DB call fails.
    """
    import packages.python.javdb_platform.db_connection as _db_conn
    from packages.python.javdb_platform.db_history_write import (
        db_commit_session_history,
    )
    from packages.python.javdb_platform.db_reports import (
        db_find_in_progress_sessions,
        db_mark_session_committed,
    )
    from packages.python.javdb_platform.logging_config import get_logger

    logger = get_logger(__name__)

    reports_path = _db_conn.REPORTS_DB_PATH
    with _db_conn.get_db(reports_path) as conn:
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

    pending_dropped = 0

    # For pending-mode sessions, drain staged writes (or drop them).
    if write_mode == "pending" and current_status != "committed":
        if req.drop_pending:
            # Drop pending writes without promoting them.
            with _db_conn.get_db(reports_path) as conn:
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
                drain = db_commit_session_history(req.session_id)
                logger.info(
                    "Pending session drained: id=%s drain=%s",
                    req.session_id, drain,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"db_commit_session_history failed for {req.session_id!r}: {exc}"
                ) from exc

    # Flip the status row.
    try:
        n = db_mark_session_committed(req.session_id)
    except Exception as exc:
        raise RuntimeError(
            f"db_mark_session_committed failed for {req.session_id!r}: {exc}"
        ) from exc

    if n == 0:
        # Idempotent — was already committed by another call.
        logger.info("Session %s already committed (idempotent)", req.session_id)

    return CommitResult(
        session_id=req.session_id,
        new_state="committed",
        pending_dropped=pending_dropped,
    )
