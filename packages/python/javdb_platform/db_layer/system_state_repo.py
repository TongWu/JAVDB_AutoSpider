from __future__ import annotations

import json
import sqlite3
from typing import Any


class SystemStateRepo:
    """Generic KV against the `system_state` table in operations.db.

    Used by:
      - onboarded flag
      - dismissed_hints array
      - any other client-side preference that needs to survive between
        sessions / multiple devices.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, *, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default

    def put(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO system_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                            updated_at = datetime('now')
            """,
            (key, value),
        )
        self._conn.commit()

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM system_state WHERE key = ?", (key,))
        self._conn.commit()

    def get_json(self, key: str, *, default: Any = None) -> Any:
        raw = self.get(key)
        return json.loads(raw) if raw is not None else default

    def put_json(self, key: str, value: Any) -> None:
        self.put(key, json.dumps(value))
