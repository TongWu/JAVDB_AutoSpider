"""Rollback coordinator for JAVDB AutoSpider.

Coordinates rollback operations across all databases (history, reports, operations).

Uses pending mode rollback: delete from Pending* tables for uncommitted sessions,
or resume commit for sessions stuck in 'finalizing'.

The rollback coordinator delegates to db.py's db_rollback_session() which handles
the full rollback lifecycle.
"""

from typing import Optional

from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_rollback_reports_for_session = None
_rollback_operations_for_session = None
_get_session_status = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _rollback_reports_for_session
    global _rollback_operations_for_session, _get_session_status
    if _rollback_reports_for_session is None:
        from javdb.storage.db.db_reports import (
            rollback_reports_for_session,
            db_get_session_status,
        )
        from javdb.storage.db.db_operations import (
            rollback_operations_for_session,
        )
        _rollback_reports_for_session = rollback_reports_for_session
        _rollback_operations_for_session = rollback_operations_for_session
        _get_session_status = db_get_session_status


# ── Rollback coordinator ─────────────────────────────────────────────────


def db_rollback_session(session_id, **kwargs):
    """Rollback all changes for a session. Delegates to db.py."""
    from javdb.storage.db.db import db_rollback_session as _f
    return _f(session_id, **kwargs)


def db_resume_finalizing_session(session_id, **kwargs):
    """Resume a session stuck in 'finalizing' status. Delegates to db.py."""
    from javdb.storage.db.db import db_resume_finalizing_session as _f
    return _f(session_id, **kwargs)
