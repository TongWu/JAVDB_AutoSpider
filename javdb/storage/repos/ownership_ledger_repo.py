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

    _UPSERT_SQL: str = ""

    @classmethod
    def _upsert_sql(cls) -> str:
        if not cls._UPSERT_SQL:
            placeholders = ", ".join(["?"] * len(_COLUMNS))
            columns = ", ".join(_COLUMNS)
            updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
            cls._UPSERT_SQL = (
                f"INSERT INTO OwnershipLedger ({columns}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(video_code, source, category) DO UPDATE SET {updates}"
            )
        return cls._UPSERT_SQL

    def upsert(self, record: OwnershipLedgerRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        self._conn.execute(self._upsert_sql(), values)

    def upsert_batch(self, records: Iterable[OwnershipLedgerRecord]) -> int:
        """Batch-upsert records using executemany. Returns count written."""
        params = [
            [getattr(r, column) for column in _COLUMNS] for r in records
        ]
        if not params:
            return 0
        self._conn.executemany(self._upsert_sql(), params)
        return len(params)

    def touch_observed_at_batch(
        self, keys: Iterable[tuple[str, str, str]], observed_at: str,
    ) -> int:
        """Batch-refresh observed_at for unchanged rows (ADR-033 D10 freshness).

        Each key is (video_code, source, category). Returns count touched."""
        params = [[observed_at, vc, src, cat] for vc, src, cat in keys]
        if not params:
            return 0
        self._conn.executemany(
            "UPDATE OwnershipLedger SET observed_at = ? "
            "WHERE video_code = ? AND source = ? AND category = ?",
            params,
        )
        return len(params)

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
        rows = self._conn.execute(
            "SELECT video_code, category FROM OwnershipLedger "
            "WHERE source = ? AND present = 1",
            [source],
        ).fetchall()
        to_sweep = [
            [row["video_code"], source, row["category"]]
            for row in rows
            if (row["video_code"], row["category"]) not in present
        ]
        if not to_sweep:
            return 0
        self._conn.executemany(
            "UPDATE OwnershipLedger SET present = 0 "
            "WHERE video_code = ? AND source = ? AND category = ?",
            to_sweep,
        )
        return len(to_sweep)

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
