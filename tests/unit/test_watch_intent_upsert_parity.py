"""Pin the WatchIntent UPSERT SQL so it cannot drift from the TS Worker (ADR-054)."""

import re

from javdb.storage.repos.watchlist_repo import WATCH_INTENT_UPSERT_SQL

# The single canonical UPSERT shape both backends must emit (whitespace-collapsed).
CANONICAL = (
    "INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at) "
    "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
    "ON CONFLICT(video_code) DO UPDATE SET "
    "href = excluded.href, status = excluded.status, notes = excluded.notes, "
    "status_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_python_upsert_matches_canonical():
    assert _norm(WATCH_INTENT_UPSERT_SQL) == CANONICAL
