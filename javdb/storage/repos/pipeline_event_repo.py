"""Repositories for the ADR-036 event spine (reports DB)."""

from __future__ import annotations

import logging
import sqlite3

from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso

logger = logging.getLogger(__name__)

_EVENT_COLS = ("session_id", "run_id", "run_attempt", "event_type",
               "entity_type", "entity_id", "payload", "created_at")


class PipelineEventRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def append(self, record: PipelineEventRecord) -> int:
        created = record.created_at or utc_now_iso()
        cur = self._conn.execute(
            f"INSERT INTO PipelineEvent ({', '.join(_EVENT_COLS)}) "
            f"VALUES ({', '.join(['?'] * len(_EVENT_COLS))})",
            [record.session_id, record.run_id, record.run_attempt, record.event_type,
             record.entity_type, record.entity_id, record.payload, created],
        )
        return int(cur.lastrowid)

    def read_since(self, last_seq: int, *, limit: int) -> list[PipelineEventRecord]:
        rows = self._conn.execute(
            "SELECT seq, session_id, run_id, run_attempt, event_type, entity_type, "
            "entity_id, payload, created_at FROM PipelineEvent "
            "WHERE seq > ? ORDER BY seq ASC LIMIT ?",
            [last_seq, limit],
        ).fetchall()
        return [
            PipelineEventRecord(
                event_type=r["event_type"], session_id=r["session_id"],
                entity_type=r["entity_type"], entity_id=r["entity_id"],
                payload=r["payload"], run_id=r["run_id"], run_attempt=r["run_attempt"],
                seq=r["seq"], created_at=r["created_at"],
            ) for r in rows
        ]

    def get_cursor(self, consumer: str) -> int:
        row = self._conn.execute(
            "SELECT last_seq FROM EventConsumerCursor WHERE consumer = ?", [consumer],
        ).fetchone()
        return 0 if row is None else int(row["last_seq"])

    def advance_cursor(self, consumer: str, last_seq: int) -> None:
        self._conn.execute(
            "INSERT INTO EventConsumerCursor (consumer, last_seq, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(consumer) DO UPDATE SET "
            "last_seq=excluded.last_seq, updated_at=excluded.updated_at",
            [consumer, last_seq, utc_now_iso()],
        )


class RunEventSummaryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def bump(self, session_id: str, event_type: str, n: int = 1) -> None:
        self._conn.execute(
            "INSERT INTO RunEventSummary (session_id, event_type, count) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id, event_type) DO UPDATE SET count = count + excluded.count",
            [session_id, event_type, n],
        )

    def reset(self) -> None:
        self._conn.execute("DELETE FROM RunEventSummary")

    def get(self, session_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT event_type, count FROM RunEventSummary WHERE session_id = ?",
            [session_id],
        ).fetchall()
        return {r["event_type"]: r["count"] for r in rows}
