"""Static SQL fragment registry (ADR-055). The single hand-edited source.

Each entry is mirrored to the TS Worker by apps/cli/ops/dump_sql_contract.py.
Add new static cross-backend mutations / static selects here, never by hand in
the other repo.
"""
from __future__ import annotations

from javdb.storage.contract.types import Param, SqlFragment

WATCH_INTENT_UPSERT = SqlFragment(
    name="watch_intent_upsert",
    db="history",
    sql="""
    INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at)
    VALUES (?, ?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(video_code) DO UPDATE SET
        href       = excluded.href,
        status     = excluded.status,
        notes      = COALESCE(excluded.notes, notes),
        status_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
""",
    params=(
        Param("video_code", "str", "string"),
        Param("href", "str", "string"),
        Param("status", "str", "string"),
        Param("notes", "str | None", "string | null"),
    ),
)

FRAGMENTS: tuple[SqlFragment, ...] = (WATCH_INTENT_UPSERT,)
