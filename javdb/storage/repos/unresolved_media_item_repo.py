"""Repository for ADR-033 UnresolvedMediaItem rows (operations DB).

Media items whose video_code could not be resolved (ADR-033 D9). Keyed by
(instance, library_id, item_id) so re-observations dedupe instead of piling up."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import UnresolvedMediaItemRecord

_COLUMNS = (
    "instance", "source_type", "library_id", "library_name",
    "item_id", "raw_title", "file_path", "observed_at",
)
_PK = ("instance", "library_id", "item_id")


def _row_to_record(row: Any) -> UnresolvedMediaItemRecord:
    return UnresolvedMediaItemRecord(**{c: row[c] for c in _COLUMNS})


class UnresolvedMediaItemRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: UnresolvedMediaItemRecord) -> None:
        values = [getattr(record, c) for c in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        conflict = ", ".join(_PK)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
        self._conn.execute(
            f"""
            INSERT INTO UnresolvedMediaItem ({columns})
            VALUES ({placeholders})
            ON CONFLICT({conflict}) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, instance: str, library_id: str, item_id: str) -> Optional[UnresolvedMediaItemRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM UnresolvedMediaItem "
            f"WHERE instance = ? AND library_id = ? AND item_id = ?",
            [instance, library_id, item_id],
        ).fetchone()
        return None if row is None else _row_to_record(row)
