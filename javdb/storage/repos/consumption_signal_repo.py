"""Repository for ADR-033 ConsumptionSignal rows (operations DB).

Full-replace UPSERT keyed by (video_code, instance, library_id): the latest
observation per source wins (ADR-033 D-P3-7). Cross-instance / cross-library
rows are naturally distinct, preserving provenance (ADR-033 D8)."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import ConsumptionSignalRecord

_COLUMNS = (
    "video_code", "source_type", "instance", "library_id", "library_name",
    "watched", "progress_pct", "play_count", "rating", "watched_at",
    "resolved_confidence", "observed_at",
)
_PK = ("video_code", "instance", "library_id")


def _row_to_record(row: Any) -> ConsumptionSignalRecord:
    d = {c: row[c] for c in _COLUMNS}
    if d["watched"] is not None:
        d["watched"] = bool(d["watched"])
    return ConsumptionSignalRecord(**d)


class ConsumptionSignalRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: ConsumptionSignalRecord) -> None:
        values = [
            int(v) if isinstance(v := getattr(record, c), bool) else v
            for c in _COLUMNS
        ]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        conflict = ", ".join(_PK)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
        self._conn.execute(
            f"""
            INSERT INTO ConsumptionSignal ({columns})
            VALUES ({placeholders})
            ON CONFLICT({conflict}) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, video_code: str, instance: str, library_id: str) -> Optional[ConsumptionSignalRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ConsumptionSignal "
            f"WHERE video_code = ? AND instance = ? AND library_id = ?",
            [video_code, instance, library_id],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_by_video_code(self, video_code: str) -> list[ConsumptionSignalRecord]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ConsumptionSignal WHERE video_code = ?",
            [video_code],
        ).fetchall()
        return [_row_to_record(r) for r in rows]
