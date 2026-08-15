"""Repository for ADR-033 AcquisitionOutcome rows (operations DB)."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import (
    ACQUISITION_STATES,
    TERMINAL_STATES,
    AcquisitionOutcomeRecord,
    utc_now_iso,
)

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
    "state_changed_at",
)
_ACTIVE_STATES = tuple(
    state for state in ACQUISITION_STATES if state not in TERMINAL_STATES
)


def _upsert_assignment(column: str) -> str:
    if column in {"completed_at", "landed_at", "state_changed_at"}:
        # Never let a caller that did not supply these blank out a recorded
        # timestamp: state_changed_at in particular is written once per
        # transition, and passes that observe no change carry it through as-is.
        return f"{column}=COALESCE(excluded.{column}, AcquisitionOutcome.{column})"
    return f"{column}=excluded.{column}"


def _row_to_record(row: Any) -> AcquisitionOutcomeRecord:
    return AcquisitionOutcomeRecord(**{column: row[column] for column in _COLUMNS})


class AcquisitionOutcomeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: AcquisitionOutcomeRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        updates = ", ".join(_upsert_assignment(column) for column in _COLUMNS if column != "qb_hash")
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
        state_changed_at: Optional[str] = None,
    ) -> None:
        """Update state in place, inserting a minimal row when qb_hash is new.

        Both timestamps only advance when the state actually changes. The caller
        names a target state, not necessarily a transition: the hourly
        acquisition pass can already have marked a hash 'completed' before the
        daily PikPak cleanup hands the same hash to apply_cleanup_completed().
        Stamping unconditionally would drag that row from the real completion
        time to the cleanup time — the exact confusion state_changed_at exists to
        remove. ``completed_at`` needs the same guard: its plain COALESCE only
        stopped a NULL from erasing a value, not a fresh timestamp from
        overwriting one on a repeat report. Both ELSE branches still COALESCE, so
        a row that predates the column gets a value backfilled.
        """
        now = utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO AcquisitionOutcome
                (qb_hash, href, state, completed_at, last_seen_at, state_changed_at)
            VALUES (?, '', ?, ?, ?, ?)
            ON CONFLICT(qb_hash) DO UPDATE SET
              state=excluded.state,
              completed_at=CASE
                  WHEN AcquisitionOutcome.state IS NOT excluded.state
                      THEN COALESCE(
                          excluded.completed_at, AcquisitionOutcome.completed_at)
                  ELSE COALESCE(
                      AcquisitionOutcome.completed_at, excluded.completed_at)
              END,
              last_seen_at=excluded.last_seen_at,
              state_changed_at=CASE
                  WHEN AcquisitionOutcome.state IS NOT excluded.state
                      THEN excluded.state_changed_at
                  ELSE COALESCE(
                      AcquisitionOutcome.state_changed_at, excluded.state_changed_at)
              END
            """,
            [
                qb_hash,
                state,
                completed_at,
                last_seen_at or now,
                state_changed_at or now,
            ],
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

    def list_pending_landing(
        self, states: tuple[str, ...] = ("queued", "downloading", "completed"),
    ) -> list[AcquisitionOutcomeRecord]:
        """Rows whose video_code may still be promoted to in_library.

        Excludes 'failed' (left untouched, D-P2-8) and 'in_library' (already
        landed). Uses the indexed video_code column downstream."""
        placeholders = ", ".join(["?"] * len(states))
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM AcquisitionOutcome "
            f"WHERE state IN ({placeholders})",
            list(states),
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    # Promotion is idempotent: re-running the landing pass over a row that is
    # already 'in_library' must not move landed_at or state_changed_at to the
    # retry time — same guard as mark_state, for the same reason. COALESCE on the
    # unchanged branch still backfills a row that predates the column.
    _MARK_IN_LIBRARY_SQL = (
        "UPDATE AcquisitionOutcome SET "
        "landed_at = CASE WHEN state IS NOT 'in_library' "
        "THEN ? ELSE COALESCE(landed_at, ?) END, "
        "state_changed_at = CASE WHEN state IS NOT 'in_library' "
        "THEN ? ELSE COALESCE(state_changed_at, ?) END, "
        "state = 'in_library' "
        "WHERE qb_hash = ?"
    )

    def mark_in_library(self, qb_hash: str, landed_at: str) -> None:
        self._conn.execute(
            self._MARK_IN_LIBRARY_SQL,
            [landed_at, landed_at, landed_at, landed_at, qb_hash],
        )

    def mark_in_library_batch(self, qb_hashes: list[str], landed_at: str) -> int:
        """Batch-promote rows to in_library. Returns count updated."""
        if not qb_hashes:
            return 0
        params = [[landed_at, landed_at, landed_at, landed_at, h] for h in qb_hashes]
        self._conn.executemany(self._MARK_IN_LIBRARY_SQL, params)
        return len(params)
