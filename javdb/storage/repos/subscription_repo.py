"""Repositories for the ActorSubscription + NewWorks tables (ADR-054 WS2)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from javdb.storage import db as _db
from javdb.storage.db import get_db

from javdb.storage.contract import fragments, order_params

# Single source of truth: the ADR-055 contract registry. Re-exported for any
# back-compat importers; the SQL itself lives only in javdb/storage/contract.
ACTOR_SUBSCRIPTION_UPSERT_SQL = fragments.ACTOR_SUBSCRIPTION_UPSERT.sql


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
                fragments.ACTOR_SUBSCRIPTION_UPSERT.sql,
                order_params(
                    fragments.ACTOR_SUBSCRIPTION_UPSERT,
                    actor_href=actor_href,
                    actor_name=actor_name,
                    active=active,
                ),
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
        """Insert a discovered work. Returns True iff a row was added.

        Keyed by the composite (actor_href, video_code) so the same release can
        persist once per followed actor (issue #223). ``INSERT OR IGNORE`` is
        used rather than an explicit ``ON CONFLICT(actor_href, video_code)``
        target so the write stays schema-tolerant during the D1-first migration
        window: a backend whose NewWorks still carries the old single-column
        ``video_code`` PK (D1 before 2026_06_16_*.sql, or a SQLite mirror before
        ``_ensure_newworks_composite_pk`` / ``--force-overwrite-all``) would make
        an explicit composite conflict target raise ``OperationalError``. ``OR
        IGNORE`` degrades to plain idempotency instead of crashing, and once the
        composite PK is in place it keeps cross-actor rows.
        """
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
        """Mark feed row(s) for ``video_code`` dismissed. Returns True if any
        row was updated.

        NOTE: dismissal is global per video_code — it clears the release from
        every followed actor's feed at once. Now that a release can occupy one
        row per actor (composite PK, issue #223), scoping dismissal to a single
        actor needs an ``actor_href`` argument and a matching API/route change
        (dual-backend, OpenAPI re-vendor); tracked as a follow-up.
        """
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE NewWorks SET dismissed = 1 WHERE video_code = ?", (video_code,)
            )
            return cur.rowcount > 0
