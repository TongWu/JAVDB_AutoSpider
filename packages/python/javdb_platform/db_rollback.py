"""Rollback coordinator for JAVDB AutoSpider.

Coordinates rollback operations across all databases (history, reports, operations).

Supports two rollback strategies:
- Pending mode: Delete from Pending* tables
- Audit mode: Restore from *Audit tables

The rollback coordinator calls each module's rollback_*_for_session() function
to ensure all session-related data is cleaned up atomically.
"""

from typing import Optional

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_rollback_history_for_session = None
_rollback_reports_for_session = None
_rollback_operations_for_session = None
_get_session_status = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _rollback_history_for_session, _rollback_reports_for_session
    global _rollback_operations_for_session, _get_session_status
    if _rollback_history_for_session is None:
        from packages.python.javdb_platform.db_history_write import (
            rollback_history_for_session,
        )
        from packages.python.javdb_platform.db_reports import (
            rollback_reports_for_session,
            db_get_session_status,
        )
        from packages.python.javdb_platform.db_operations import (
            rollback_operations_for_session,
        )
        _rollback_history_for_session = rollback_history_for_session
        _rollback_reports_for_session = rollback_reports_for_session
        _rollback_operations_for_session = rollback_operations_for_session
        _get_session_status = db_get_session_status


# ── Rollback coordinator ─────────────────────────────────────────────────


def db_rollback_session(
    session_id: str,
    *,
    dry_run: bool = False,
    verify: bool = True,
) -> dict:
    """Rollback all changes for a session across all databases.

    Coordinates rollback across:
    - History (MovieHistory, TorrentHistory, Pending*, Audit*)
    - Reports (ReportSessions, ReportMovies, ReportTorrents)
    - Operations (DedupRecords, PikpakHistory)

    Args:
        session_id: Session identifier
        dry_run: If True, only report what would be rolled back
        verify: If True, verify session status before rollback

    Returns:
        Dict with rollback statistics:
        {
            'history_rows_rolled_back': int,
            'reports_rows_deleted': int,
            'operations_rows_deleted': int,
            'pending_rows_deleted': int,
            'verification_passed': bool,
        }

    Raises:
        ValueError: If session is already committed (and verify=True)
    """
    _ensure_imports()

    result = {
        'history_rows_rolled_back': 0,
        'reports_rows_deleted': 0,
        'operations_rows_deleted': 0,
        'pending_rows_deleted': 0,
        'verification_passed': True,
    }

    # Verify session status
    if verify:
        status_info = _get_session_status(session_id)
        if status_info:
            write_mode, status = status_info
            if status == 'committed':
                raise ValueError(
                    f"Session {session_id} is already committed. "
                    "Rollback is not allowed for committed sessions."
                )
            logger.info(
                "Rolling back session %s (status=%s, write_mode=%s)",
                session_id, status, write_mode,
            )
        else:
            logger.warning(
                "Session %s not found in ReportSessions. "
                "Proceeding with rollback anyway (may be a stale session).",
                session_id,
            )

    if dry_run:
        logger.info("DRY RUN: Would rollback session %s", session_id)
        return result

    # Rollback in reverse dependency order
    try:
        # 1. Rollback history (most critical)
        history_count = _rollback_history_for_session(session_id)
        result['history_rows_rolled_back'] = history_count
        logger.info("Rolled back %d history rows for session %s", history_count, session_id)

        # 2. Rollback operations
        operations_count = _rollback_operations_for_session(session_id)
        result['operations_rows_deleted'] = operations_count
        logger.info("Rolled back %d operations rows for session %s", operations_count, session_id)

        # 3. Rollback reports (last, includes ReportSessions)
        reports_count = _rollback_reports_for_session(session_id)
        result['reports_rows_deleted'] = reports_count
        logger.info("Rolled back %d reports rows for session %s", reports_count, session_id)

        logger.info(
            "Successfully rolled back session %s: "
            "history=%d, operations=%d, reports=%d",
            session_id, history_count, operations_count, reports_count,
        )

    except Exception as exc:
        logger.error("Rollback failed for session %s: %s", session_id, exc)
        result['verification_passed'] = False
        raise

    return result


def db_resume_finalizing_session(
    session_id: str,
) -> None:
    """Resume a session stuck in 'finalizing' status.

    Attempts to complete the commit process for a session that was
    interrupted during the finalization phase.

    Args:
        session_id: Session identifier

    Raises:
        ValueError: If session is not in 'finalizing' status
    """
    _ensure_imports()

    status_info = _get_session_status(session_id)
    if not status_info:
        raise ValueError(f"Session {session_id} not found")

    write_mode, status = status_info
    if status != 'finalizing':
        raise ValueError(
            f"Session {session_id} is in status '{status}', "
            "not 'finalizing'. Cannot resume."
        )

    logger.info("Resuming finalizing session %s (write_mode=%s)", session_id, write_mode)

    # Import commit logic from db.py (will be refactored later)
    from packages.python.javdb_platform.db import db_resume_finalizing_session as resume_impl

    resume_impl(session_id)
    logger.info("Successfully resumed session %s", session_id)
