from __future__ import annotations

import json
from typing import Any


class SystemStateRepo:
    """Generic KV against the `system_state` table in operations.db.

    Used by:
      - onboarded flag
      - dismissed_hints array
      - any other client-side preference that needs to survive between
        sessions / multiple devices.

    Accepts any duck-typed connection (sqlite3.Connection, D1Connection,
    DualConnection) — backend dispatch is handled by get_db() at call site.
    Callers must not call conn.commit() themselves; get_db() auto-commits on
    context exit. Explicit commit() calls are intentionally absent here to
    avoid mid-transaction drift-log flushes under DualConnection.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get(self, key: str, *, default: str | None = None) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        if row is None:
            return default
        # D1 returns dicts; sqlite3.Row supports both name and index access
        try:
            return row["value"]
        except (KeyError, TypeError, IndexError):
            return row[0]

    def put(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO system_state (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                            updated_at = datetime('now')
            """,
            (key, value),
        )

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM system_state WHERE key = ?", (key,))

    def get_json(self, key: str, *, default: Any = None) -> Any:
        raw = self.get(key)
        return json.loads(raw) if raw is not None else default

    def put_json(self, key: str, value: Any) -> None:
        self.put(key, json.dumps(value))
