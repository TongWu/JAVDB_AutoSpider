# javdb/storage/repos/parse_run_field_fill_repo.py
"""Repository for ADR-035 ParseRunFieldFill rows (reports DB)."""

from __future__ import annotations

import logging
import sqlite3
import statistics
from typing import Optional

from javdb.ops.sentinel.models import FieldFill, utc_now_iso

logger = logging.getLogger(__name__)


class ParseRunFieldFillRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except (AttributeError, TypeError):
            logger.debug("row_factory set failed", exc_info=True)

    def upsert_fills(self, session_id: str, fills: list[FieldFill]) -> None:
        now = utc_now_iso()
        self._conn.executemany(
            """
            INSERT INTO ParseRunFieldFill
              (session_id, page_type, field, fill_rate, sample_count, committed, observed_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(session_id, page_type, field) DO UPDATE SET
              fill_rate=excluded.fill_rate,
              sample_count=excluded.sample_count,
              observed_at=excluded.observed_at
            """,
            [(session_id, f.page_type, f.field, f.fill_rate, f.sample_count, now) for f in fills],
        )

    def get_fills(self, session_id: str) -> list[FieldFill]:
        rows = self._conn.execute(
            "SELECT page_type, field, fill_rate, sample_count "
            "FROM ParseRunFieldFill WHERE session_id = ?",
            [session_id],
        ).fetchall()
        return [FieldFill(r["page_type"], r["field"], r["fill_rate"], r["sample_count"]) for r in rows]

    def baseline(self, page_type: str, field: str, *, window: int) -> Optional[float]:
        rows = self._conn.execute(
            """
            SELECT fill_rate FROM ParseRunFieldFill
            WHERE page_type = ? AND field = ? AND committed = 1
            ORDER BY observed_at DESC LIMIT ?
            """,
            [page_type, field, window],
        ).fetchall()
        values = [r["fill_rate"] for r in rows]
        if not values:
            return None
        # fill_rate is a ratio in [0, 1]; round to tame IEEE-754 averaging
        # artifacts when median averages the two middle values of an even set.
        return round(statistics.median(values), 6)

    def mark_committed(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE ParseRunFieldFill SET committed = 1 WHERE session_id = ?",
            [session_id],
        )
