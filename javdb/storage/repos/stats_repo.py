"""StatsRepo — typed wrapper over the Stats domain (``reports.db``).

ADR-005 PR-1: thin delegate over ``javdb/storage/db/db_stats.py``.
"""

from __future__ import annotations

from typing import Optional


class StatsRepo:
    """Thin typed wrapper over SpiderStats / UploaderStats / PikpakStats.

    Method-for-method delegation to the ``db_*`` function family in
    ``javdb/storage/db/db_stats.py``. Establishes the interface surface
    so callers can migrate incrementally; PR-2 will inline the SQL here
    and retire the underlying functions.

    Construction takes an optional ``db_path`` override (used in tests /
    smoke runs against a fresh DB) and an optional ``session_id`` (kept for
    repo-API symmetry with ``HistoryRepo`` / ``OperationsRepo``; ADR-046 P2).
    Every method already takes ``session_id`` explicitly — there is no
    thread-local global to remove here, so the bound session is currently
    informational only.
    """

    def __init__(
        self, *, db_path: Optional[str] = None, session_id: Optional[str] = None,
    ) -> None:
        self._db_path = db_path
        self._session_id = session_id

    # ── Save (session_id required) ───────────────────────────────

    def save_spider_stats(self, session_id: str, stats: dict) -> int:
        """Upsert SpiderStats for *session_id*. Returns lastrowid."""
        from javdb.storage.db._db_stats import db_save_spider_stats
        return db_save_spider_stats(session_id, stats, db_path=self._db_path)

    def save_uploader_stats(self, session_id: str, stats: dict) -> int:
        """Upsert UploaderStats for *session_id*. Returns lastrowid."""
        from javdb.storage.db._db_stats import db_save_uploader_stats
        return db_save_uploader_stats(session_id, stats, db_path=self._db_path)

    def save_pikpak_stats(self, session_id: str, stats: dict) -> int:
        """Upsert PikpakStats for *session_id*. Returns lastrowid."""
        from javdb.storage.db._db_stats import db_save_pikpak_stats
        return db_save_pikpak_stats(session_id, stats, db_path=self._db_path)

    # ── Read (D1-aware in dual mode) ─────────────────────────────

    def get_spider_stats(self, session_id: str) -> Optional[dict]:
        """Fetch SpiderStats row. In dual mode reads from D1."""
        from javdb.storage.db._db_stats import db_get_spider_stats
        return db_get_spider_stats(session_id, db_path=self._db_path)

    def get_uploader_stats(self, session_id: str) -> Optional[dict]:
        """Fetch UploaderStats row. In dual mode reads from D1."""
        from javdb.storage.db._db_stats import db_get_uploader_stats
        return db_get_uploader_stats(session_id, db_path=self._db_path)

    def get_pikpak_stats(self, session_id: str) -> Optional[dict]:
        """Fetch PikpakStats row. In dual mode reads from D1."""
        from javdb.storage.db._db_stats import db_get_pikpak_stats
        return db_get_pikpak_stats(session_id, db_path=self._db_path)

    # ── Read (SQLite-only, for observability) ─────────────────────

    def get_spider_stats_local(self, session_id: str) -> Optional[dict]:
        """SQLite-only read — for email notifications / drift advisories."""
        from javdb.storage.db._db_stats import db_get_spider_stats_local
        return db_get_spider_stats_local(session_id, db_path=self._db_path)

    def get_uploader_stats_local(self, session_id: str) -> Optional[dict]:
        """SQLite-only read — for email notifications / drift advisories."""
        from javdb.storage.db._db_stats import db_get_uploader_stats_local
        return db_get_uploader_stats_local(session_id, db_path=self._db_path)

    def get_pikpak_stats_local(self, session_id: str) -> Optional[dict]:
        """SQLite-only read — for email notifications / drift advisories."""
        from javdb.storage.db._db_stats import db_get_pikpak_stats_local
        return db_get_pikpak_stats_local(session_id, db_path=self._db_path)
