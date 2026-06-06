# javdb/storage/repos/ownership_ledger_repo.py
"""Repository for ADR-033 OwnershipLedger rows (operations DB)."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Optional

from javdb.ops.reconcile.models import OwnershipLedgerRecord

_COLUMNS = ("video_code", "source", "category", "path", "size", "present", "observed_at")
_PK = ("video_code", "source", "category")


def _row_to_record(row: Any) -> OwnershipLedgerRecord:
    return OwnershipLedgerRecord(**{column: row[column] for column in _COLUMNS})


class OwnershipLedgerRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: OwnershipLedgerRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
        self._conn.execute(
            f"""
            INSERT INTO OwnershipLedger ({columns})
            VALUES ({placeholders})
            ON CONFLICT(video_code, source, category) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, video_code: str, source: str, category: str) -> Optional[OwnershipLedgerRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OwnershipLedger "
            "WHERE video_code = ? AND source = ? AND category = ?",
            [video_code, source, category],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_by_source(self, source: str) -> list[OwnershipLedgerRecord]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OwnershipLedger WHERE source = ?",
            [source],
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def mark_absent(self, source: str, present_keys: Iterable[tuple[str, str]]) -> int:
        """Sweep prior rows of *source* whose (video_code, category) is absent from
        *present_keys* to present=0 (audit-preserving; never deletes). Returns the
        number of rows swept."""
        present = {(vc, cat) for vc, cat in present_keys}
        swept = 0
        rows = self._conn.execute(
            "SELECT video_code, category FROM OwnershipLedger "
            "WHERE source = ? AND present = 1",
            [source],
        ).fetchall()
        for row in rows:
            key = (row["video_code"], row["category"])
            if key in present:
                continue
            self._conn.execute(
                "UPDATE OwnershipLedger SET present = 0 "
                "WHERE video_code = ? AND source = ? AND category = ?",
                [row["video_code"], source, row["category"]],
            )
            swept += 1
        return swept

    def list_present_video_codes(self, sources: Iterable[str]) -> set[str]:
        """Distinct video_codes with present=1 in any of *sources*."""
        sources = tuple(sources)
        if not sources:
            return set()
        placeholders = ", ".join(["?"] * len(sources))
        rows = self._conn.execute(
            f"SELECT DISTINCT video_code FROM OwnershipLedger "
            f"WHERE present = 1 AND source IN ({placeholders})",
            list(sources),
        ).fetchall()
        return {r["video_code"] for r in rows}
