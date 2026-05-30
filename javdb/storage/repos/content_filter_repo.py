"""Repository for ContentFilterRule rows in reports.db."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping

from javdb.spider.services.content_filter import Rule

logger = logging.getLogger(__name__)


def _row_to_rule(row: Mapping[str, object]) -> Rule:
    return Rule(
        id=int(row["id"]),
        dimension=row["dimension"],
        mode=row["mode"],
        value=row["value"] or "",
        enabled=bool(int(row["enabled"] or 0)),
    )


class ContentFilterRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def load_rules(self) -> list[Rule]:
        rows = self._conn.execute(
            "SELECT id, dimension, mode, value, enabled FROM ContentFilterRule "
            "WHERE enabled = 1 ORDER BY id ASC",
        ).fetchall()
        return [_row_to_rule(row) for row in rows]

    def add_rule(self, dimension: str, mode: str, value: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO ContentFilterRule (dimension, mode, value, enabled) "
            "VALUES (?, ?, ?, 1)",
            [dimension, mode, value],
        )
        return int(cur.lastrowid)

    def list_rules(self) -> list[Rule]:
        rows = self._conn.execute(
            "SELECT id, dimension, mode, value, enabled FROM ContentFilterRule "
            "ORDER BY id ASC",
        ).fetchall()
        return [_row_to_rule(row) for row in rows]

    def remove_rule(self, rule_id: int) -> None:
        self._conn.execute(
            "DELETE FROM ContentFilterRule WHERE id = ?",
            [rule_id],
        )

    def set_enabled(self, rule_id: int, enabled: bool) -> None:
        self._conn.execute(
            "UPDATE ContentFilterRule SET enabled = ? WHERE id = ?",
            [1 if enabled else 0, rule_id],
        )
