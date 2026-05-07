"""Dual-write SQLite + Cloudflare D1 connection facade.

Used for the parallel-test phase of the SQLite → D1 migration:

* Every **write** (``INSERT``/``UPDATE``/``DELETE``/``REPLACE``/DDL) is
  executed against the local SQLite file **and** the corresponding D1
  database.  SQLite is the canonical source for ``cursor.lastrowid`` and
  ``total_changes`` because the existing codebase relies on the IDs it
  generates for follow-up inserts.
* Every **read** (``SELECT`` / ``PRAGMA``) is routed
  to D1 only — proving that D1 already serves the application's read path
  before we cut over.

Failures on the D1 side are logged but do not raise, so the SQLite-backed
pipeline keeps running even if the D1 mirror is temporarily unavailable.

Drift tracking
--------------
Each :class:`DualConnection` keeps two per-transaction counters that
together cover both ways SQLite and D1 can diverge:

* ``_d1_failure_count`` — number of D1 write failures (caller's SQL
  applied locally but the mirrored D1 statement raised).
* ``_d1_uncommitted_writes`` — number of D1 writes that succeeded since
  the last ``commit()``/``rollback()``.  Because D1 auto-commits every
  HTTP statement, a SQLite ``rollback()`` with a non-zero count means
  D1 still holds rows SQLite has just discarded — also drift.

On ``commit()`` (or ``rollback()``), if either counter signals divergence,
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
from typing import Any, Iterable, Optional, Sequence, Tuple

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_READ_KEYWORDS = ("SELECT", "PRAGMA", "EXPLAIN")


def _is_read(sql: str) -> bool:
    # Linear scan that skips leading whitespace, ``--`` line comments, and
    # ``/* ... */`` block comments before matching a read-only keyword.
    #
    # Implemented without regex on purpose: a pattern like
    # ``(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*`` exhibits catastrophic backtracking
    # on inputs that begin with ``/`` followed by many ``/*`` fragments
    # (classic ReDoS), because the engine retries every alternation split.
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            nl = sql.find("\n", i + 2)
            if nl == -1:
                return False
            i = nl + 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                return False
            i = end + 2
            continue
        break

    tail = sql[i : i + 8].upper()
    for kw in _READ_KEYWORDS:
        if tail.startswith(kw):
            after = i + len(kw)
            if after >= n or not (sql[after].isalnum() or sql[after] == "_"):
                return True
    return False


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


# ── ID drift delta tracking ──────────────────────────────────────────────
# AUTOINCREMENT counters between SQLite and D1 typically diverge once early
# in the migration and then stay offset by a constant delta forever. A naive
# per-row warning floods logs with noise that says nothing new.
#
# We instead remember the last observed ``(d1_lastrowid - sqlite_lastrowid)``
# delta per table (process-wide) and only emit:
#   • one INFO line the first time a table's delta is seen (the baseline);
#   • a WARNING when the delta *changes* (a real new asymmetric INSERT — i.e.
#     one side committed a row the other did not, since this run started);
#   • DEBUG for the unchanged steady-state case.
#
# State is keyed by table name, lives for the lifetime of the process, and
# is guarded by a lock since DualConnection is used from multiple threads.
_INSERT_TABLE_RE = re.compile(
    r"^\s*(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+[\"`']?([\w.]+)[\"`']?",
    re.IGNORECASE,
)
_ID_DELTA_LOCK = threading.Lock()
_ID_DELTA_BY_TABLE: "dict[str, int]" = {}


def _extract_insert_table(sql: str) -> Optional[str]:
    """Return the target table name of an INSERT/REPLACE, or ``None``."""
    m = _INSERT_TABLE_RE.match(sql)
    if not m:
        return None
    name = m.group(1)
    return name.split(".")[-1] if name else None


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


def _sqlite_row_to_dict(row):
    """Convert a sqlite3.Row (or compatible mapping) into a plain dict.

    Mirrors the helper used by the D1 reconciler so callers in the read
    fallback path see the same dict-shaped rows whether the data came
    from D1 or local SQLite.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:  # noqa: BLE001 — last-resort fallback, never raise
        return dict(row)


