"""Dual-write SQLite + Cloudflare D1 connection facade.

Used for the parallel-test phase of the SQLite → D1 migration:

* Every **write** (``INSERT``/``UPDATE``/``DELETE``/``REPLACE``/DDL) is
  executed against the local SQLite file **and** the corresponding D1
  database.  SQLite is the canonical source for ``cursor.lastrowid`` and
  ``total_changes`` because the existing codebase relies on the IDs it
  generates for follow-up inserts.
* Every **read** (``SELECT`` / ``WITH ... SELECT`` / ``PRAGMA``) is routed
  to D1 only — proving that D1 already serves the application's read path
  before we cut over.

Failures on the D1 side are logged but do not raise, so the SQLite-backed
pipeline keeps running even if the D1 mirror is temporarily unavailable.

Drift tracking
--------------
Each :class:`DualConnection` keeps a per-transaction counter of D1 write
failures. On ``commit()`` (or ``rollback()``), if any D1 failures occurred,
a structured record is appended to ``reports/d1_drift.jsonl`` so an out-of-
band reconciliation job can later inspect what diverged.

To avoid log floods, repeated failures of the same SQL template within a
short LRU window are downgraded from WARNING to DEBUG.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_READ_PREFIX_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*(SELECT|WITH|PRAGMA|EXPLAIN)\b",
    re.IGNORECASE | re.DOTALL,
)


def _is_read(sql: str) -> bool:
    return bool(_READ_PREFIX_RE.match(sql))


# ── Drift log file ──────────────────────────────────────────────────────
# Resolved lazily so tests can monkeypatch the module-level constant.
_DRIFT_LOG_PATH = os.path.join(
    os.environ.get("REPORTS_DIR", "reports"), "d1_drift.jsonl"
)
_DRIFT_LOG_LOCK = threading.Lock()


def _append_drift_record(record: dict) -> None:
    """Append a JSON line to the drift log; never raises."""
    try:
        os.makedirs(os.path.dirname(_DRIFT_LOG_PATH) or ".", exist_ok=True)
        with _DRIFT_LOG_LOCK:
            with open(_DRIFT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — last-resort safeguard
        logger.error("Failed to append d1 drift record: %s", exc)


# Strip string/numeric literals to bucket "same statement" log spam.
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|\b\d+\b|\?")
_WS_RE = re.compile(r"\s+")
_RECENT_FAILURE_LRU_MAX = 32


def _signature(sql: str) -> str:
    s = _LITERAL_RE.sub("?", sql)
    s = _WS_RE.sub(" ", s).strip()
    return s[:200]


class DualCursor:
    """Cursor wrapper exposing SQLite values as the canonical result."""

    __slots__ = ("_sqlite_cur", "_d1_cur", "lastrowid", "rowcount")

    def __init__(self, sqlite_cur, d1_cur):
        self._sqlite_cur = sqlite_cur
        self._d1_cur = d1_cur
        self.lastrowid = (
            sqlite_cur.lastrowid if sqlite_cur is not None else getattr(d1_cur, "lastrowid", None)
        )
        self.rowcount = (
            sqlite_cur.rowcount if sqlite_cur is not None else getattr(d1_cur, "rowcount", -1)
        )

    def fetchone(self):
        if self._d1_cur is not None:
            return self._d1_cur.fetchone()
        return self._sqlite_cur.fetchone() if self._sqlite_cur is not None else None

    def fetchall(self):
        if self._d1_cur is not None:
            return self._d1_cur.fetchall()
        return self._sqlite_cur.fetchall() if self._sqlite_cur is not None else []

    def __iter__(self):
        if self._d1_cur is not None:
            return iter(self._d1_cur)
        return iter(self._sqlite_cur) if self._sqlite_cur is not None else iter(())


class DualConnection:
    """Facade that writes to SQLite + D1, reads from D1.

    Drop-in replacement for ``sqlite3.Connection`` for the JAVDB codebase.
    """

    def __init__(self, sqlite_conn: sqlite3.Connection, d1_conn, logical_name: str = "unknown"):
        self._sqlite = sqlite_conn
        self._d1 = d1_conn
        self._logical_name = logical_name
        self.row_factory = sqlite_conn.row_factory
        # Per-transaction drift state.
        self._d1_failure_count = 0
        self._d1_failure_first_sql: Optional[str] = None
        self._d1_failure_first_error: Optional[str] = None
        self._recent_failure_signatures: "collections.OrderedDict[str, int]" = (
            collections.OrderedDict()
        )

    # ── Statement execution ─────────────────────────────────────────────

    def execute(self, sql: str, params: Iterable[Any] = ()):
        if _is_read(sql):
            try:
                return self._d1.execute(sql, params)
            except Exception as exc:
                self._record_d1_failure(sql, exc, kind="read")
                return self._sqlite.execute(sql, params)

        sqlite_cur = self._sqlite.execute(sql, params)
        d1_cur = None
        try:
            d1_cur = self._d1.execute(sql, params)
            self._maybe_warn_id_drift(sqlite_cur, d1_cur, sql)
        except Exception as exc:
            self._record_d1_failure(sql, exc, kind="write")
        return DualCursor(sqlite_cur, d1_cur)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        seq_list = list(seq_of_params)
        self._sqlite.executemany(sql, seq_list)
        try:
            self._d1.executemany(sql, seq_list)
        except Exception as exc:
            self._record_d1_failure(sql, exc, kind="executemany")

    def executescript(self, script: str):
        self._sqlite.executescript(script)
        try:
            self._d1.executescript(script)
        except Exception as exc:
            self._record_d1_failure(script, exc, kind="executescript")

    # ── Transaction & lifecycle ─────────────────────────────────────────

    def commit(self):
        self._sqlite.commit()
        try:
            self._d1.commit()
        except Exception as exc:
            self._record_d1_failure("COMMIT", exc, kind="commit")
        self._flush_drift_record(committed=True)

    def rollback(self):
        self._sqlite.rollback()
        logger.warning(
            "DualConnection.rollback(): SQLite rolled back; D1 cannot truly "
            "roll back per-statement auto-commits — drift may have been introduced."
        )
        self._flush_drift_record(committed=False)

    def close(self):
        try:
            self._sqlite.close()
        finally:
            try:
                self._d1.close()
            except Exception:
                pass

    @property
    def total_changes(self) -> int:
        return self._sqlite.total_changes

    # ── Diagnostics ─────────────────────────────────────────────────────

    def _record_d1_failure(self, sql: str, exc: Exception, *, kind: str) -> None:
        self._d1_failure_count += 1
        if self._d1_failure_first_sql is None:
            self._d1_failure_first_sql = _shorten(sql, 200)
            self._d1_failure_first_error = f"{type(exc).__name__}: {exc}"

        sig = _signature(sql)
        prior = self._recent_failure_signatures.get(sig, 0)
        self._recent_failure_signatures[sig] = prior + 1
        # Keep LRU bounded.
        self._recent_failure_signatures.move_to_end(sig)
        while len(self._recent_failure_signatures) > _RECENT_FAILURE_LRU_MAX:
            self._recent_failure_signatures.popitem(last=False)

        if prior >= 1:
            logger.debug(
                "D1 %s failed (repeat #%d for this signature): %s | sql=%s",
                kind, prior + 1, exc, _shorten(sql),
            )
        else:
            logger.warning(
                "D1 %s failed (SQLite still applied): %s | sql=%s",
                kind, exc, _shorten(sql),
            )

    def _flush_drift_record(self, *, committed: bool) -> None:
        if self._d1_failure_count <= 0:
            self._reset_failure_state()
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "db": self._logical_name,
            "committed": committed,
            "failure_count": self._d1_failure_count,
            "first_failed_sql": self._d1_failure_first_sql,
            "first_error": self._d1_failure_first_error,
        }
        _append_drift_record(record)
        logger.error(
            "Transaction %s with %d D1 write failure(s) on db=%s; drift logged to %s",
            "committed" if committed else "rolled back",
            self._d1_failure_count, self._logical_name, _DRIFT_LOG_PATH,
        )
        self._reset_failure_state()

    def _reset_failure_state(self) -> None:
        self._d1_failure_count = 0
        self._d1_failure_first_sql = None
        self._d1_failure_first_error = None
        self._recent_failure_signatures.clear()

    @staticmethod
    def _maybe_warn_id_drift(sqlite_cur, d1_cur, sql: str) -> None:
        """Log a warning if AUTOINCREMENT IDs diverged between SQLite and D1."""
        if sqlite_cur is None or d1_cur is None:
            return
        s_id = getattr(sqlite_cur, "lastrowid", None)
        d_id = getattr(d1_cur, "lastrowid", None)
        if s_id is None or d_id is None or s_id == 0 or d_id == 0:
            return
        if s_id != d_id:
            logger.warning(
                "Dual-write ID drift: sqlite.lastrowid=%s vs d1.lastrowid=%s | sql=%s",
                s_id,
                d_id,
                _shorten(sql),
            )


def _shorten(sql: str, n: int = 100) -> str:
    s = " ".join(sql.split())
    return s[:n] + ("…" if len(s) > n else "")
