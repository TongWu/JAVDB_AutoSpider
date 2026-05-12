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

Application-generated PRIMARY KEY tables
----------------------------------------
Tables listed in :data:`APPLICATION_GENERATED_ID_TABLES`
(``ReportSessions`` and the ``Pending*HistoryWrites`` pair) MUST be
inserted with an explicit PK column (``Id`` / ``Seq``) on both backends
— see ``migration/d1/2026_05_08_sessionid_decouple.md``. When the
INSERT supplies that column in its column list, the application has
already chosen the canonical value and both backends write the same id;
the row is consistent regardless of either side's AUTOINCREMENT
counter, so :class:`DualCursor` skips the ``lastrowid`` comparison in
that case. When the INSERT *omits* the PK column the caller is back to
trusting AUTOINCREMENT, and the SQLite-side id silently disagrees with
the D1-side id whenever the two counters have ever drifted; the
mismatch is detected, recorded to the drift log, and raised as
:class:`DualWriteIdMismatchError` so the corrupted id never reaches
downstream rows.
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


_READ_KEYWORDS = ("SELECT", "PRAGMA", "EXPLAIN", "VALUES")


# ── P0-1 strict-dual-write switch ───────────────────────────────────────
#
# Default behaviour (``STRICT_DUAL_WRITE`` unset or "0"/"false") preserves
# the original semantics: D1 write failures are logged to the drift file
# and counted, but SQLite still commits.  That is appropriate for the
# initial parallel-test phase where SQLite is canonical and D1 is a
# best-effort mirror.
#
# When ``STRICT_DUAL_WRITE=1`` (or any non-empty value other than "0"/"false"):
#   * Any write into an application-generated-id table where ``d1_cur is
#     None`` immediately raises :class:`DualWriteIdMismatchError` so the
#     business transaction aborts (mirrors the existing behaviour for
#     lastrowid disagreements).
#   * :meth:`DualConnection.commit` refuses to commit a transaction whose
#     D1 failure count is non-zero, so SQLite + D1 stay in lock-step
#     instead of drifting silently.
#
# Operators flip the flag on once the rest of the dual-write rough edges
# are fixed (executemany chunking, batch_execute None-cursor, etc.) and
# the email notification surfaces drift loudly.
_STRICT_DUAL_WRITE_ENV = "STRICT_DUAL_WRITE"


def _strict_dual_write_enabled() -> bool:
    """Return True iff the strict-dual-write opt-in is active.

    Read at call-time (not module import) so tests can monkeypatch
    ``os.environ`` without re-importing the module.
    """
    raw = os.environ.get(_STRICT_DUAL_WRITE_ENV, "")
    if not raw:
        return False
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


class DualWriteStrictError(RuntimeError):
    """Raised when STRICT_DUAL_WRITE detects asymmetric SQLite / D1 state.

    Distinct from :class:`DualWriteIdMismatchError` so callers (and
    tests) can distinguish "lastrowid disagreed" from "commit refused
    because D1 failures were recorded".
    """


class DualWriteAsymmetryError(RuntimeError):
    """Raised when a batched write asymmetrically applied to one backend.

    Specifically the cases where the legacy path used to ``break``
    silently and let the caller believe the full batch had been written:

    * :meth:`DualConnection.executemany` — D1 raised on chunk k, so
      chunks 0..k-1 are mirrored on both backends but k..N-1 were never
      attempted. SQLite is canonical for those earlier chunks, but the
      caller's expectation of "N rows written" is violated.
    * :meth:`DualConnection.batch_execute` — D1 returned ``None`` cursors
      for individual writes inside an otherwise-successful batch.

    The drift JSONL still captures forensic detail (``partial_prefix_count``
    / ``missing_write_indices``); this exception just makes the asymmetry
    impossible to ignore.

    Distinct from :class:`DualWriteStrictError` so callers can
    distinguish "STRICT_DUAL_WRITE on, refusing to commit" from "the
    batch landed only on one backend". Subclassing keeps existing
    ``except DualWriteStrictError`` handlers a no-op against the new
    raise — they specifically opt into strict-mode behaviour.
    """


