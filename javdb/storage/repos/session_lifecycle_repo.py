"""Write-Repo wrapper for ReportSessions lifecycle mutations."""

from __future__ import annotations

from typing import List, Optional, Tuple


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
        """Mark the session committed via the SessionLifecycle authority (ADR-019).

        Routes through ``transition`` so the legal graph is enforced for every
        caller: a ``failed→committed`` edge raises ``IllegalTransition`` instead of
        silently resurrecting a failed session, and ``finalizing→committed`` uses
        the strict primitive.
        """
        # Lazy import avoids the lifecycle↔db import cycle (lifecycle imports the
        # _db_reports primitives at module top).
        from javdb.storage.sessions.lifecycle import transition

        return transition(session_id, "committed", db_path=self._db_path)

    def mark_session_failed(
        self,
        session_id: str,
        *,
        reason: Optional[str] = None,
    ) -> int:
        """Mark the session failed via the SessionLifecycle authority (ADR-019).

        Routes through ``transition`` so a ``committed→failed`` edge raises
        ``IllegalTransition`` rather than corrupting a committed session.
        """
        from javdb.storage.sessions.lifecycle import transition

        return transition(session_id, "failed", db_path=self._db_path, reason=reason)

    def rollback_session(
        self,
        session_id: str,
        *,
        dry_run: bool = False,
        scope: str = "all",
        force: bool = False,
        history_db_path: Optional[str] = None,
        reports_db_path: Optional[str] = None,
        operations_db_path: Optional[str] = None,
        run_started_at: Optional[str] = None,
        failure_reason: Optional[str] = None,
        auto_resume_finalizing: bool = True,
    ) -> dict:
        """Roll back all D1/SQLite writes that belong to *session_id*.

        Thin delegate to ``db_rollback_session`` (ADR-032 Phase 2a). The
        Repo's ``db_path`` is threaded as the default ``reports_db_path``;
        explicit per-call overrides take precedence.
        """
        from javdb.storage.db import db_rollback_session

        return db_rollback_session(
            session_id,
            dry_run=dry_run,
            scope=scope,
            force=force,
            history_db_path=history_db_path,
            reports_db_path=(
                reports_db_path if reports_db_path is not None else self._db_path
            ),
            operations_db_path=operations_db_path,
            run_started_at=run_started_at,
            failure_reason=failure_reason,
            auto_resume_finalizing=auto_resume_finalizing,
        )

    # ── Thin db_* delegates (ADR-032 Phase 2a) ────────────────────────
    #
    # 1:1 wrappers over the session/reports function family in
    # ``javdb/storage/db/_db_reports.py``. They route through the
    # function family (which opens its own connection from ``db_path``),
    # so the Repo's ``self._db_path`` is threaded directly. Phase 2b
    # repoints these onto inlined SQL.

    def find_in_progress_sessions(
        self,
        *,
        since: Optional[str] = None,
        max_age_hours: Optional[float] = None,
        require_run_identity: bool = False,
    ) -> List[str]:
        """Return ReportSessions.Id rows still flagged 'in_progress'."""
        from javdb.storage.db import db_find_in_progress_sessions

        return db_find_in_progress_sessions(
            since=since,
            max_age_hours=max_age_hours,
            require_run_identity=require_run_identity,
            db_path=self._db_path,
        )

    def find_sessions_by_run(
        self,
        run_id: str,
        run_attempt: Optional[int] = None,
        *,
        history_db_path: Optional[str] = None,
    ) -> List[str]:
        """Return every session id touched by a (RunId, RunAttempt) run."""
        from javdb.storage.db import db_find_sessions_by_run

        return db_find_sessions_by_run(
            run_id,
            run_attempt,
            reports_db_path=self._db_path,
            history_db_path=history_db_path,
        )

    def get_session_run_identity(
        self, session_id: str,
    ) -> Optional[Tuple[Optional[str], Optional[int]]]:
        """Return ``(RunId, RunAttempt)`` for *session_id*, or ``None``."""
        from javdb.storage.db import db_get_session_run_identity

        return db_get_session_run_identity(session_id, db_path=self._db_path)

    def pending_session_stats(self, session_id: str) -> dict[str, int]:
        """Snapshot pending-table counts for *session_id* (Phase 2 verify)."""
        from javdb.storage.db import db_pending_session_stats

        return db_pending_session_stats(session_id, db_path=self._db_path)

    def find_stale_pending_sessions(
        self,
        *,
        max_age_hours: float = 48.0,
        require_run_identity: bool = True,
    ) -> List[Tuple[str, str, str]]:
        """Return [(Id, Status, WriteMode), ...] for stale Phase 3 sessions."""
        from javdb.storage.db import db_find_stale_pending_sessions

        return db_find_stale_pending_sessions(
            db_path=self._db_path,
            max_age_hours=max_age_hours,
            require_run_identity=require_run_identity,
        )

    def find_in_progress_session_ids_for_run_csv(
        self,
        run_id: str,
        run_attempt: Optional[int],
        csv_filename: str,
    ) -> List[str]:
        """Return 'in_progress' SessionIds for the same run + CSV file."""
        from javdb.storage.db import db_find_in_progress_session_ids_for_run_csv

        return db_find_in_progress_session_ids_for_run_csv(
            run_id,
            run_attempt,
            csv_filename,
            db_path=self._db_path,
        )

    def get_latest_session_local(
        self, report_type: Optional[str] = None,
    ) -> Optional[dict]:
        """SQLite-only latest-session lookup (optionally by report type)."""
        from javdb.storage.db import db_get_latest_session_local

        return db_get_latest_session_local(report_type, self._db_path)

    def insert_report_rows(self, session_id: str, rows: List[dict]) -> int:
        """Insert report rows into ReportMovies + ReportTorrents."""
        from javdb.storage.db import db_insert_report_rows

        return db_insert_report_rows(session_id, rows, self._db_path)
