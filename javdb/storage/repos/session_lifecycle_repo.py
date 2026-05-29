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
        from javdb.storage.db import db_mark_session_committed

        return db_mark_session_committed(session_id, db_path=self._db_path)

    def mark_session_failed(
        self,
        session_id: str,
        *,
        reason: Optional[str] = None,
    ) -> int:
        from javdb.storage.db import db_mark_session_failed

        return db_mark_session_failed(
            session_id,
            db_path=self._db_path,
            reason=reason,
        )
