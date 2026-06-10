"""Repositories for the ADR-036 event spine (reports DB)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only; importing it at module load would create a
    # storage -> javdb.pipeline.events -> storage import cycle (events/__init__
    # eagerly imports store + consumer, both of which import this module).
    from javdb.pipeline.events.models import PipelineEventRecord

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

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
        created = record.created_at or _utc_now_iso()
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
        from javdb.pipeline.events.models import PipelineEventRecord
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
            [consumer, last_seq, _utc_now_iso()],
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


class AcquisitionOutcomeShadowRepo:
    """Shadow projection repo for AcquisitionOutcomeShadow (ADR-036 Phase 2).

    Populated by TorrentQueued and TorrentCompleted events.
    Cross-validation use only — never read by production decisions.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def upsert_queued(
        self,
        qb_hash: str,
        href: str,
        video_code: str | None,
        category: str | None,
        queued_at: str | None,
        session_id: str | None,
    ) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            "INSERT INTO AcquisitionOutcomeShadow "
            "(qb_hash, href, video_code, category, state, queued_at, session_id, updated_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?) "
            "ON CONFLICT(qb_hash) DO UPDATE SET "
            "href=excluded.href, video_code=excluded.video_code, "
            "category=excluded.category, state='queued', "
            "queued_at=excluded.queued_at, session_id=excluded.session_id, "
            "updated_at=excluded.updated_at",
            [qb_hash, href or "", video_code, category, queued_at, session_id, now],
        )

    def mark_completed(self, qb_hash: str, completed_at: str | None) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            "UPDATE AcquisitionOutcomeShadow "
            "SET state='completed', completed_at=?, updated_at=? "
            "WHERE qb_hash=?",
            [completed_at or now, now, qb_hash],
        )

    def get(self, qb_hash: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT qb_hash, href, video_code, category, state, "
            "queued_at, completed_at, session_id, updated_at "
            "FROM AcquisitionOutcomeShadow WHERE qb_hash=?",
            [qb_hash],
        ).fetchone()

    def list_all(self) -> list:
        return self._conn.execute(
            "SELECT qb_hash, href, video_code, category, state, "
            "queued_at, completed_at, session_id, updated_at "
            "FROM AcquisitionOutcomeShadow"
        ).fetchall()

    def reset(self) -> None:
        self._conn.execute("DELETE FROM AcquisitionOutcomeShadow")
