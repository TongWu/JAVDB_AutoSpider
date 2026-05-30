"""Repository for MovieRatings and ContentPreferences tables (ADR-022)."""

from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from javdb.storage.db import get_db, HISTORY_DB_PATH


class PreferenceRepo:
    """Typed wrapper over MovieRatings and ContentPreferences in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or HISTORY_DB_PATH

    # ------------------------------------------------------------------
    # MovieRatings
    # ------------------------------------------------------------------

    def upsert_rating(
        self,
        *,
        href: str,
        rating: Optional[int],
        tags: List[str],
        notes: Optional[str],
    ) -> dict:
        """UPSERT a movie rating. Returns the updated row as a dict."""
        video_code = re.sub(r'^/video/', '', href).strip('/')

        sql = """
            INSERT INTO MovieRatings
                (href, video_code, rating, tags, notes, rated_at, updated_at)
            VALUES (?, ?, ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(href) DO UPDATE SET
                rating     = excluded.rating,
                tags       = excluded.tags,
                notes      = excluded.notes,
                rated_at   = CASE WHEN excluded.rating IS NOT NULL
                                  THEN strftime('%Y-%m-%dT%H:%M:%fZ','now')
                                  ELSE rated_at END,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """
        with get_db(self._db_path) as conn:
            conn.execute(sql, (href, video_code, rating, json.dumps(tags), notes))
            row = conn.execute(
                "SELECT * FROM MovieRatings WHERE href = ?", (href,)
            ).fetchone()
        return dict(row)

    def get_rating(self, href: str) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM MovieRatings WHERE href = ?", (href,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_ratings(
        self, *, limit: int = 50, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count) for paginated listing."""
        with get_db(self._db_path) as conn:
            # Alias + key access: D1/Dual cursors return dict-shaped rows, so
            # positional ``fetchone()[0]`` would KeyError on the canonical backend.
            total = conn.execute(
                "SELECT COUNT(*) AS cnt FROM MovieRatings"
            ).fetchone()["cnt"]
            rows = conn.execute(
                "SELECT * FROM MovieRatings ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    # ------------------------------------------------------------------
    # ContentPreferences
    # ------------------------------------------------------------------

    def upsert_preference(
        self,
        *,
        content_type: str,
        content_id: str,
        content_name: str,
        hearted: bool,
        weight: float = 1.0,
    ) -> dict:
        """UPSERT a content preference. Returns the updated row as a dict."""
        sql = """
            INSERT INTO ContentPreferences
                (content_type, content_id, content_name, hearted, weight, updated_at)
            VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(content_type, content_id) DO UPDATE SET
                content_name = excluded.content_name,
                hearted      = excluded.hearted,
                weight       = excluded.weight,
                updated_at   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """
        with get_db(self._db_path) as conn:
            conn.execute(sql, (
                content_type, content_id, content_name,
                1 if hearted else 0, weight,
            ))
            row = conn.execute(
                "SELECT * FROM ContentPreferences WHERE content_type=? AND content_id=?",
                (content_type, content_id),
            ).fetchone()
        return dict(row)

    def get_preference(
        self, content_type: str, content_id: str
    ) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM ContentPreferences WHERE content_type=? AND content_id=?",
                (content_type, content_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_preferences(
        self,
        *,
        content_type: Optional[str] = None,
        hearted_only: bool = False,
    ) -> List[dict]:
        conditions: list[str] = []
        params: list = []
        if content_type:
            conditions.append("content_type = ?")
            params.append(content_type)
        if hearted_only:
            conditions.append("hearted = 1")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            f"SELECT * FROM ContentPreferences {where} "
            "ORDER BY content_type, content_name"
        )
        with get_db(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def is_actor_blocked(self, actor_href: str) -> bool:
        """True if the actor has an explicit hearted=0 ContentPreferences entry."""
        row = self.get_preference('actor', actor_href)
        return row is not None and row['hearted'] == 0
