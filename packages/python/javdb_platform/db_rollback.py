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


def db_rollback_session(session_id, **kwargs):
    """Rollback all changes for a session. Delegates to db.py."""
    from packages.python.javdb_platform.db import db_rollback_session as _f
    return _f(session_id, **kwargs)


def db_resume_finalizing_session(session_id, **kwargs):
    """Resume a session stuck in 'finalizing' status. Delegates to db.py."""
    from packages.python.javdb_platform.db import db_resume_finalizing_session as _f
    return _f(session_id, **kwargs)
