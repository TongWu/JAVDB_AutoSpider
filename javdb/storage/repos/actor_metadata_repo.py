# javdb/storage/repos/actor_metadata_repo.py
"""Repository for ActorMetadata rows (history DB) — ADR-040 Phase 2 age cache."""

from __future__ import annotations

import sqlite3

_UPSERT_SQL = """
INSERT INTO ActorMetadata
    (actor_href, actor_name, birthdate, source, source_url, resolved,
     created_at, updated_at)
VALUES
    (?, ?, ?, ?, ?, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ','now'),
     strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(actor_href) DO UPDATE SET
    actor_name = excluded.actor_name,
    birthdate  = excluded.birthdate,
    source     = excluded.source,
    source_url = excluded.source_url,
    resolved   = 1,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
"""


class ActorMetadataRepo:
    """Thin typed wrapper over ActorMetadata. Takes a live connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def get(self, actor_href: str) -> dict | None:
        row = self._conn.execute(
            "SELECT actor_href, actor_name, birthdate, source, source_url, resolved "
            "FROM ActorMetadata WHERE actor_href = ?",
            (actor_href,),
        ).fetchone()
        return dict(row) if row is not None else None

    def upsert(self, actor_href: str, actor_name: str, birthdate: str | None,
               source: str, source_url: str) -> None:
        self._conn.execute(
            _UPSERT_SQL, (actor_href, actor_name, birthdate, source, source_url)
        )

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT actor_href, actor_name, birthdate, source, source_url, resolved "
            "FROM ActorMetadata ORDER BY actor_href"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, actor_href: str) -> None:
        self._conn.execute(
            "DELETE FROM ActorMetadata WHERE actor_href = ?", (actor_href,)
        )