def _is_read(sql: str) -> bool:
    # Linear scan that skips leading whitespace, ``--`` line comments,
    # ``/* ... */`` block comments and leading ``(`` (e.g.
    # ``( SELECT ... ) UNION ...``) before matching a read-only keyword.
    #
    # Implemented without regex on purpose: a pattern like
    # ``(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*`` exhibits catastrophic backtracking
    # on inputs that begin with ``/`` followed by many ``/*`` fragments
    # (classic ReDoS), because the engine retries every alternation split.
    #
    # Note on ``WITH``: SQLite supports ``WITH cte AS (...) <SELECT |
    # INSERT | UPDATE | DELETE>`` so we deliberately do NOT treat ``WITH``
    # as a read keyword. A misclassified CTE-prefixed write would skip the
    # SQLite mirror entirely; whereas a CTE-prefixed read landing on the
    # write path is merely wasteful (D1 rejects mid-statement and the
    # SQLite path still serves the answer). Callers that need a CTE
    # SELECT should rewrite as a sub-select.
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
        if ch == "(":
            # Skip a single leading paren (most read patterns wrap once).
            # Multiple leading parens are uncommon but the next loop
            # iteration handles them. We do NOT try to match the closing
            # paren — only the first non-paren/non-whitespace token
            # decides read vs write.
            i += 1
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
    os.environ.get("REPORTS_DIR", "reports"), "D1", "d1_drift.jsonl"
)
_DRIFT_LOG_LOCK = threading.Lock()


