"""Repositories for the ActorSubscription + NewWorks tables (ADR-054 WS2)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from javdb.storage import db as _db
from javdb.storage.db import get_db

# Byte-mirrored with server/services/subscription-service.ts
# ACTOR_SUBSCRIPTION_UPSERT_SQL (ADR-017 dual-backend parity). Pinned by
# tests/unit/test_actor_subscription_upsert_parity.py.
#
# created_at is preserved on conflict; active / actor_name / updated_at are
# refreshed. Cursor columns are advanced separately by the monitor and must not
# be clobbered by a follow/unfollow upsert.
ACTOR_SUBSCRIPTION_UPSERT_SQL = """
    INSERT INTO ActorSubscription
        (actor_href, actor_name, active, created_at, updated_at)
    VALUES (?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(actor_href) DO UPDATE SET
        actor_name = excluded.actor_name,
        active     = excluded.active,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
"""


class ActorSubscriptionRepo:
    """Typed wrapper over the ActorSubscription table in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        # Resolve HISTORY_DB_PATH at construction via ``_db`` (not bound at
        # import) so pytest's path monkeypatch is honoured (BFR-016).
        self._db_path = db_path or _db.HISTORY_DB_PATH

    def upsert(
        self, *, actor_href: str, actor_name: Optional[str] = None, active: int = 1
    ) -> dict:
        """Follow or update an actor subscription. Returns the row as a dict."""
        with get_db(self._db_path) as conn:
            conn.execute(
                ACTOR_SUBSCRIPTION_UPSERT_SQL, (actor_href, actor_name, active)
            )
            row = conn.execute(
                "SELECT * FROM ActorSubscription WHERE actor_href = ?", (actor_href,)
            ).fetchone()
        return dict(row)

    def get(self, actor_href: str) -> Optional[dict]:
        """Return one subscription row, or None when absent."""
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM ActorSubscription WHERE actor_href = ?", (actor_href,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list(
        self, *, active_only: bool = False, limit: int = 200, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count) for paginated listing."""
        where = "WHERE active = 1" if active_only else ""
        with get_db(self._db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM ActorSubscription {where}",  # noqa: S608
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"SELECT * FROM ActorSubscription {where} "  # noqa: S608
                "ORDER BY updated_at DESC, actor_href ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def list_active_hrefs(self) -> List[str]:
        """Ordered actor hrefs the subscription monitor should scrape."""
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                "SELECT actor_href FROM ActorSubscription WHERE active = 1 "
                "ORDER BY actor_href ASC"
            ).fetchall()
        return [r["actor_href"] for r in rows]

    def advance_cursor(self, actor_href: str, *, last_seen_href: Optional[str]) -> None:
        """Record the newest href seen and stamp last_checked_at after a scrape."""
        with get_db(self._db_path) as conn:
            conn.execute(
                "UPDATE ActorSubscription SET "
                "last_seen_href = COALESCE(?, last_seen_href), "
                "last_checked_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE actor_href = ?",
                (last_seen_href, actor_href),
            )

    def delete(self, actor_href: str) -> bool:
        """Unfollow an actor. Returns True if a row was removed."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM ActorSubscription WHERE actor_href = ?", (actor_href,)
            )
            return cur.rowcount > 0


class NewWorksRepo:
    """Typed wrapper over the NewWorks feed table in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _db.HISTORY_DB_PATH

    def add(
        self,
        *,
        video_code: str,
        href: str,
        actor_href: str,
        title: Optional[str] = None,
        release_date: Optional[str] = None,
    ) -> bool:
        """Insert a discovered work. Returns True iff a row was added."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO NewWorks "
                "(video_code, href, actor_href, title, release_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (video_code, href, actor_href, title, release_date),
            )
            return cur.rowcount > 0

    def list(
        self,
        *,
        actor_href: Optional[str] = None,
        include_dismissed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count). Excludes dismissed rows by default."""
        clauses: list[str] = []
        params: list[str] = []
        if not include_dismissed:
            clauses.append("dismissed = 0")
        if actor_href:
            clauses.append("actor_href = ?")
            params.append(actor_href)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with get_db(self._db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM NewWorks {where}",  # noqa: S608
                params,
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"SELECT * FROM NewWorks {where} "  # noqa: S608
                "ORDER BY discovered_at DESC, video_code ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def dismiss(self, video_code: str) -> bool:
        """Mark a feed row dismissed. Returns True if a row was updated."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE NewWorks SET dismissed = 1 WHERE video_code = ?", (video_code,)
            )
            return cur.rowcount > 0
