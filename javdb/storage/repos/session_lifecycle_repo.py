"""Write-Repo wrapper for ReportSessions lifecycle mutations."""

from __future__ import annotations

from typing import Optional


class SessionLifecycleRepo:
    """Thin typed wrapper over ReportSessions lifecycle write helpers.

    This intentionally stays separate from SessionsRepo: SessionsRepo is a
    conn-owned read/API surface, while this Repo mirrors the write-family
    pattern used by HistoryRepo, OperationsRepo, and StatsRepo.
    """

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    def init_storage(self) -> None:
        from javdb.storage.db import init_db

        init_db(db_path=self._db_path)

    def get_active_session_id(self) -> Optional[str]:
        from javdb.storage.db import get_active_session_id

        return get_active_session_id()

    def create_report_session(
        self,
        *,
        report_type: str,
        report_date: str,
        csv_filename: str,
    ) -> str:
        from javdb.storage.db import db_create_report_session

        return db_create_report_session(
            report_type=report_type,
            report_date=report_date,
            csv_filename=csv_filename,
            db_path=self._db_path,
        )

    def mark_session_committed(self, session_id: str) -> int:
        # Route through the SessionLifecycle authority (ADR-019) so the legal
        # transition graph is enforced for every caller — e.g. a failed→committed
        # edge raises IllegalTransition instead of silently resurrecting a failed
        # session, and finalizing→committed uses the strict primitive. Lazy import
        # avoids an import cycle (lifecycle imports the _db_reports primitives).
        from javdb.storage.sessions.lifecycle import transition

        return transition(session_id, "committed", db_path=self._db_path)

    def mark_session_failed(
        self,
        session_id: str,
        *,
        reason: Optional[str] = None,
    ) -> int:
        # Route through the SessionLifecycle authority (ADR-019): a committed→failed
        # edge raises IllegalTransition rather than corrupting a committed session.
        from javdb.storage.sessions.lifecycle import transition

        return transition(session_id, "failed", db_path=self._db_path, reason=reason)
