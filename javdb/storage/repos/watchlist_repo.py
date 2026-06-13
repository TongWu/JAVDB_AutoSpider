"""Repository for the WatchIntent table (ADR-054 WS1)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from javdb.storage import db as _db
from javdb.storage.db import get_db

# Byte-mirrored with server/services/watchlist-service.ts (ADR-017 dual-backend
# parity). Pinned by tests/unit/test_watch_intent_upsert_parity.py.
WATCH_INTENT_UPSERT_SQL = """
    INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at)
    VALUES (?, ?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(video_code) DO UPDATE SET
        href       = excluded.href,
        status     = excluded.status,
        notes      = excluded.notes,
        status_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
"""


class WatchIntentRepo:
    """Typed wrapper over the WatchIntent table in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        # Resolve HISTORY_DB_PATH at construction via ``_db`` (not bound at
        # import) so pytest's path monkeypatch is honoured (BFR-016).
        self._db_path = db_path or _db.HISTORY_DB_PATH

    def upsert(
        self, *, video_code: str, href: str, status: str, notes: Optional[str] = None
    ) -> dict:
        """UPSERT a watch intent. Returns the updated row as a dict."""
        with get_db(self._db_path) as conn:
            conn.execute(WATCH_INTENT_UPSERT_SQL, (video_code, href, status, notes))
            row = conn.execute(
                "SELECT * FROM WatchIntent WHERE video_code = ?", (video_code,)
            ).fetchone()
        return dict(row)

    def get(self, video_code: str) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM WatchIntent WHERE video_code = ?", (video_code,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list(
        self, *, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count) for paginated listing."""
        where = "WHERE status = ?" if status else ""
        params: list = [status] if status else []
        with get_db(self._db_path) as conn:
            # Alias + key access: D1/Dual cursors return dict-shaped rows.
            total = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM WatchIntent {where}",  # noqa: S608
                params,
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"SELECT * FROM WatchIntent {where} "  # noqa: S608
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def delete(self, video_code: str) -> bool:
        """Delete a watch intent (un-track). Returns True if a row was removed."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM WatchIntent WHERE video_code = ?", (video_code,)
            )
            return cur.rowcount > 0