class _SqliteFallbackCursor:
    """Wraps a ``sqlite3.Cursor`` so iteration / fetch* return dict rows.

    The read path in :meth:`DualConnection.execute` falls back to SQLite
    when D1 is unreachable; D1 yields dict-like rows natively, but
    ``sqlite3.Row`` is *not* a dict (no ``.get()``, no ``in`` membership
    over keys, etc.). Callers in the codebase routinely treat SELECT
    results as mappings, so we wrap the cursor here to keep the contract
    consistent regardless of which backend served the query.
    """

    __slots__ = ("_cur", "lastrowid", "rowcount")

    def __init__(self, sqlite_cur):
        self._cur = sqlite_cur
        self.lastrowid = sqlite_cur.lastrowid
        self.rowcount = sqlite_cur.rowcount

    def fetchone(self):
        return _sqlite_row_to_dict(self._cur.fetchone())

    def fetchall(self):
        return [_sqlite_row_to_dict(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for row in self._cur:
            yield _sqlite_row_to_dict(row)


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
        # Successful D1 writes since the last commit/rollback. Tracked
        # separately from failures because D1 auto-commits each statement,
        # so a SQLite rollback with N>0 means D1 keeps rows SQLite no
        # longer has — that's drift even when zero failures occurred.
        self._d1_uncommitted_writes = 0
        self._recent_failure_signatures: "collections.OrderedDict[str, int]" = (
            collections.OrderedDict()
        )

    # ── Statement execution ─────────────────────────────────────────────

    def execute(self, sql: str, params: Iterable[Any] = ()):
        if _is_read(sql):
            try:
                return self._d1.execute(sql, params)
            except Exception as exc:
                # Read failures are NOT drift — we transparently fall back to
                # SQLite and keep serving the request. Counting them as drift
                # would inflate ``failure_count`` in the JSONL and trigger
                # spurious reconciliation passes.
                self._record_d1_read_failure(sql, exc)
                return _SqliteFallbackCursor(self._sqlite.execute(sql, params))

        sqlite_cur = self._sqlite.execute(sql, params)
        d1_cur = None
        try:
            d1_cur = self._d1.execute(sql, params)
            self._d1_uncommitted_writes += 1
            self._maybe_warn_id_drift(sqlite_cur, d1_cur, sql)
        except Exception as exc:
            self._record_d1_failure(sql, exc, kind="write")
        return DualCursor(sqlite_cur, d1_cur)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        seq_list = list(seq_of_params)
        self._sqlite.executemany(sql, seq_list)
        try:
            self._d1.executemany(sql, seq_list)
            self._d1_uncommitted_writes += len(seq_list)
        except Exception as exc:
            # On partial failure D1 may still have applied a prefix of the
            # batch; ``_d1_uncommitted_writes`` is intentionally a lower
            # bound so the failure record is the source of truth here.
            self._record_d1_failure(sql, exc, kind="executemany")

    def executescript(self, script: str):
        self._sqlite.executescript(script)
        try:
            self._d1.executescript(script)
            self._d1_uncommitted_writes += 1
        except Exception as exc:
            self._record_d1_failure(script, exc, kind="executescript")

    def batch_execute(self, statements: Sequence[Tuple[str, Sequence[Any]]]):
        sqlite_cursors = [
            self._sqlite.execute(sql, params)
            for sql, params in statements
        ]
        d1_cursors = [None] * len(sqlite_cursors)
        if not statements:
            return []
        first_sql = statements[0][0]
        try:
            batch = getattr(self._d1, "batch_execute", None)
            if callable(batch):
                d1_cursors = batch(statements)
            else:
                d1_cursors = [
                    self._d1.execute(sql, params)
                    for sql, params in statements
                ]
            self._d1_uncommitted_writes += sum(
                1 for sql, _params in statements if not _is_read(sql)
            )
            for sqlite_cur, d1_cur, (sql, _params) in zip(
                sqlite_cursors, d1_cursors, statements,
            ):
                self._maybe_warn_id_drift(sqlite_cur, d1_cur, sql)
        except Exception as exc:
            self._record_d1_failure(first_sql, exc, kind="batch_execute")
        if len(d1_cursors) < len(sqlite_cursors):
            d1_cursors = list(d1_cursors) + [None] * (
                len(sqlite_cursors) - len(d1_cursors)
            )
        return [
            DualCursor(sqlite_cur, d1_cur)
            for sqlite_cur, d1_cur in zip(sqlite_cursors, d1_cursors)
        ]

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
        # Only warn when there's actually something to be concerned about:
        # if no D1 writes happened (read-only transaction) and no failures
        # were recorded, the rollback is a true no-op for both backends.
        if self._d1_uncommitted_writes > 0:
            logger.warning(
                "DualConnection.rollback(): SQLite rolled back, but %d "
                "previously-applied D1 write(s) cannot be rolled back "
                "(D1 auto-commits per statement) — drift introduced.",
                self._d1_uncommitted_writes,
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

    def _track_failure_signature(self, sql: str) -> int:
        """Update the LRU and return the prior count for this signature.

        Shared between read- and write-failure paths so both honour the
        same WARNING-vs-DEBUG throttling without double-counting.
        """
        sig = _signature(sql)
        prior = self._recent_failure_signatures.get(sig, 0)
        self._recent_failure_signatures[sig] = prior + 1
        self._recent_failure_signatures.move_to_end(sig)
        while len(self._recent_failure_signatures) > _RECENT_FAILURE_LRU_MAX:
            self._recent_failure_signatures.popitem(last=False)
        return prior

    def _record_d1_failure(self, sql: str, exc: Exception, *, kind: str) -> None:
        """Record a D1 *write*-side failure (counts toward drift JSONL).

        Only call this for paths that mutate D1 state (INSERT / UPDATE /
        DELETE / executemany / executescript / commit). Read failures
        must use :meth:`_record_d1_read_failure` so they don't pollute
        the drift counters.
        """
        self._d1_failure_count += 1
        if self._d1_failure_first_sql is None:
            self._d1_failure_first_sql = _shorten(sql, 200)
            self._d1_failure_first_error = f"{type(exc).__name__}: {exc}"

        prior = self._track_failure_signature(sql)
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

    def _record_d1_read_failure(self, sql: str, exc: Exception) -> None:
        """Log a D1 *read* failure WITHOUT incrementing the drift counter.

        Read failures are recoverable: the caller transparently falls back
        to local SQLite and keeps serving the query. They do NOT represent
        SQLite-vs-D1 row divergence, so ``commit()`` and ``d1_drift.jsonl``
        must remain untouched. We still apply the LRU throttle so a flaky
        D1 doesn't flood WARNING logs.
        """
        prior = self._track_failure_signature(sql)
        if prior >= 1:
            logger.debug(
                "D1 read failed (repeat #%d, falling back to SQLite): %s | sql=%s",
                prior + 1, exc, _shorten(sql),
            )
        else:
            logger.warning(
                "D1 read failed (falling back to SQLite, not counted as drift): %s | sql=%s",
                exc, _shorten(sql),
            )

    def _flush_drift_record(self, *, committed: bool) -> None:
        # Two independent ways the transaction can leave SQLite and D1 out
        # of sync:
        #   1. One or more D1 writes raised — captured by failure_count.
        #   2. SQLite rolled back successful D1 writes — D1 auto-commits
        #      per statement so those rows are now orphans on D1's side.
        rollback_drift = (not committed) and self._d1_uncommitted_writes > 0
        if self._d1_failure_count <= 0 and not rollback_drift:
            self._reset_failure_state()
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "db": self._logical_name,
            "committed": committed,
            "failure_count": self._d1_failure_count,
            # Only meaningful on rollback; on commit both backends agree
            # and the count represents successful (and now matching) writes.
            "uncommitted_d1_writes": (
                self._d1_uncommitted_writes if not committed else 0
            ),
            "first_failed_sql": self._d1_failure_first_sql,
            "first_error": self._d1_failure_first_error,
        }
        _append_drift_record(record)
        if self._d1_failure_count > 0:
            logger.error(
                "Transaction %s with %d D1 write failure(s) on db=%s; "
                "drift logged to %s",
                "committed" if committed else "rolled back",
                self._d1_failure_count, self._logical_name, _DRIFT_LOG_PATH,
            )
        else:  # rollback_drift only
            logger.error(
                "Transaction rolled back after %d successful D1 write(s) on "
                "db=%s; D1 cannot undo them — drift logged to %s",
                self._d1_uncommitted_writes, self._logical_name, _DRIFT_LOG_PATH,
            )
        self._reset_failure_state()

    def _reset_failure_state(self) -> None:
        self._d1_failure_count = 0
        self._d1_failure_first_sql = None
        self._d1_failure_first_error = None
        self._d1_uncommitted_writes = 0
        self._recent_failure_signatures.clear()

    @staticmethod
    def _maybe_warn_id_drift(sqlite_cur, d1_cur, sql: str) -> None:
        """Track AUTOINCREMENT delta drift per table; warn only on *changes*.

        A constant offset between SQLite and D1 ``lastrowid`` is the normal
        post-migration steady state and is harmless (FK resolution uses
        business keys). What *is* a real signal is the offset *changing* —
        that means one side committed an INSERT the other did not since
        this process started, i.e. fresh asymmetric drift.

        Emits:
          * INFO once per table for the baseline delta (first observation).
          * WARNING when the delta changes (real new drift event).
          * DEBUG for steady-state matches (unchanged delta).
        """
        if sqlite_cur is None or d1_cur is None:
            return
        s_id = getattr(sqlite_cur, "lastrowid", None)
        d_id = getattr(d1_cur, "lastrowid", None)
        if s_id is None or d_id is None or s_id == 0 or d_id == 0:
            return

        delta = int(d_id) - int(s_id)
        table = _extract_insert_table(sql) or "<unknown>"

        with _ID_DELTA_LOCK:
            prior = _ID_DELTA_BY_TABLE.get(table)
            _ID_DELTA_BY_TABLE[table] = delta

        if prior is None:
            if delta == 0:
                logger.debug(
                    "Dual-write ID baseline aligned for %s (sqlite=%s d1=%s)",
                    table, s_id, d_id,
                )
            else:
                logger.info(
                    "Dual-write ID baseline delta for %s: d1-sqlite=%+d "
                    "(sqlite=%s d1=%s) — constant offsets are expected "
                    "post-migration; will only warn on future delta changes",
                    table, delta, s_id, d_id,
                )
            return

        if delta == prior:
            logger.debug(
                "Dual-write ID delta unchanged for %s: %+d (sqlite=%s d1=%s)",
                table, delta, s_id, d_id,
            )
            return

        logger.warning(
            "Dual-write ID drift CHANGED for %s: delta %+d -> %+d "
            "(sqlite=%s d1=%s) — one side committed an asymmetric INSERT | sql=%s",
            table, prior, delta, s_id, d_id, _shorten(sql),
        )


def _shorten(sql: str, n: int = 100) -> str:
    s = " ".join(sql.split())
    return s[:n] + ("…" if len(s) > n else "")
