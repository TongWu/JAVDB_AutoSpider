"""Repository for ADR-033 AcquisitionOutcome rows (operations DB)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord, utc_now_iso

logger = logging.getLogger(__name__)

_COLUMNS = (
    "qb_hash",
    "href",
    "video_code",
    "category",
    "state",
    "queued_at",
    "completed_at",
    "landed_at",
    "last_seen_at",
    "session_id",
)
_ACTIVE_STATES = ("queued", "downloading")


def _row_to_record(row: Any) -> AcquisitionOutcomeRecord:
    return AcquisitionOutcomeRecord(**{column: row[column] for column in _COLUMNS})


class AcquisitionOutcomeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def upsert(self, record: AcquisitionOutcomeRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        updates = ", ".join(f"{column}=excluded.{column}" for column in _COLUMNS if column != "qb_hash")
        self._conn.execute(
            f"""
            INSERT INTO AcquisitionOutcome ({columns})
            VALUES ({placeholders})
            ON CONFLICT(qb_hash) DO UPDATE SET {updates}
            """,
            values,
        )

    def mark_state(
        self,
        qb_hash: str,
        state: str,
        *,
        completed_at: Optional[str] = None,
        last_seen_at: Optional[str] = None,
    ) -> None:
        """Update state in place, inserting a minimal row when qb_hash is new."""
        self._conn.execute(
            """
            INSERT INTO AcquisitionOutcome (qb_hash, href, state, completed_at, last_seen_at)
            VALUES (?, '', ?, ?, ?)
            ON CONFLICT(qb_hash) DO UPDATE SET
              state=excluded.state,
              completed_at=COALESCE(excluded.completed_at, AcquisitionOutcome.completed_at),
              last_seen_at=excluded.last_seen_at
            """,
            [qb_hash, state, completed_at, last_seen_at or utc_now_iso()],
        )

    def get(self, qb_hash: str) -> AcquisitionOutcomeRecord | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM AcquisitionOutcome WHERE qb_hash = ?",
            [qb_hash],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_active(self) -> list[AcquisitionOutcomeRecord]:
        placeholders = ", ".join(["?"] * len(_ACTIVE_STATES))
        rows = self._conn.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM AcquisitionOutcome
            WHERE state IN ({placeholders})
            """,
            list(_ACTIVE_STATES),
        ).fetchall()
        return [_row_to_record(row) for row in rows]