def _append_drift_record(record: dict) -> None:
    """Append a JSON line to the drift log; never raises."""
    # Defence in depth against polluting the git-tracked production drift
    # log from a test that forgot to monkeypatch ``_DRIFT_LOG_PATH``.  The
    # ``_isolate_drift_log`` autouse fixture in ``tests/conftest.py`` is
    # the primary protection; this is a last-line check for tests run via
    # pytest that bypass the conftest (e.g. nested pytest invocations,
    # third-party harnesses).
    if os.environ.get("PYTEST_CURRENT_TEST") and (
        "reports/D1/d1_drift.jsonl" in _DRIFT_LOG_PATH.replace(os.sep, "/")
    ):
        logger.warning(
            "Refusing to write drift record to production path %s under "
            "PYTEST_CURRENT_TEST=%s.  Test should monkeypatch "
            "_DRIFT_LOG_PATH (autouse fixture in tests/conftest.py).",
            _DRIFT_LOG_PATH, os.environ.get("PYTEST_CURRENT_TEST"),
        )
        return
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
# P1: support all four SQL identifier-quoting styles (double-quote,
# backtick, single-quote, square-bracket). A bracket-quoted INSERT like
# ``INSERT INTO [ReportSessions] ...`` previously slipped past the
# guarded-table check entirely because the regex only allowed
# ``"`'`` opening characters.
_INSERT_TABLE_RE = re.compile(
    r"""^\s*
    (?:INSERT|REPLACE)\s+
    (?:OR\s+\w+\s+)?
    INTO\s+
    (?:
        \[\s*([\w.]+)\s*\]              # [Table] form
        |
        [\"`']?([\w.]+)[\"`']?           # "Table" / `Table` / 'Table' / bare
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ID_DELTA_LOCK = threading.Lock()
_ID_DELTA_BY_TABLE: "dict[str, int]" = {}

# ── Application-generated PRIMARY KEY tables ────────────────────────────
#
# These tables MUST be inserted with an explicit ``Id`` column on both
# backends.  Trusting AUTOINCREMENT under STORAGE_BACKEND=dual means the
# two backends drift the moment a single asymmetric INSERT lands (one
# side commits, the other fails), and ``cur.lastrowid`` then returns
# whichever backend the cursor wraps — corrupting any ``SessionId`` that
# downstream rows are tagged with.
#
# The 2026-05-08 incident report (``migration/d1/2026_05_08_sessionid_
# decouple.md``) walks through one concrete failure caused by trusting
# the SQLite-side AUTOINCREMENT.
#
# When :meth:`DualCursor.__init__` sees an INSERT into one of these
# tables and the two ``lastrowid`` values disagree, the transaction is
# aborted (``DualWriteIdMismatchError``) so the corrupted Id never
# reaches downstream code.
# Map each guarded table to the application-generated PRIMARY KEY column
# whose value the application supplies explicitly in INSERTs. When the
# INSERT carries that column in its column list, the caller has already
# decided the canonical Id and the cross-backend ``lastrowid`` check
# becomes meaningless — Cloudflare D1's HTTP API has been observed to
# return a ``last_row_id`` that disagrees with SQLite's ``lastrowid`` for
# the same explicit-Id INSERT (e.g. when D1's AUTOINCREMENT counter has
# drifted ahead and reports the counter value rather than the explicit
# rowid). The row itself is written with the same ``Id`` on both
# backends, so that disagreement is a backend reporting quirk, not real
# drift; raising on it would (and did, 2026-05-12) abort otherwise-fine
# pipeline runs after the 2026-05-08 sessionid-decouple migration.
APPLICATION_GENERATED_ID_PK_COLUMN: "dict[str, str]" = {
    "ReportSessions": "Id",
    # Ingestion Perfect Rollback (Phase 2): the Pending tables also need
    # the same protection — ``_commit_one_movie`` flips ``ApplyState`` by
    # ``Seq IN (...)`` and ``db_commit_session_history`` later DELETEs by
    # ``ApplyState='applied'``.  If the SQLite-side Seq disagrees with
    # the D1-side Seq for the same logical row (e.g. asymmetric INSERT
    # failure during a stage burst), the apply / delete loop touches the
    # wrong row on at least one backend and leaves residual ``pending``
    # entries that trigger the Phase 3 critical alert.
    "PendingMovieHistoryWrites": "Seq",
    "PendingTorrentHistoryWrites": "Seq",
}
APPLICATION_GENERATED_ID_TABLES: frozenset = frozenset(
    APPLICATION_GENERATED_ID_PK_COLUMN
)


class DualWriteIdMismatchError(RuntimeError):
    """Raised when an application-generated PRIMARY KEY drifts between SQLite and D1.

    Indicates the application failed to supply an explicit ``Id`` for an
    insert into one of :data:`APPLICATION_GENERATED_ID_TABLES`, or that
    the two backends silently assigned different AUTOINCREMENT values.
    Either way the transaction must be rolled back to avoid tagging
    downstream rows with the wrong session id.
    """


def _extract_insert_table(sql: str) -> Optional[str]:
    """Return the target table name of an INSERT/REPLACE, or ``None``."""
    m = _INSERT_TABLE_RE.match(sql)
    if not m:
        return None
    # Group 1 = ``[Table]`` capture, group 2 = ``"Table"`` / bare. Exactly
    # one of them matches because the alternation is mutually exclusive.
    name = m.group(1) or m.group(2)
    return name.split(".")[-1] if name else None


# Matches the column list of an INSERT/REPLACE statement, e.g. the
# ``(Id, ReportType, ...)`` block that follows the table name. Used to
# detect whether the caller is supplying the application-generated PK
# column (Id / Seq) explicitly, in which case the cross-backend lastrowid
# check is moot — the row is written with the same explicit value on
# both SQLite and D1.
_INSERT_COLUMN_LIST_RE = re.compile(
    r"""^\s*
    (?:INSERT|REPLACE)\s+
    (?:OR\s+\w+\s+)?
    INTO\s+
    (?:\[\s*[\w.]+\s*\]|["`']?[\w.]+["`']?)\s*
    \(\s*(?P<cols>[^)]*?)\s*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _insert_supplies_column(sql: str, column: str) -> bool:
    """Return True iff *sql* is an INSERT whose column list contains *column*.

    Comparison is case-insensitive and ignores surrounding quoting
    (``"Id"`` / ``[Id]`` / ``` `Id` ``` / bare). An INSERT without an
    explicit column list (``INSERT INTO t VALUES (...)``) returns False —
    we cannot tell which columns the values map to, so we treat it as
    *not* supplying the column.
    """
    m = _INSERT_COLUMN_LIST_RE.match(sql)
    if not m:
        return False
    target = column.strip().lower()
    for raw in m.group("cols").split(","):
        name = raw.strip().strip('"').strip("`").strip("'").strip("[]").strip()
        if name.lower() == target:
            return True
    return False


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

    @staticmethod
    def _check_id_consistency(sqlite_cur, d1_cur, sql: str) -> None:
        """Log drift / raise on ``lastrowid`` mismatch for guarded tables.

        Shared by :meth:`for_write` and the
        :meth:`DualConnection.executemany` / :meth:`executescript` paths
        so the same invariant holds regardless of which write API was
        used.  When a guarded-table write lands on SQLite but D1 is
        missing (``d1_cur is None``) and :data:`STRICT_DUAL_WRITE` is on,
        the transaction is aborted via :class:`DualWriteIdMismatchError`
        — that is precisely the asymmetric-INSERT pattern responsible
        for the ``ReportSessions`` / ``SpiderStats`` ``-1`` drift seen
        in the 2026-05 dry-run reconcile reports.
        """
        if sqlite_cur is None:
            return
        table = _extract_insert_table(sql)
        if not table or table not in APPLICATION_GENERATED_ID_TABLES:
            return
        if d1_cur is None:
            # SQLite-only write into a guarded table. In default
            # (non-strict) mode the drift has already been recorded via
            # ``_record_d1_failure`` upstream so we silently continue;
            # in strict mode we abort the transaction so SQLite + D1
            # never split.
            if _strict_dual_write_enabled():
                record = {
                    "kind": "application_id_missing_d1_cursor",
                    "table": table,
                    "sql": _shorten(sql),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                _append_drift_record(record)
                logger.error(
                    "DualConnection: STRICT_DUAL_WRITE aborting transaction — "
                    "guarded INSERT into %s landed on SQLite but D1 cursor is "
                    "missing; details in %s",
                    table, _DRIFT_LOG_PATH,
                )
                raise DualWriteIdMismatchError(
                    f"{table}: SQLite committed but D1 cursor missing under "
                    f"STRICT_DUAL_WRITE; refusing to drift (see drift log)"
                )
            return
        # Post-2026-05-08: ``db_create_report_session`` (and the
        # equivalent Pending-table writers) supply the PK column
        # explicitly via :func:`_generate_session_id`, so the same Id
        # lands on both backends regardless of either side's
        # AUTOINCREMENT counter. In that case the cross-backend
        # ``lastrowid`` comparison no longer tells us anything useful
        # — Cloudflare D1's HTTP API has been observed to return a
        # ``last_row_id`` that differs from SQLite's ``lastrowid`` for
        # the same explicit-Id INSERT (the D1 wrapper reports the
        # session's internal counter rather than the explicit rowid).
        # The row is written correctly on both backends; raising would
        # abort an otherwise-fine pipeline run.
        pk_col = APPLICATION_GENERATED_ID_PK_COLUMN.get(table)
        if pk_col and _insert_supplies_column(sql, pk_col):
            return
        s_id = getattr(sqlite_cur, "lastrowid", None)
        d_id = getattr(d1_cur, "lastrowid", None)
        if s_id is None or d_id is None or s_id == d_id:
            return
        record = {
            "kind": "application_id_mismatch",
            "table": table,
            "sqlite_lastrowid": int(s_id),
            "d1_lastrowid": int(d_id),
            "sql": _shorten(sql),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        _append_drift_record(record)
        # Do NOT include the raw lastrowid values or SQL text in
        # the user-facing log / exception message: those identify
        # ReportSessions / Pending*HistoryWrites rows (private
        # session ids) and the SQL may carry inline literals.
        # The full forensic detail is already persisted to
        # reports/D1/d1_drift.jsonl above; operators read it from
        # there, not from console / CI logs.
        logger.error(
            "DualConnection: application-generated id "
            "mismatch on %s; aborting transaction. Caller must "
            "INSERT with explicit Id (see migration/d1/"
            "2026_05_08_sessionid_decouple.md); details in %s",
            table, _DRIFT_LOG_PATH,
        )
        raise DualWriteIdMismatchError(
            f"{table}: SQLite vs D1 lastrowid mismatch; "
            f"INSERT must supply Id explicitly under "
            f"STORAGE_BACKEND=dual (see drift log for details)"
        )

    @classmethod
    def for_write(cls, sqlite_cur, d1_cur, sql: str):
        """Build a cursor and enforce id consistency for guarded tables.

        Used by :meth:`DualConnection.execute` / ``batch_execute`` after
        a write.  When *sql* targets an application-generated-id table
        (:data:`APPLICATION_GENERATED_ID_TABLES`) and the two backends
        report different ``lastrowid`` values, the divergent state is
        flushed to ``d1_drift.jsonl`` and a
        :class:`DualWriteIdMismatchError` is raised so the surrounding
        transaction can roll back.
        """
        cls._check_id_consistency(sqlite_cur, d1_cur, sql)
        return cls(sqlite_cur, d1_cur)

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
        # Optional structured details about the first failure — kept as a
        # dict so executemany can attach partial_prefix_count, etc., and
        # the JSONL reconciler does not need to scrape the human-readable
        # error string.
        self._d1_failure_first_extra: Optional[dict] = None
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
        return DualCursor.for_write(sqlite_cur, d1_cur, sql)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        # P0-2: D1 auto-commits each HTTP batch, so a naive
        # ``sqlite.executemany(N) ; d1.executemany(N)`` lets D1 commit a
        # prefix and SQLite keep the full N rows when D1 chunk k fails.
        # We instead step through ``_BATCH_LIMIT``-sized chunks:
        #
        #   for each chunk:
        #       d1.executemany(chunk)      # may raise; SQLite untouched
        #       sqlite.executemany(chunk)  # only if D1 succeeded
        #
        # On D1 failure we record the drift with ``partial_prefix_count``
        # (the number of rows D1 has already auto-committed) and raise
        # so the outer transaction sees the failure and can rollback its
        # remaining work; D1 keeps the prefix and the drift JSONL points
        # the reconciler at exactly where it landed.
        seq_list = list(seq_of_params)
        if not seq_list:
            # Match sqlite3's no-op behaviour for empty batches.
            self._sqlite.executemany(sql, seq_list)
            return

        # Import lazily so we keep the legacy import surface stable and
        # avoid a hard circular dependency at module load time.
        from packages.python.javdb_platform.d1_client import _BATCH_LIMIT

        chunk_size = max(1, int(_BATCH_LIMIT))
        partial_prefix_count = 0
        last_sqlite_cur = None
        last_d1_cur = None
        last_chunk_failed = False

        for start in range(0, len(seq_list), chunk_size):
            chunk = seq_list[start:start + chunk_size]
            d1_chunk_cur = None
            try:
                d1_chunk_cur = self._d1.executemany(sql, chunk)
            except Exception as exc:
                last_chunk_failed = True
                # Record drift with a precise prefix count so reconcilers
                # know exactly how many rows ended up only on D1.
                self._record_d1_failure(
                    sql, exc, kind="executemany",
                    extra={"partial_prefix_count": partial_prefix_count,
                           "failed_chunk_size": len(chunk)},
                )
                # Enforce guarded-table invariant for an asymmetric
                # INSERT batch (SQLite has NOT been touched for this
                # chunk, but downstream code may be relying on a clean
                # break — let _check_id_consistency decide based on the
                # strict-mode flag).
                table = _extract_insert_table(sql)
                if table and table in APPLICATION_GENERATED_ID_TABLES:
                    if _strict_dual_write_enabled():
                        raise DualWriteStrictError(
                            f"{table}: executemany chunk failed on D1 with "
                            f"partial_prefix_count={partial_prefix_count}; "
                            f"refusing to commit further chunks under "
                            f"STRICT_DUAL_WRITE"
                        ) from exc
                # B.3 (post-2026-05-11 review): even in non-strict mode
                # we now raise instead of silently ``break``ing. Earlier
                # behaviour let the caller believe N rows were written
                # when only ``partial_prefix_count`` actually mirrored
                # to D1; the remaining ``len(seq_list) - partial_prefix_count``
                # entries weren't attempted on either backend. The drift
                # JSONL already captures forensic detail above — this
                # raise just makes the asymmetry visible to the caller
                # so the surrounding transaction can rollback.
                raise DualWriteAsymmetryError(
                    f"executemany chunk failed on D1 (sql={_shorten(sql)}): "
                    f"partial_prefix_count={partial_prefix_count}, "
                    f"failed_chunk_size={len(chunk)}, "
                    f"remaining_chunks_skipped={(len(seq_list) - start - len(chunk))}; "
                    f"drift recorded in {_DRIFT_LOG_PATH}"
                ) from exc

            # D1 chunk succeeded — mirror to SQLite. If SQLite raises
            # here we have NEW drift toward D1 (D1 has the chunk, SQLite
            # does not). Record it and propagate the exception so the
            # outer transaction aborts.
            try:
                sqlite_chunk_cur = self._sqlite.executemany(sql, chunk)
            except Exception as sqlite_exc:
                self._record_d1_failure(
                    sql, sqlite_exc, kind="executemany_sqlite_after_d1",
                    extra={"partial_prefix_count": partial_prefix_count,
                           "d1_already_applied_chunk_size": len(chunk)},
                )
                raise

            partial_prefix_count += len(chunk)
            self._d1_uncommitted_writes += len(chunk)
            last_sqlite_cur = sqlite_chunk_cur
            last_d1_cur = d1_chunk_cur

        # Enforce the same guarded-table ``lastrowid`` invariant as
        # :meth:`execute`. Skipped when the last chunk failed because
        # ``last_d1_cur`` would be ``None`` and the strict-mode raise
        # has already happened (or non-strict legacy continues without
        # the check, matching the prior behaviour).
        if not last_chunk_failed:
            DualCursor._check_id_consistency(last_sqlite_cur, last_d1_cur, sql)

    def executescript(self, script: str):
        # If a script wedges an INSERT into one of the guarded
        # application-generated-id tables we must NOT execute it through
        # this path: neither SQLite's nor D1's ``executescript`` exposes
        # per-statement ``lastrowid`` for the cross-backend check, so the
        # invariant in :meth:`DualCursor._check_id_consistency` cannot be
        # enforced.  Route guarded INSERTs through :meth:`execute` /
        # :meth:`executemany` / :meth:`batch_execute` instead.
        #
        # B.4: use sqlite3.complete_statement to walk the script
        # statement-by-statement instead of naive ``split(";")``. The
        # split-on-semicolon path mis-segmented when a string literal,
        # a trigger body, or a multi-line BEGIN…END block contained an
        # inline semicolon, so a guarded INSERT embedded in such a
        # construct could slip past the refusal.
        statements = list(_iter_complete_statements(script))
        for stmt in statements:
            table = _extract_insert_table(stmt.strip())
            if table and table in APPLICATION_GENERATED_ID_TABLES:
                raise DualWriteIdMismatchError(
                    f"executescript() refuses INSERT into guarded table "
                    f"{table}; use execute()/executemany()/batch_execute() "
                    f"so SQLite vs D1 lastrowid can be compared"
                )
        self._sqlite.executescript(script)
        try:
            self._d1.executescript(script)
            # B.10: count each statement that lands on D1 instead of a
            # flat +1. The drift JSONL's ``uncommitted_d1_writes`` is
            # the rollback reconciler's primary signal for "how many D1
            # rows are orphaned after a rollback"; a per-script +1 lost
            # the picture for any non-trivial migration script.
            stmt_count = sum(1 for s in statements if s.strip())
            self._d1_uncommitted_writes += max(1, stmt_count)
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
        # ``batch_outer_raised`` distinguishes "all writes failed because
        # batch_execute itself raised" (handled by the except below; the
        # P0-3 follow-up check must NOT double-record) from "batch
        # succeeded but returned None for some entries" (the malformed-
        # response case the P0-3 check exists to catch).
        batch_outer_raised = False
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
            batch_outer_raised = True
            if any(not _is_read(sql) for sql, _params in statements):
                self._record_d1_failure(first_sql, exc, kind="batch_execute")
        d1_cursors = list(d1_cursors)
        # Fail fast if D1 returned fewer cursors than statements on the
        # write path: silently padding with ``None`` would mask a dropped
        # mirror write and let downstream code believe both backends
        # applied the batch.  Reads are allowed to fall back to SQLite
        # below.
        if len(d1_cursors) < len(statements):
            missing_writes = [
                sql for (sql, _p), d1_cur in zip(
                    statements,
                    d1_cursors + [None] * (
                        len(statements) - len(d1_cursors)
                    ),
                ) if d1_cur is None and not _is_read(sql)
            ]
            if missing_writes:
                raise RuntimeError(
                    "DualConnection.batch_execute: D1 returned "
                    f"{len(d1_cursors)} cursor(s) for {len(statements)} "
                    f"statement(s); {len(missing_writes)} write(s) were "
                    "dropped by the mirror — refusing to silently pad with "
                    "None and continue."
                )
            d1_cursors = d1_cursors + [None] * (
                len(sqlite_cursors) - len(d1_cursors)
            )
        # P0-3: ``d1_cursors`` may have the same length as ``statements``
        # while still containing ``None`` for individual write entries
        # (malformed CF response, single-statement timeout inside a
        # batch, etc.). Reads transparently fall back to SQLite below,
        # but writes must be recorded as drift — otherwise the caller
        # receives a DualCursor that hides the missing D1 mirror. Skip
        # this check when the outer ``batch_execute`` already raised:
        # the except clause above has already recorded the batch-wide
        # failure and we'd otherwise double-count.
        missing_write_indices: list[int] = []
        if not batch_outer_raised:
            for idx, ((sql, _params), d1_cur) in enumerate(zip(statements, d1_cursors)):
                if d1_cur is None and not _is_read(sql):
                    missing_write_indices.append(idx)

        if missing_write_indices:
            sample_sql, _ = statements[missing_write_indices[0]]
            self._record_d1_failure(
                sample_sql,
                RuntimeError(
                    "batch_execute returned None cursor for "
                    f"{len(missing_write_indices)} write statement(s)"
                ),
                kind="batch_execute_missing_write_cursor",
                extra={
                    "missing_write_indices": missing_write_indices,
                    "total_statements": len(statements),
                },
            )
            # Under strict mode, abort the transaction so SQLite (which
            # already executed every statement above) is rolled back by
            # the outer ``get_db()`` context. Keep the StrictError
            # subclass for tests / handlers that opt into strict-only
            # semantics; non-strict callers still see an
            # AsymmetryError so they can't ignore the dropped writes.
            if _strict_dual_write_enabled():
                raise DualWriteStrictError(
                    f"batch_execute: {len(missing_write_indices)} write "
                    f"statement(s) returned no D1 cursor under "
                    f"STRICT_DUAL_WRITE; refusing to drift."
                )
            # B.3 (post-2026-05-11 review): non-strict mode used to
            # silently return DualCursors whose ``_d1_cur`` was None
            # for the missing writes, so downstream code (especially
            # readers that fall back to SQLite via _SqliteFallbackCursor)
            # could not even detect the asymmetry. Raise so the
            # surrounding transaction aborts — drift is already in the
            # JSONL above for the reconciler.
            raise DualWriteAsymmetryError(
                f"batch_execute: {len(missing_write_indices)} write "
                f"statement(s) returned no D1 cursor "
                f"(total={len(statements)}); drift recorded in "
                f"{_DRIFT_LOG_PATH}"
            )

        for idx, ((sql, _params), sqlite_cur, d1_cur) in enumerate(zip(
            statements, sqlite_cursors, d1_cursors,
        )):
            if d1_cur is None and _is_read(sql):
                d1_cursors[idx] = _SqliteFallbackCursor(sqlite_cur)
        return [
            DualCursor.for_write(sqlite_cur, d1_cur, sql)
            for (sql, _params), sqlite_cur, d1_cur in zip(
                statements, sqlite_cursors, d1_cursors,
            )
        ]

    # ── Transaction & lifecycle ─────────────────────────────────────────

    def commit(self):
        # P0-1: Under STRICT_DUAL_WRITE we refuse to commit a transaction
        # whose D1 mirror is already known to be inconsistent (any write
        # raised earlier). Abort the SQLite side too so callers see a
        # clear error instead of silently inheriting drift.
        #
        # Snapshotted before ``_flush_drift_record`` clears the failure
        # counters; otherwise the error message would always report 0.
        strict = _strict_dual_write_enabled()
        pending_failures = self._d1_failure_count
        first_sql = self._d1_failure_first_sql
        first_err = self._d1_failure_first_error

        if strict and pending_failures > 0:
            # Roll back SQLite to restore parity, log the abort, and
            # raise. _flush_drift_record() is still called inside
            # rollback() so the drift JSONL captures the event.
            logger.error(
                "DualConnection.commit(): STRICT_DUAL_WRITE refusing to commit "
                "on db=%s — %d D1 write failure(s) recorded in this transaction. "
                "Rolling back SQLite to keep backends in lock-step.",
                self._logical_name, pending_failures,
            )
            try:
                self.rollback()
            except Exception:
                logger.exception(
                    "DualConnection.commit(): rollback() raised while aborting "
                    "after D1 failures on db=%s — proceeding to raise.",
                    self._logical_name,
                )
            raise DualWriteStrictError(
                f"db={self._logical_name}: {pending_failures} D1 write "
                f"failure(s) under STRICT_DUAL_WRITE; first failed sql="
                f"{first_sql!r} first_error={first_err!r}"
            )

        self._sqlite.commit()
        try:
            self._d1.commit()
        except Exception as exc:
            self._record_d1_failure("COMMIT", exc, kind="commit")
            if _strict_dual_write_enabled() and self._d1_uncommitted_writes > 0:
                # COMMIT itself raised on D1 *and* the transaction had
                # at least one successful prior D1 write. SQLite has
                # already committed so we cannot truly recover, but we
                # surface the mismatch loudly instead of silently
                # claiming success.
                self._flush_drift_record(committed=True)
                raise DualWriteStrictError(
                    f"db={self._logical_name}: SQLite commit succeeded but "
                    f"D1 commit raised under STRICT_DUAL_WRITE: {exc!r}"
                ) from exc
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

    @property
    def d1_failure_count(self) -> int:
        """Number of D1 write failures recorded in the current transaction."""
        return self._d1_failure_count

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

    def _record_d1_failure(
        self,
        sql: str,
        exc: Exception,
        *,
        kind: str,
        extra: Optional[dict] = None,
    ) -> None:
        """Record a D1 *write*-side failure (counts toward drift JSONL).

        Only call this for paths that mutate D1 state (INSERT / UPDATE /
        DELETE / executemany / executescript / commit). Read failures
        must use :meth:`_record_d1_read_failure` so they don't pollute
        the drift counters.

        *extra* is an optional structured payload (e.g. ``{"partial_
        prefix_count": 100}`` from :meth:`executemany`) merged into the
        drift JSONL record so reconcilers can act precisely without
        regexing the human-readable error string.
        """
        self._d1_failure_count += 1
        if self._d1_failure_first_sql is None:
            self._d1_failure_first_sql = _shorten(sql, 200)
            self._d1_failure_first_error = f"{type(exc).__name__}: {exc}"
            if extra:
                self._d1_failure_first_extra = dict(extra)

        prior = self._track_failure_signature(sql)
        if prior >= 1:
            logger.debug(
                "D1 %s failed (repeat #%d for this signature): %s | sql=%s | extra=%s",
                kind, prior + 1, exc, _shorten(sql), extra or {},
            )
        else:
            logger.warning(
                "D1 %s failed (SQLite still applied): %s | sql=%s | extra=%s",
                kind, exc, _shorten(sql), extra or {},
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
        # P0-2: surface structured extras (e.g. executemany
        # partial_prefix_count) so the reconciler can target exactly the
        # rows D1 already auto-committed.
        if self._d1_failure_first_extra:
            record["first_failed_extra"] = self._d1_failure_first_extra
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
        self._d1_failure_first_extra = None
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


def _iter_complete_statements(script: str):
    """Yield individual SQL statements from a multi-statement script.

    Uses ``sqlite3.complete_statement`` to track parser state — so an
    inline ``;`` inside a string literal, a trigger body, or a
    ``BEGIN…END`` block does NOT prematurely terminate the current
    statement. Falls back to emitting the whole script when sqlite3
    cannot identify any complete statement at all (e.g. operator-supplied
    DDL with a missing trailing ``;``).
    """
    remainder = script
    any_yielded = False
    # Forward-scan each ``;`` position. The first one whose prefix is a
    # complete statement marks a real boundary; an earlier ``;`` whose
    # prefix is incomplete means we're still inside a string literal or
    # a BEGIN…END block and we need to keep scanning forward.
    while remainder:
        # Find the earliest ``;`` that closes a complete statement.
        idx = 0
        boundary = -1
        while True:
            pos = remainder.find(";", idx)
            if pos == -1:
                break
            prefix = remainder[: pos + 1]
            if sqlite3.complete_statement(prefix):
                boundary = pos
                break
            idx = pos + 1
        if boundary == -1:
            # No more complete statements. Emit any trailing non-blank
            # fragment so callers (e.g. the guarded-INSERT scan) still
            # see it.
            tail = remainder.strip()
            if tail:
                yield tail
                any_yielded = True
            break
        stmt = remainder[: boundary + 1]
        yield stmt
        any_yielded = True
        remainder = remainder[boundary + 1 :]
    if not any_yielded and script.strip():
        # Defensive fallback: never lose the script entirely if our
        # walker mis-segments — emit it as a single statement so the
        # caller sees at least one slice to inspect.
        yield script
