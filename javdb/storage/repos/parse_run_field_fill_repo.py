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

    def baseline(self, page_type: str, field: str, *, window: int,
                 before: Optional[str] = None) -> Optional[float]:
        """Median committed fill-rate over the most recent ``window`` runs.

        ``before`` (an ISO ``observed_at``) restricts to runs strictly earlier than
        it, excluding the current run from its own baseline — the post-hoc drift
        surface uses this to reproduce the gate detector's pure-historical baseline
        (the run being judged is not part of the history). Omit it for the gate /
        canary paths, where the run under evaluation is not yet a committed row."""
        sql = (
            "SELECT fill_rate FROM ParseRunFieldFill "
            "WHERE page_type = ? AND field = ? AND committed = 1"
        )
        params: list = [page_type, field]
        if before is not None:
            sql += " AND observed_at < ?"
            params.append(before)
        sql += " ORDER BY observed_at DESC LIMIT ?"
        params.append(window)
        rows = self._conn.execute(sql, params).fetchall()
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

    def latest_committed_fills(self) -> list[tuple[str, str, float, int, str | None]]:
        """Newest committed fill per (page_type, field): the 'current health'.

        Rows: (page_type, field, fill_rate, sample_count, observed_at). Uncommitted
        rows and older runs are excluded; exactly one row per field — ties on
        ``observed_at`` are broken deterministically by ``session_id`` (the higher
        session_id wins) so the one-row-per-field contract holds even if two
        committed runs share a timestamp."""
        rows = self._conn.execute(
            """
            SELECT page_type, field, fill_rate, sample_count, observed_at
            FROM ParseRunFieldFill p
            WHERE p.committed = 1
              AND NOT EXISTS (
                SELECT 1 FROM ParseRunFieldFill q
                WHERE q.page_type = p.page_type AND q.field = p.field
                  AND q.committed = 1
                  AND (
                    COALESCE(q.observed_at, '') > COALESCE(p.observed_at, '')
                    OR (
                      COALESCE(q.observed_at, '') = COALESCE(p.observed_at, '')
                      AND q.session_id > p.session_id
                    )
                  )
              )
            ORDER BY page_type, field
            """
        ).fetchall()
        return [
            (r["page_type"], r["field"], r["fill_rate"], r["sample_count"], r["observed_at"])
            for r in rows
        ]
