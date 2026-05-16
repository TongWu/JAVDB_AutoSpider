"""Tests for the D1 client + dual-write connection facade."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any, Iterable, List

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.storage.d1_client import (  # noqa: E402
    D1Connection,
    D1Cursor,
    D1PermanentError,
    D1TransientError,
    _split,
)
from javdb.storage import d1_client as _d1_client_module  # noqa: E402
from javdb.storage import dual_connection as _dual_module  # noqa: E402
from javdb.storage.dual_connection import (  # noqa: E402
    APPLICATION_GENERATED_ID_TABLES,
    DualConnection,
    DualWriteIdMismatchError,
    _is_read,
    _iter_complete_statements,
)
from javdb.storage.repos.operations_repo import (  # noqa: E402
    append_rclone_staging,
    open_rclone_staging,
    swap_rclone_inventory,
)


# ── _is_read ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  SELECT * FROM x",
        "PRAGMA table_info(x)",
        "EXPLAIN SELECT 1",
        "-- comment line\nSELECT 1",
        # B.5 (2026-05-11): leading parens are now skipped so wrapped /
        # set-operation reads are routed through the read path instead
        # of being executed against both backends as if they were writes.
        "(SELECT 1)",
        "  ( SELECT * FROM x )",
        "((SELECT 1))",
        "VALUES (1), (2)",
    ],
)
def test_is_read_true(sql):
    assert _is_read(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO x VALUES(1)",
        "UPDATE x SET y=1",
        "DELETE FROM x",
        "CREATE TABLE x(a)",
        "REPLACE INTO x VALUES(1)",
        "DROP TABLE x",
        "WITH foo AS (SELECT 1) SELECT * FROM foo",
        "WITH foo AS (SELECT 1) INSERT INTO x SELECT * FROM foo",
        "WITH foo AS (SELECT 1) UPDATE x SET y=1",
        "WITH foo AS (SELECT 1) DELETE FROM x",
    ],
)
def test_is_read_false(sql):
    assert not _is_read(sql)


# ── _iter_complete_statements (B.4) ──────────────────────────────────────


def test_iter_complete_statements_basic():
    """Trivial 3-statement script split into 3 complete statements."""
    script = "CREATE TABLE x(a); INSERT INTO x VALUES(1); INSERT INTO x VALUES(2);"
    stmts = [s.strip() for s in _iter_complete_statements(script) if s.strip()]
    assert stmts == [
        "CREATE TABLE x(a);",
        "INSERT INTO x VALUES(1);",
        "INSERT INTO x VALUES(2);",
    ]


def test_iter_complete_statements_does_not_split_on_semicolon_inside_string():
    """Regression for B.4: a ``;`` inside a string literal must NOT
    terminate the surrounding statement. The legacy split-on-semicolon
    walker would have emitted ``INSERT INTO note VALUES ('hi`` as a
    standalone (and let a guarded INSERT slip past inspection if the
    operator's payload happened to wedge one inside a literal)."""
    script = "INSERT INTO note VALUES ('hi; there'); SELECT 1;"
    stmts = [s.strip() for s in _iter_complete_statements(script) if s.strip()]
    assert stmts == [
        "INSERT INTO note VALUES ('hi; there');",
        "SELECT 1;",
    ]


def test_iter_complete_statements_handles_trailing_fragment_without_semicolon():
    """Trailing statement without a closing ``;`` is still emitted so
    callers (e.g. ``executescript``'s guarded-INSERT check) can inspect
    it. Defensive: we'd rather over-report a slice than silently
    discard a fragment."""
    script = "CREATE TABLE x(a); INSERT INTO x VALUES(1)"
    stmts = [s.strip() for s in _iter_complete_statements(script) if s.strip()]
    assert stmts == [
        "CREATE TABLE x(a);",
        "INSERT INTO x VALUES(1)",
    ]


# ── D1Cursor ─────────────────────────────────────────────────────────────


def test_d1_cursor_parses_meta_and_rows():
    cur = D1Cursor(
        {
            "meta": {"last_row_id": 42, "changes": 1},
            "results": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
        }
    )
    assert cur.lastrowid == 42
    assert cur.rowcount == 1
    assert cur.fetchone() == {"a": 1, "b": "x"}
    assert cur.fetchall() == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_d1_cursor_empty_results():
    cur = D1Cursor({"meta": {}})
    assert cur.lastrowid is None
    assert cur.rowcount == 0
    assert cur.fetchone() is None
    assert cur.fetchall() == []


def test_split_chunks():
    assert list(_split([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(_split([], 3)) == []


# ── DualConnection ───────────────────────────────────────────────────────


class FakeD1Cursor:
    def __init__(self, lastrowid=None, rowcount=0, rows=None):
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeD1Connection:
    """Records every call so tests can assert on dual-write behaviour."""

    def __init__(self, *, fail_on_write: bool = False):
        self.executed: List[tuple] = []
        self.executed_many: List[tuple] = []
        self.commits = 0
        self.fail_on_write = fail_on_write
        # Default response: one row from a SELECT
        self._next_select_rows = [{"n": 99}]

    def execute(self, sql: str, params: Iterable[Any] = ()):
        self.executed.append((sql, list(params)))
        if not _is_read(sql):
            if self.fail_on_write:
                raise RuntimeError("simulated D1 write failure")
            return FakeD1Cursor(lastrowid=123, rowcount=1)
        return FakeD1Cursor(rows=self._next_select_rows)

    def executemany(self, sql, seq):
        seq_list = [list(p) for p in seq]
        self.executed_many.append((sql, seq_list))

    def commit(self):
        self.commits += 1

    def close(self):
        pass


@pytest.fixture
def sqlite_conn(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    return conn


def test_writes_go_to_both_backends(sqlite_conn):
    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1)

    cur = dual.execute("INSERT INTO t (v) VALUES (?)", ("hello",))

    # SQLite saw the write
    rows = sqlite_conn.execute("SELECT v FROM t").fetchall()
    assert [dict(r) for r in rows] == [{"v": "hello"}]
    # D1 also saw it
    assert fake_d1.executed[-1][0].startswith("INSERT INTO t")
    # Cursor reports the SQLite-canonical lastrowid
    assert cur.lastrowid == 1


def test_reads_go_to_d1_only(sqlite_conn):
    fake_d1 = FakeD1Connection()
    fake_d1._next_select_rows = [{"n": 7}]
    dual = DualConnection(sqlite_conn, fake_d1)

    cur = dual.execute("SELECT COUNT(*) AS n FROM t")
    assert cur.fetchone() == {"n": 7}

    # The SQL should have hit D1 and not SQLite
    assert any("SELECT" in s for s, _ in fake_d1.executed)


def test_d1_write_failure_does_not_break_sqlite(sqlite_conn):
    fake_d1 = FakeD1Connection(fail_on_write=True)
    dual = DualConnection(sqlite_conn, fake_d1)

    cur = dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    assert cur.lastrowid == 1

    rows = sqlite_conn.execute("SELECT v FROM t").fetchall()
    assert [dict(r) for r in rows] == [{"v": "x"}]


def test_executemany_mirrors_to_d1(sqlite_conn):
    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1)

    dual.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])

    assert sqlite_conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    assert fake_d1.executed_many == [
        ("INSERT INTO t (v) VALUES (?)", [["a"], ["b"], ["c"]]),
    ]


def test_commit_propagates_to_both(sqlite_conn):
    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1)
    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.commit()
    assert fake_d1.commits == 1


def test_total_changes_reports_sqlite_canonical(sqlite_conn):
    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1)
    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.execute("INSERT INTO t (v) VALUES (?)", ("y",))
    assert dual.total_changes == 2


# ── D1Connection retry / classification ──────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", headers=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {"success": True, "result": []}
        self.text = text or ""
        self.headers = headers or {}

    def json(self):
        if isinstance(self._json_body, Exception):
            raise self._json_body
        return self._json_body


@pytest.fixture
def d1_conn():
    return D1Connection(account_id="acct", database_id="db", api_token="tok")


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []

    def _sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(_d1_client_module.time, "sleep", _sleep)
    return sleeps


def test_execute_sends_single_object_body(monkeypatch, d1_conn, no_sleep):
    """CF /query rejects bare arrays; single statement must be {sql, params}."""
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    d1_conn.execute("SELECT * FROM t WHERE id = ?", (1,))
    assert isinstance(captured["json"], dict)
    assert captured["json"] == {"sql": "SELECT * FROM t WHERE id = ?", "params": [1]}


def test_execute_stringifies_json_unsafe_integer_params(monkeypatch, d1_conn, no_sleep):
    """Ints beyond JS Number.MAX_SAFE_INTEGER must not be JSON Number on the wire."""
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    unsafe = 2**53  # first integer not exactly representable as IEEE-754 double
    d1_conn.execute("INSERT INTO t (id) VALUES (?)", (unsafe,))
    assert captured["json"]["params"] == [str(unsafe)]

    captured.clear()
    safe = 2**53 - 1
    d1_conn.execute("INSERT INTO t (id) VALUES (?)", (safe,))
    assert captured["json"]["params"] == [safe]

    captured.clear()
    d1_conn.execute("SELECT x FROM t WHERE ok = ?", (True,))
    assert captured["json"]["params"] == [True]


def test_executemany_sends_batch_object_body(monkeypatch, d1_conn, no_sleep):
    """Multi-statement must be {batch: [...]}, not a bare array."""
    captured = []

    def fake_post(url, headers, json, timeout):
        captured.append(json)
        return _FakeResponse(
            status_code=200,
            json_body={
                "success": True,
                "result": [{"meta": {"changes": 1}, "results": []}] * len(json["batch"]),
            },
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    d1_conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])

    assert len(captured) == 1
    assert isinstance(captured[0], dict)
    assert "batch" in captured[0]
    assert captured[0]["batch"] == [
        {"sql": "INSERT INTO t (v) VALUES (?)", "params": ["a"]},
        {"sql": "INSERT INTO t (v) VALUES (?)", "params": ["b"]},
        {"sql": "INSERT INTO t (v) VALUES (?)", "params": ["c"]},
    ]


def test_retry_on_5xx_then_succeeds(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(status_code=503, text="boom")
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    cur = d1_conn.execute("INSERT INTO x VALUES (1)")
    assert cur.rowcount == 1
    assert len(calls) == 2
    assert len(no_sleep) == 1


def test_retry_on_429_respects_retry_after(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(status_code=429, text="slow down", headers={"Retry-After": "2"})
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    d1_conn.execute("SELECT 1")
    assert len(calls) == 2
    assert no_sleep == [2.0]


def test_no_retry_on_4xx(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(status_code=400, text="bad request")

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    with pytest.raises(D1PermanentError):
        d1_conn.execute("INSERT INTO x VALUES (1)")
    assert len(calls) == 1
    assert no_sleep == []


def test_retry_exhausted_raises_transient(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(status_code=503, text="still down")

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    with pytest.raises(D1TransientError):
        d1_conn.execute("INSERT INTO x VALUES (1)")
    assert len(calls) == _d1_client_module._MAX_RETRIES
    assert len(no_sleep) == _d1_client_module._MAX_RETRIES - 1


def test_d1_reset_do_classified_transient(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(
                status_code=200,
                json_body={"success": False, "errors": [{"message": "D1_RESET_DO occurred"}]},
            )
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    d1_conn.execute("SELECT 1")
    assert len(calls) == 2


def test_400_long_running_export_treated_as_transient(monkeypatch, d1_conn, no_sleep):
    """CF returns HTTP 400 + code 7500 when an export holds a DB-wide lock.

    Without explicit handling this would raise D1PermanentError on the very
    first call and silently drop the write into the d1_drift.jsonl bucket.
    """
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(
                status_code=400,
                json_body={
                    "messages": [],
                    "result": [],
                    "success": False,
                    "errors": [
                        {"code": 7500, "message": "Currently processing a long-running export."}
                    ],
                },
            )
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    cur = d1_conn.execute("INSERT INTO x VALUES (1)")
    assert cur.rowcount == 1
    assert len(calls) == 2
    assert len(no_sleep) == 1
    # Export-lock backoff floor must apply (default 15s).
    assert no_sleep[0] >= _d1_client_module._EXPORT_LOCK_BACKOFF_FLOOR_SEC


def test_400_real_sql_error_remains_permanent(monkeypatch, d1_conn, no_sleep):
    """Genuine SQL errors must still raise D1PermanentError without retries."""
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(
            status_code=400,
            json_body={
                "success": False,
                "errors": [{"code": 7400, "message": "near \"FORM\": syntax error"}],
            },
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    with pytest.raises(D1PermanentError):
        d1_conn.execute("SELEKT * FORM t")
    assert len(calls) == 1
    assert no_sleep == []


def test_400_code_7500_constraint_mismatch_is_permanent(monkeypatch, d1_conn, no_sleep):
    """2026-05-12 regression: CF D1 maps every SQLite error onto wrapper
    code 7500, so the classifier must decide on message text rather than
    blanket-classifying 7500 as transient. The case in production was a
    SpiderStats upsert against D1 missing the uq_spiderstats_session
    UNIQUE index — its ``ON CONFLICT(SessionId)`` failed permanently,
    but the old classifier retried five times wasting ~60s.
    """
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(
            status_code=400,
            json_body={
                "success": False,
                "errors": [{
                    "code": 7500,
                    "message": (
                        "ON CONFLICT clause does not match any PRIMARY KEY "
                        "or UNIQUE constraint: SQLITE_ERROR"
                    ),
                }],
            },
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    with pytest.raises(D1PermanentError):
        d1_conn.execute(
            "INSERT INTO SpiderStats (SessionId) VALUES (?) "
            "ON CONFLICT(SessionId) DO UPDATE SET SessionId=excluded.SessionId",
            (1,),
        )
    # Permanent ⇒ exactly one call, zero retries, zero sleeps.
    assert len(calls) == 1
    assert no_sleep == []


def test_export_lock_backoff_capped_by_max_sleep(monkeypatch, d1_conn, no_sleep):
    """Even with the export floor, backoff is bounded by D1_RETRY_MAX_SLEEP_SEC."""
    monkeypatch.setattr(_d1_client_module, "_RETRY_MAX_SLEEP_SEC", 5.0)
    monkeypatch.setattr(_d1_client_module, "_EXPORT_LOCK_BACKOFF_FLOOR_SEC", 30.0)

    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(
                status_code=400,
                json_body={
                    "success": False,
                    "errors": [{"code": 7500, "message": "long-running export"}],
                },
            )
        return _FakeResponse(
            status_code=200,
            json_body={"success": True, "result": [{"meta": {}, "results": []}]},
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    d1_conn.execute("SELECT 1")
    # Cap (5s) + jitter (<=0.5s) → cannot exceed 5.5s.
    assert no_sleep[0] <= 5.5


def test_d1_connection_uses_session_for_keepalive():
    """A real Session reuses the urllib3 connection pool across requests."""
    import requests as _requests

    conn = D1Connection(account_id="a", database_id="b", api_token="t")
    try:
        assert isinstance(conn._session, _requests.Session)
        # The bound post must come from the same Session, not the module.
        assert conn._post_request.__self__ is conn._session
    finally:
        conn.close()


def test_d1_close_releases_session(d1_conn):
    """close() must close the underlying Session so its connection pool exits."""
    closed = []
    original_close = d1_conn._session.close

    def _track():
        closed.append(True)
        original_close()

    d1_conn._session.close = _track
    d1_conn.close()
    assert closed == [True]


def test_batch_execute_packs_chunks_of_50(monkeypatch, d1_conn, no_sleep):
    """120 statements → 3 HTTP calls (50, 50, 20), one cursor per statement."""
    posts: list = []

    def fake_post(url, headers, json, timeout):
        posts.append(json)
        n = len(json["batch"])
        return _FakeResponse(
            status_code=200,
            json_body={
                "success": True,
                "result": [
                    {"meta": {"changes": 1, "last_row_id": idx + 1}, "results": []}
                    for idx in range(n)
                ],
            },
        )

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)

    statements = [
        ("INSERT INTO t (v) VALUES (?)", [i]) for i in range(120)
    ]
    cursors = d1_conn.batch_execute(statements)

    # Exactly one cursor per input statement, in order.
    assert len(cursors) == 120
    # Three HTTP roundtrips of 50/50/20.
    assert [len(p["batch"]) for p in posts] == [50, 50, 20]
    # All bodies must use the {batch: [...]} shape, never bare arrays.
    assert all("batch" in p and "sql" not in p for p in posts)


def test_batch_execute_empty_returns_no_cursors(monkeypatch, d1_conn, no_sleep):
    posts = []

    def fake_post(*a, **kw):
        posts.append(kw.get("json"))
        return _FakeResponse()

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
    assert d1_conn.batch_execute([]) == []
    assert posts == []


def test_dual_batch_execute_read_failure_falls_back_without_drift(sqlite_conn, tmp_path, monkeypatch):
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    class FailingBatchD1(FakeD1Connection):
        def batch_execute(self, statements):
            raise RuntimeError("simulated D1 read outage")

    dual = DualConnection(sqlite_conn, FailingBatchD1(), logical_name="history")
    cursors = dual.batch_execute([("SELECT COUNT(*) AS n FROM t", ())])

    assert len(cursors) == 1
    row = cursors[0].fetchone()
    assert row == {"n": 0}
    assert row.get("n") == 0
    assert dual._d1_uncommitted_writes == 0
    dual.commit()
    assert not drift_path.exists()


def test_dual_batch_execute_read_none_cursor_uses_dict_fallback(sqlite_conn):
    class MissingReadCursorD1(FakeD1Connection):
        def batch_execute(self, statements):
            return [None for _statement in statements]

    dual = DualConnection(sqlite_conn, MissingReadCursorD1(), logical_name="history")
    cursors = dual.batch_execute([("SELECT COUNT(*) AS n FROM t", ())])

    row = cursors[0].fetchone()
    assert row == {"n": 0}
    assert row.get("n") == 0


def test_rclone_inventory_swap_raises_when_dual_batch_d1_write_fails(
    sqlite_conn, tmp_path, monkeypatch,
):
    # Redirect the drift log so test-injected "simulated D1 batch outage"
    # rollback records don't pollute the git-tracked production
    # ``reports/D1/d1_drift.jsonl`` (the path consumed by
    # ``scripts/aggregate_pending_health.py`` and the email Phase 3 alerts).
    monkeypatch.setattr(
        _dual_module, "_DRIFT_LOG_PATH",
        str(tmp_path / "d1_drift.jsonl"),
    )
    sqlite_conn.execute("DROP TABLE IF EXISTS RcloneInventory")
    sqlite_conn.execute(
        """
        CREATE TABLE RcloneInventory (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            VideoCode TEXT NOT NULL,
            SensorCategory TEXT,
            SubtitleCategory TEXT,
            FolderPath TEXT,
            FolderSize INTEGER,
            FileCount INTEGER,
            DateTimeScanned TEXT
        )
        """
    )
    sqlite_conn.execute(
        """
        INSERT INTO RcloneInventory
        (VideoCode, SensorCategory, SubtitleCategory, FolderPath,
         FolderSize, FileCount, DateTimeScanned)
        VALUES ('OLD-001', NULL, NULL, '2025/old/OLD-001', 1, 1, '2026-05-07')
        """
    )
    staging = open_rclone_staging(sqlite_conn, 7)
    append_rclone_staging(
        sqlite_conn,
        [{
            "VideoCode": "NEW-001",
            "FolderPath": "2026/new/NEW-001",
            "FolderSize": 2,
            "FileCount": 1,
            "DateTimeScanned": "2026-05-08",
        }],
        7,
    )
    sqlite_conn.commit()

    class FailingInventoryBatchD1(FakeD1Connection):
        def batch_execute(self, statements):
            raise RuntimeError("simulated D1 batch outage")

    dual = DualConnection(
        sqlite_conn, FailingInventoryBatchD1(), logical_name="operations",
    )

    try:
        with pytest.raises(RuntimeError, match="D1 mirror failed"):
            swap_rclone_inventory(dual, 7)
        assert dual.d1_failure_count == 1
    finally:
        dual.rollback()

    rows = sqlite_conn.execute(
        "SELECT VideoCode FROM RcloneInventory ORDER BY VideoCode"
    ).fetchall()
    assert [row["VideoCode"] for row in rows] == ["OLD-001"]
    assert sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE name=?", (staging,),
    ).fetchone() is not None


# ── DualConnection drift tracking ────────────────────────────────────────


def test_drift_log_written_on_commit_with_d1_failures(monkeypatch, sqlite_conn, tmp_path):
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection(fail_on_write=True)
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.commit()

    assert drift_path.exists()
    lines = drift_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["db"] == "history"
    assert record["committed"] is True
    assert record["failure_count"] == 1
    assert "INSERT INTO t" in record["first_failed_sql"]


def test_repeated_failure_signature_downgrades_to_debug(monkeypatch, sqlite_conn, tmp_path, caplog):
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(tmp_path / "d.jsonl"))
    fake_d1 = FakeD1Connection(fail_on_write=True)
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    caplog.set_level("DEBUG", logger=_dual_module.logger.name)
    for v in ("a", "b", "c"):
        dual.execute("INSERT INTO t (v) VALUES (?)", (v,))

    warning_msgs = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "D1 write failed" in r.getMessage()
    ]
    debug_msgs = [
        r for r in caplog.records
        if r.levelname == "DEBUG" and "D1 write failed" in r.getMessage()
    ]
    assert len(warning_msgs) == 1
    assert len(debug_msgs) == 2


def test_failure_state_reset_after_commit(monkeypatch, sqlite_conn, tmp_path):
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection(fail_on_write=True)
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.commit()

    fake_d1.fail_on_write = False
    dual.execute("INSERT INTO t (v) VALUES (?)", ("y",))
    dual.commit()

    lines = drift_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, "second clean commit must NOT add a new drift record"


def test_no_drift_log_when_d1_healthy(monkeypatch, sqlite_conn, tmp_path):
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.commit()

    assert not drift_path.exists()


def test_rollback_after_successful_d1_writes_logs_drift(monkeypatch, sqlite_conn, tmp_path):
    """SQLite rolled back, D1 already auto-committed → real divergence.

    Even with zero D1 failures the transaction still leaves D1 holding
    rows SQLite no longer has, which the reconciler must see.
    """
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.execute("INSERT INTO t (v) VALUES (?)", ("y",))
    dual.rollback()

    assert drift_path.exists()
    lines = drift_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["db"] == "history"
    assert record["committed"] is False
    assert record["failure_count"] == 0
    assert record["uncommitted_d1_writes"] == 2
    # No failure happened so first_failed_sql stays empty.
    assert record["first_failed_sql"] is None


def test_rollback_with_no_writes_does_not_log_drift(monkeypatch, sqlite_conn, tmp_path):
    """Read-only or empty transactions must not emit drift on rollback."""
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    # SELECT routes to D1 only; no write happens on either side.
    dual.execute("SELECT COUNT(*) AS n FROM t")
    dual.rollback()

    assert not drift_path.exists()


def test_rollback_drift_includes_executemany_count(monkeypatch, sqlite_conn, tmp_path):
    """Successful executemany writes must inflate uncommitted_d1_writes."""
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    dual.executemany(
        "INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)]
    )
    dual.rollback()

    record = json.loads(drift_path.read_text(encoding="utf-8").strip())
    assert record["uncommitted_d1_writes"] == 3
    assert record["failure_count"] == 0


def test_uncommitted_writes_reset_after_commit(monkeypatch, sqlite_conn, tmp_path):
    """Successful commit must zero the counter so a later read-only
    rollback doesn't replay the previous transaction's writes as drift."""
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    dual.commit()  # both backends in sync, counter resets
    dual.rollback()  # nothing to roll back

    assert not drift_path.exists()


def test_current_backend_reflects_env(monkeypatch):
    """``current_backend()`` should reflect the active STORAGE_BACKEND."""
    from javdb.storage.db import db as _db

    monkeypatch.delenv("_STORAGE_BACKEND_INIT_OVERRIDE", raising=False)

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    assert _db.current_backend() == "sqlite"

    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    assert _db.current_backend() == "d1"

    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    assert _db.current_backend() == "dual"

    monkeypatch.setenv("STORAGE_BACKEND", "DUAL")
    assert _db.current_backend() == "dual"

    monkeypatch.setenv("STORAGE_BACKEND", "garbage")
    assert _db.current_backend() == "sqlite"


def test_use_db_storage_includes_d1_backends(monkeypatch):
    from javdb.infra import config as config_helper

    monkeypatch.setattr(config_helper, "storage_mode", lambda: "csv")
    monkeypatch.delenv("_STORAGE_BACKEND_INIT_OVERRIDE", raising=False)
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    assert config_helper.use_db_storage() is True

    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    assert config_helper.use_db_storage() is True

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    assert config_helper.use_db_storage() is False


# ── init_db dual-backend override isolation ───────────────────────────────


def test_backend_mode_thread_local_invisible_to_siblings(monkeypatch):
    """Thread-local override must NOT leak to sibling threads.

    The dual-backend ``init_db`` window stashes ``'sqlite'`` into a
    per-thread sentinel so the calling thread's DDL plumbing only touches
    the local file. Sibling threads, however, must keep observing the
    configured ``STORAGE_BACKEND='dual'`` so that any concurrent
    ``_get_connection`` calls cache real ``DualConnection`` objects rather
    than plain ``sqlite3.Connection`` ones.
    """
    import threading

    from javdb.storage.db import db as _db

    monkeypatch.delenv("_STORAGE_BACKEND_INIT_OVERRIDE", raising=False)
    monkeypatch.setenv("STORAGE_BACKEND", "dual")

    main_set = threading.Event()
    sibling_done = threading.Event()
    sibling_observed: dict = {}

    def sibling():
        if not main_set.wait(timeout=5):
            sibling_observed["error"] = "timed out waiting for main"
            return
        sibling_observed["mode"] = _db._backend_mode()
        sibling_observed["env"] = os.environ.get("_STORAGE_BACKEND_INIT_OVERRIDE")
        sibling_done.set()

    t = threading.Thread(target=sibling, name="sibling-mode-probe")
    t.start()

    _db._local._storage_backend_init_override = "sqlite"
    try:
        # Main thread sees its own override (proves the mechanism works).
        assert _db._backend_mode() == "sqlite"
        main_set.set()
        assert sibling_done.wait(timeout=5), "sibling never reported"
    finally:
        try:
            del _db._local._storage_backend_init_override
        except AttributeError:
            pass

    t.join(timeout=5)

    assert "error" not in sibling_observed, sibling_observed.get("error")
    assert sibling_observed["mode"] == "dual", (
        "Sibling thread saw the init-time override leak: expected 'dual', "
        f"got {sibling_observed['mode']!r}. The thread-local must NOT be "
        "mirrored into the process env or it breaks concurrent "
        "_get_connection callers."
    )
    assert sibling_observed["env"] is None, (
        "init_db(dual) must not write _STORAGE_BACKEND_INIT_OVERRIDE into "
        f"os.environ; sibling observed {sibling_observed['env']!r}."
    )


def test_init_db_dual_does_not_set_global_env_var(monkeypatch):
    """Regression: ``init_db`` under STORAGE_BACKEND=dual must keep the
    override thread-local — never writing ``_STORAGE_BACKEND_INIT_OVERRIDE``
    into ``os.environ`` where sibling threads would observe it.

    Earlier code paired the thread-local with an ``os.environ`` write so
    that any thread calling ``_backend_mode()`` while ``init_db`` was in
    flight saw ``'sqlite'`` and cached a plain SQLite connection — a silent
    correctness bug for the dual-write contract.
    """
    import threading

    import javdb.infra.config as _cfg
    from javdb.storage.db import db as _db

    monkeypatch.delenv("_STORAGE_BACKEND_INIT_OVERRIDE", raising=False)
    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    monkeypatch.setattr(_cfg, "use_sqlite", lambda: True)

    inside_init = threading.Event()
    sibling_done = threading.Event()
    observed: dict = {}

    def fake_do_init(db_path):
        # While we're in the dual-init window the env var must remain unset.
        observed["env_during_init_main_view"] = os.environ.get(
            "_STORAGE_BACKEND_INIT_OVERRIDE"
        )
        # Main thread (which set the thread-local) must see 'sqlite'.
        observed["mode_during_init_main_view"] = _db._backend_mode()
        inside_init.set()
        # Block until the sibling has had a chance to probe.
        if not sibling_done.wait(timeout=5):
            observed["error"] = "sibling never finished probing"

    monkeypatch.setattr(_db, "_do_init", fake_do_init)

    def sibling():
        if not inside_init.wait(timeout=5):
            observed["sibling_error"] = "init never reached fake_do_init"
            sibling_done.set()
            return
        # Sibling has no thread-local; with the old bug it would fall
        # through to the env-var branch and see 'sqlite'.
        observed["sibling_mode"] = _db._backend_mode()
        observed["sibling_env"] = os.environ.get("_STORAGE_BACKEND_INIT_OVERRIDE")
        sibling_done.set()

    t = threading.Thread(target=sibling, name="sibling-init-probe")
    t.start()

    _db.init_db(force=True)
    t.join(timeout=5)

    assert "error" not in observed, observed.get("error")
    assert "sibling_error" not in observed, observed.get("sibling_error")

    # While init is mid-flight on the main thread, the env var MUST stay
    # unset so sibling threads keep seeing the configured 'dual' backend.
    assert observed["env_during_init_main_view"] is None, (
        "init_db(dual) wrote _STORAGE_BACKEND_INIT_OVERRIDE into os.environ "
        f"(saw {observed['env_during_init_main_view']!r}); the override must "
        "stay thread-local so siblings don't cache plain SQLite connections."
    )
    assert observed["mode_during_init_main_view"] == "sqlite", (
        "The calling thread should still see its own 'sqlite' downgrade "
        f"during init; got {observed['mode_during_init_main_view']!r}."
    )
    assert observed["sibling_mode"] == "dual", (
        "Sibling thread saw the init-time override leak: expected 'dual', "
        f"got {observed['sibling_mode']!r}."
    )
    assert observed["sibling_env"] is None, (
        "Sibling observed _STORAGE_BACKEND_INIT_OVERRIDE in os.environ "
        f"({observed['sibling_env']!r}); init_db(dual) must not write it."
    )

    # Post-init: state is fully restored.
    assert os.environ.get("_STORAGE_BACKEND_INIT_OVERRIDE") is None
    assert not hasattr(_db._local, "_storage_backend_init_override")
    assert _db._backend_mode() == "dual"


# ── Application-generated id guard (Phase 3) ────────────────────────────


def _make_report_sessions_sqlite(tmp_path):
    """Set up a sqlite connection with the ReportSessions schema."""
    path = tmp_path / "rs.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ReportSessions ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ReportType TEXT, ReportDate TEXT, CsvFilename TEXT, "
        "DateTimeCreated TEXT, Status TEXT, RunId TEXT, RunAttempt INTEGER)"
    )
    return conn


def test_report_sessions_in_application_id_guard_set():
    assert "ReportSessions" in APPLICATION_GENERATED_ID_TABLES


def test_explicit_id_into_report_sessions_is_accepted(tmp_path):
    sqlite_conn = _make_report_sessions_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    # Configure the fake to return the same lastrowid for the explicit
    # Id INSERT — simulating both backends honouring the application-
    # supplied id.
    SAME_ID = 1234567890
    sqlite_conn.execute(
        "INSERT INTO ReportSessions (Id, ReportType, ReportDate, "
        "CsvFilename, DateTimeCreated, Status) "
        "VALUES (?, 'daily', '2026-05-08', 'x.csv', '2026-05-08 00:00:00', "
        "'in_progress')",
        (SAME_ID,),
    )

    class _Cursor:
        def __init__(self, lastrowid, rowcount=1, rows=None):
            self.lastrowid = lastrowid
            self.rowcount = rowcount
            self._rows = rows or []
        def fetchone(self):
            return self._rows[0] if self._rows else None
        def fetchall(self):
            return list(self._rows)

    fake_d1.execute = lambda sql, params=(): _Cursor(  # type: ignore[assignment]
        lastrowid=SAME_ID, rowcount=1,
    )

    # Simulate a *fresh* INSERT that uses an explicit Id — the guard
    # should NOT raise because lastrowid agrees on both sides.
    sqlite_conn.execute("DELETE FROM ReportSessions")
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="reports")
    cur = dual.execute(
        "INSERT INTO ReportSessions (Id, ReportType, ReportDate, "
        "CsvFilename, DateTimeCreated, Status) "
        "VALUES (?, ?, ?, ?, ?, 'in_progress')",
        (SAME_ID, "daily", "2026-05-08", "x.csv", "2026-05-08 00:00:00"),
    )
    assert cur.lastrowid == SAME_ID


def test_explicit_id_skips_lastrowid_check_even_when_backends_disagree(tmp_path):
    """2026-05-12 regression: when the INSERT supplies an explicit Id,
    the application has chosen the canonical value and both backends
    write it. Cloudflare D1's HTTP API has been observed to report a
    ``last_row_id`` that disagrees with SQLite's ``lastrowid`` for the
    same explicit-Id INSERT (D1 returns its internal AUTOINCREMENT
    counter, not the explicit rowid). The guard must not raise in that
    case — the row is consistent.
    """
    sqlite_conn = _make_report_sessions_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    SID = 1821300000000001  # snowflake-shaped session id

    class _Cursor:
        def __init__(self, lastrowid, rowcount=1):
            self.lastrowid = lastrowid
            self.rowcount = rowcount
            self._rows = []
        def fetchone(self):
            return None
        def fetchall(self):
            return []

    # D1 returns a *different* lastrowid (e.g. its internal counter
    # value) — SQLite returns SID because Id is the rowid alias.
    fake_d1.execute = lambda sql, params=(): _Cursor(  # type: ignore[assignment]
        lastrowid=347, rowcount=1,
    )

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="reports")
    # Should NOT raise — explicit Id is in the column list.
    cur = dual.execute(
        "INSERT INTO ReportSessions (Id, ReportType, ReportDate, "
        "CsvFilename, DateTimeCreated, Status) "
        "VALUES (?, ?, ?, ?, ?, 'in_progress')",
        (SID, "daily", "2026-05-12", "x.csv", "2026-05-12 00:00:00"),
    )
    # SQLite is canonical → DualCursor.lastrowid reflects SID.
    assert cur.lastrowid == SID


def test_lastrowid_mismatch_on_report_sessions_raises(monkeypatch, tmp_path):
    sqlite_conn = _make_report_sessions_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    class _Cursor:
        def __init__(self, lastrowid, rowcount=1):
            self.lastrowid = lastrowid
            self.rowcount = rowcount
            self._rows = []
        def fetchone(self):
            return None
        def fetchall(self):
            return []

    # SQLite will use AUTOINCREMENT (1), D1 returns 999 → mismatch.
    fake_d1.execute = lambda sql, params=(): _Cursor(  # type: ignore[assignment]
        lastrowid=999, rowcount=1,
    )

    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="reports")

    with pytest.raises(DualWriteIdMismatchError):
        dual.execute(
            "INSERT INTO ReportSessions (ReportType, ReportDate, "
            "CsvFilename, DateTimeCreated) "
            "VALUES (?, ?, ?, ?)",
            ("daily", "2026-05-08", "x.csv", "2026-05-08 00:00:00"),
        )

    # Drift log captured the event.
    assert drift_path.exists()
    contents = drift_path.read_text(encoding="utf-8").splitlines()
    assert any(
        "application_id_mismatch" in line for line in contents
    )


def test_unguarded_table_id_drift_is_warning_not_error(tmp_path):
    """For tables not in APPLICATION_GENERATED_ID_TABLES, divergent
    lastrowid is just a warning (legacy behaviour)."""
    path = tmp_path / "t.db"
    sqlite_conn = sqlite3.connect(path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_conn.execute(
        "CREATE TABLE OtherTable ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"
    )
    fake_d1 = FakeD1Connection()

    class _Cursor:
        def __init__(self, lastrowid, rowcount=1):
            self.lastrowid = lastrowid
            self.rowcount = rowcount
            self._rows = []
        def fetchone(self):
            return None
        def fetchall(self):
            return []

    fake_d1.execute = lambda sql, params=(): _Cursor(  # type: ignore[assignment]
        lastrowid=999, rowcount=1,
    )

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="other")
    # Should not raise.
    cur = dual.execute(
        "INSERT INTO OtherTable (v) VALUES (?)", ("hi",),
    )
    assert cur is not None


def test_explicit_pk_insert_does_not_emit_drift_warning(tmp_path, caplog):
    """2026-05-12 follow-up: when the INSERT supplies the explicit PK,
    Cloudflare D1's HTTP API can return a ``last_row_id`` whose low bits
    are clipped (JSON Number → IEEE-754 double, losing precision above
    2^53). The row is canonical on both sides; the per-INSERT
    ``_maybe_warn_id_drift`` must therefore stay silent — otherwise
    every Pending* INSERT for a real-sized snowflake Seq spams the log
    with bogus "drift CHANGED" warnings.
    """
    import logging
    from javdb.storage import dual_connection as _dual_mod

    sqlite_conn = _make_pending_tables_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    # Two snowflake Seqs whose float-converted D1 last_row_ids differ
    # from SQLite's by varying low-bit deltas — the exact pattern that
    # produced "delta -56 -> +64 -> -8" warnings in production logs.
    plan = [
        (7459908895307053056, 7459908895307053000),  # delta -56
        (7459908897626503168, 7459908897626503000),  # delta -168
        (7459908898851239936, 7459908898851240000),  # delta +64
        (7459908904106702848, 7459908904106703000),  # delta +152
    ]
    cursor_iter = iter(plan)

    def _fake_execute(sql, params=()):
        _, d1_lr = next(cursor_iter)
        return _FixedLastrowidCursor(lastrowid=d1_lr, rowcount=1)

    fake_d1.execute = _fake_execute  # type: ignore[assignment]
    # Clear cross-test pollution in the process-wide delta tracker so
    # the WARN-on-change branch is exercised faithfully.
    with _dual_mod._ID_DELTA_LOCK:
        _dual_mod._ID_DELTA_BY_TABLE.pop("PendingTorrentHistoryWrites", None)

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    with caplog.at_level(logging.WARNING, logger="javdb.storage.dual_connection"):
        for sid_seq, _ in plan:
            dual.execute(
                "INSERT INTO PendingTorrentHistoryWrites "
                "(Seq, SessionId, Href, SubtitleIndicator, CensorIndicator, "
                "DateTimeVisited, ApplyState) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (sid_seq, 7, "/v/abc", 1, 1, "2026-05-12 00:00:00"),
            )

    drift_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "Dual-write ID drift" in r.getMessage()
    ]
    assert drift_warnings == [], (
        f"Explicit-Seq INSERTs must not emit ID-drift warnings; got: "
        f"{[w.getMessage() for w in drift_warnings]}"
    )


# ── Pending tables (R2: Ingestion Perfect Rollback) ─────────────────────
#
# PendingMovie/TorrentHistoryWrites must be guarded the same way
# ReportSessions is, otherwise a Seq drift between SQLite and D1 would
# silently leave residual ``ApplyState='pending'`` rows after commit
# (``_commit_one_movie`` flips ``applied`` by ``Seq IN (...)`` and the
# subsequent DELETE is keyed off ``ApplyState='applied'``).  These tests
# protect the contract on both sides: the table-set membership and the
# explicit-Seq INSERT pattern that ``db_stage_history_write`` emits.


_PENDING_MOVIE_DDL = (
    "CREATE TABLE PendingMovieHistoryWrites ("
    "Seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "SessionId INTEGER NOT NULL, "
    "RunId TEXT, RunAttempt INTEGER, "
    "Href TEXT NOT NULL, VideoCode TEXT, "
    "ActorName TEXT, ActorGender TEXT, ActorLink TEXT, SupportingActors TEXT, "
    "DateTimeVisited TEXT NOT NULL, "
    "CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "ApplyState TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(ApplyState IN ('pending','applied')))"
)

_PENDING_TORRENT_DDL = (
    "CREATE TABLE PendingTorrentHistoryWrites ("
    "Seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "SessionId INTEGER NOT NULL, "
    "RunId TEXT, RunAttempt INTEGER, "
    "Href TEXT NOT NULL, VideoCode TEXT, "
    "Category TEXT, "
    "SubtitleIndicator INTEGER NOT NULL, CensorIndicator INTEGER NOT NULL, "
    "MagnetUri TEXT, Size TEXT, FileCount INTEGER, ResolutionType INTEGER, "
    "DateTimeVisited TEXT NOT NULL, "
    "CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "ApplyState TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(ApplyState IN ('pending','applied')))"
)


def _make_pending_tables_sqlite(tmp_path):
    path = tmp_path / "pending.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_PENDING_MOVIE_DDL)
    conn.execute(_PENDING_TORRENT_DDL)
    return conn


class _FixedLastrowidCursor:
    def __init__(self, lastrowid, rowcount=1):
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._rows = []

    def fetchone(self):
        return None

    def fetchall(self):
        return []


@pytest.mark.parametrize(
    "table",
    ["PendingMovieHistoryWrites", "PendingTorrentHistoryWrites"],
)
def test_pending_tables_in_application_id_guard_set(table):
    """R2: both Pending tables must be guarded against dual-mode Seq drift."""
    assert table in APPLICATION_GENERATED_ID_TABLES


def test_explicit_seq_into_pending_movie_history_is_accepted(tmp_path):
    """When SQLite + D1 agree on the explicit Seq, the guard passes."""
    sqlite_conn = _make_pending_tables_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    SAME_SEQ = 1755545088000123
    fake_d1.execute = lambda sql, params=(): _FixedLastrowidCursor(  # type: ignore[assignment]
        lastrowid=SAME_SEQ, rowcount=1,
    )

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    cur = dual.execute(
        "INSERT INTO PendingMovieHistoryWrites "
        "(Seq, SessionId, Href, DateTimeVisited, ApplyState) "
        "VALUES (?, ?, ?, ?, 'pending')",
        (SAME_SEQ, 42, "/v/abc", "2026-05-08 12:00:00"),
    )
    assert cur.lastrowid == SAME_SEQ


def test_explicit_seq_into_pending_torrent_history_is_accepted(tmp_path):
    sqlite_conn = _make_pending_tables_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    SAME_SEQ = 1755545088000456
    fake_d1.execute = lambda sql, params=(): _FixedLastrowidCursor(  # type: ignore[assignment]
        lastrowid=SAME_SEQ, rowcount=1,
    )

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    cur = dual.execute(
        "INSERT INTO PendingTorrentHistoryWrites "
        "(Seq, SessionId, Href, SubtitleIndicator, CensorIndicator, "
        "DateTimeVisited, ApplyState) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (SAME_SEQ, 42, "/v/abc", 1, 1, "2026-05-08 12:00:00"),
    )
    assert cur.lastrowid == SAME_SEQ


def test_explicit_seq_skips_lastrowid_check_even_when_backends_disagree(tmp_path):
    """Pending-table counterpart of the ReportSessions regression test:
    when ``Seq`` is supplied explicitly the dual guard must trust the
    application value and not raise on a per-backend ``lastrowid``
    disagreement.
    """
    sqlite_conn = _make_pending_tables_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()

    SEQ = 1821300000000099
    fake_d1.execute = lambda sql, params=(): _FixedLastrowidCursor(  # type: ignore[assignment]
        lastrowid=42, rowcount=1,
    )

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    cur = dual.execute(
        "INSERT INTO PendingMovieHistoryWrites "
        "(Seq, SessionId, Href, DateTimeVisited, ApplyState) "
        "VALUES (?, ?, ?, ?, 'pending')",
        (SEQ, 7, "/v/abc", "2026-05-12 00:00:00"),
    )
    assert cur.lastrowid == SEQ


def test_lastrowid_mismatch_on_pending_movie_history_raises(monkeypatch, tmp_path):
    """Without explicit Seq, AUTOINCREMENT can drift → guard MUST raise.

    This is the regression net for R2: if a future caller forgets to
    supply Seq under STORAGE_BACKEND=dual, the dual guard catches the
    SQLite (=1) vs D1 (=999) mismatch before any downstream apply loop
    keys off the wrong row.
    """
    sqlite_conn = _make_pending_tables_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()
    fake_d1.execute = lambda sql, params=(): _FixedLastrowidCursor(  # type: ignore[assignment]
        lastrowid=999, rowcount=1,
    )

    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    with pytest.raises(DualWriteIdMismatchError):
        dual.execute(
            "INSERT INTO PendingMovieHistoryWrites "
            "(SessionId, Href, DateTimeVisited) VALUES (?, ?, ?)",
            (42, "/v/abc", "2026-05-08 12:00:00"),
        )

    assert drift_path.exists()
    contents = drift_path.read_text(encoding="utf-8").splitlines()
    assert any("application_id_mismatch" in line for line in contents)
    assert any("PendingMovieHistoryWrites" in line for line in contents)


def test_lastrowid_mismatch_on_pending_torrent_history_raises(monkeypatch, tmp_path):
    sqlite_conn = _make_pending_tables_sqlite(tmp_path)
    fake_d1 = FakeD1Connection()
    fake_d1.execute = lambda sql, params=(): _FixedLastrowidCursor(  # type: ignore[assignment]
        lastrowid=999, rowcount=1,
    )

    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")
    with pytest.raises(DualWriteIdMismatchError):
        dual.execute(
            "INSERT INTO PendingTorrentHistoryWrites "
            "(SessionId, Href, SubtitleIndicator, CensorIndicator, "
            "DateTimeVisited) VALUES (?, ?, ?, ?, ?)",
            (42, "/v/abc", 1, 1, "2026-05-08 12:00:00"),
        )

    assert drift_path.exists()
    contents = drift_path.read_text(encoding="utf-8").splitlines()
    assert any("application_id_mismatch" in line for line in contents)
    assert any("PendingTorrentHistoryWrites" in line for line in contents)


def test_db_stage_history_write_supplies_explicit_seq():
    """Integration: db_stage_history_write must INSERT with explicit Seq.

    A regression here means the guard above stops catching real drift
    because production writes go back through AUTOINCREMENT (and that's
    exactly the silent-residual scenario R2 was filed against).
    """
    from javdb.storage.db import db as _db

    # The autouse `_isolate_sqlite` conftest fixture has already pointed
    # _db.HISTORY_DB_PATH at a temp file and run init_db.  Stage one
    # row, then read back the Seq.
    _db.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-09",
        csv_filename="r2-test.csv",
        write_mode="pending",
    )
    sid = "999_999_999"
    seq = _db.db_stage_history_write(
        sid, "movie", {"Href": "/v/r2-test", "VideoCode": "R2TEST"},
    )
    # Post-2026-05-13 Seq is a TEXT ISO-like snowflake. AUTOINCREMENT
    # would give a tiny decimal string like "1" / "2"; assert the
    # canonical shape instead.
    assert _db._SESSION_ID_PATTERN.match(seq), (
        f"Seq={seq!r} looks like AUTOINCREMENT; the explicit-Seq INSERT "
        "path may have regressed."
    )
    with _db.get_db(_db.HISTORY_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Seq, SessionId, Href FROM PendingMovieHistoryWrites "
            "WHERE SessionId=?",
            (sid,),
        ).fetchone()
    assert row is not None
    assert row["Seq"] == seq
    assert row["Href"] == "/v/r2-test"


def test_db_stage_history_write_torrent_supplies_explicit_seq():
    """Same as above for the torrent staging path."""
    from javdb.storage.db import db as _db

    _db.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-09",
        csv_filename="r2-torrent-test.csv",
        write_mode="pending",
    )
    sid = "888_888_888"
    seq = _db.db_stage_history_write(
        sid, "torrent",
        {
            "Href": "/v/r2-tor",
            "VideoCode": "R2TOR",
            "Category": "subtitle",
            "MagnetUri": "magnet:?xt=urn:btih:abc",
        },
    )
    assert _db._SESSION_ID_PATTERN.match(seq), (
        f"torrent Seq={seq!r} looks like AUTOINCREMENT"
    )
    with _db.get_db(_db.HISTORY_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Seq FROM PendingTorrentHistoryWrites WHERE SessionId=?",
            (sid,),
        ).fetchone()
    assert row is not None
    assert row["Seq"] == seq


# ─── P0 hardening regression tests ──────────────────────────────────────
#
# These cover the cross-backend invariants that were silently broken
# before the 2026-05 hardening pass: STRICT_DUAL_WRITE commit-refusal,
# executemany chunk alignment, batch_execute None-cursor accounting,
# and the legacy plain-INSERT semantics under default (non-strict)
# mode (no behaviour change there — only opt-in).


from javdb.storage.dual_connection import (  # noqa: E402
    DualWriteAsymmetryError,
    DualWriteStrictError,
)


def _strict_env(monkeypatch, enabled: bool = True) -> None:
    """Helper: toggle STRICT_DUAL_WRITE on/off for a single test."""
    if enabled:
        monkeypatch.setenv("STRICT_DUAL_WRITE", "1")
    else:
        monkeypatch.delenv("STRICT_DUAL_WRITE", raising=False)


def test_strict_mode_commit_raises_when_d1_failure_recorded(
    sqlite_conn, monkeypatch, tmp_path,
):
    """P0-1: commit() must abort under strict mode + D1 failure."""
    _strict_env(monkeypatch, True)
    drift = tmp_path / "drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift))

    fake_d1 = FakeD1Connection(fail_on_write=True)
    dual = DualConnection(sqlite_conn, fake_d1)
    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))
    assert dual.d1_failure_count == 1

    with pytest.raises(DualWriteStrictError):
        dual.commit()


def test_strict_mode_commit_clean_path_unaffected(
    sqlite_conn, monkeypatch,
):
    """P0-1: clean transactions still commit normally under strict mode."""
    _strict_env(monkeypatch, True)
    fake_d1 = FakeD1Connection()
    dual = DualConnection(sqlite_conn, fake_d1)
    dual.execute("INSERT INTO t (v) VALUES (?)", ("y",))
    dual.commit()  # must NOT raise
    assert fake_d1.commits == 1


def test_default_mode_legacy_behaviour_preserved(sqlite_conn, monkeypatch):
    """P0-1: with STRICT_DUAL_WRITE unset, legacy semantics still apply."""
    _strict_env(monkeypatch, False)
    fake_d1 = FakeD1Connection(fail_on_write=True)
    dual = DualConnection(sqlite_conn, fake_d1)
    dual.execute("INSERT INTO t (v) VALUES (?)", ("z",))
    # Legacy: commit() does NOT raise even though D1 had a failure
    dual.commit()


class _ChunkAwareFakeD1(FakeD1Connection):
    """Variant that fails ``executemany`` only on the Nth chunk.

    Mirrors what CF does in practice: chunks 1..k-1 auto-commit, chunk
    k raises (timeout / 5xx / network blip), chunks k+1..n never run.
    """

    def __init__(self, fail_chunk_at_call: int):
        super().__init__()
        self.fail_chunk_at_call = fail_chunk_at_call
        self._chunk_call_count = 0

    def executemany(self, sql, seq):
        self._chunk_call_count += 1
        if self._chunk_call_count == self.fail_chunk_at_call:
            raise RuntimeError(
                f"simulated D1 chunk failure on call #{self._chunk_call_count}"
            )
        super().executemany(sql, seq)


def test_executemany_chunked_failure_records_partial_prefix_count(
    sqlite_conn, monkeypatch, tmp_path,
):
    """B.3 (2026-05-11): executemany chunk failure now raises
    ``DualWriteAsymmetryError`` instead of silently returning.

    The legacy path used to ``break`` out of the chunking loop without
    surfacing the failure to the caller; SQLite + D1 ended up with
    matching prefix rows while the remainder simply went missing. The
    raise is the contract that lets the surrounding ``get_db()``
    transaction rollback. ``first_failed_extra.partial_prefix_count``
    in the drift JSONL still records exactly where D1 stopped.
    """
    _strict_env(monkeypatch, False)
    # Force a tiny BATCH_LIMIT so we get multiple chunks for a small input.
    monkeypatch.setattr(_d1_client_module, "_BATCH_LIMIT", 2)
    drift = tmp_path / "drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift))

    fake_d1 = _ChunkAwareFakeD1(fail_chunk_at_call=2)
    dual = DualConnection(sqlite_conn, fake_d1)

    with pytest.raises(DualWriteAsymmetryError) as excinfo:
        dual.executemany(
            "INSERT INTO t (v) VALUES (?)",
            [("a",), ("b",), ("c",), ("d",), ("e",)],
        )
    assert "partial_prefix_count=2" in str(excinfo.value)

    # First chunk landed on both backends; the raise then aborted SQLite
    # writes too, so the chunk-1 prefix is still observable locally.
    sqlite_rows = sqlite_conn.execute(
        "SELECT v FROM t ORDER BY id"
    ).fetchall()
    assert [r["v"] for r in sqlite_rows] == ["a", "b"]
    # Drift record carries the structured prefix count.
    dual.rollback()  # flushes any pending drift JSONL record
    assert drift.exists()
    payload = [json.loads(line) for line in drift.read_text().splitlines() if line]
    extras = [
        rec.get("first_failed_extra") for rec in payload
        if rec.get("first_failed_extra")
    ]
    assert extras, f"expected first_failed_extra in {payload!r}"
    assert extras[0].get("partial_prefix_count") == 2


def test_executemany_chunked_failure_strict_mode_raises_for_guarded_table(
    tmp_path, monkeypatch,
):
    """P0-2 + P0-1: guarded-table chunk failure under strict raises."""
    _strict_env(monkeypatch, True)
    monkeypatch.setattr(_d1_client_module, "_BATCH_LIMIT", 1)
    drift = tmp_path / "drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift))

    sqlite_path = tmp_path / "guarded.db"
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    # Mirror enough of ReportSessions schema for the test
    conn.execute(
        "CREATE TABLE ReportSessions (Id INTEGER PRIMARY KEY, Note TEXT)"
    )

    fake_d1 = _ChunkAwareFakeD1(fail_chunk_at_call=1)
    dual = DualConnection(conn, fake_d1)

    with pytest.raises(DualWriteStrictError):
        dual.executemany(
            "INSERT INTO ReportSessions (Id, Note) VALUES (?, ?)",
            [(1, "a"), (2, "b")],
        )


def test_batch_execute_missing_d1_cursor_records_drift(
    sqlite_conn, monkeypatch, tmp_path,
):
    """B.3 (2026-05-11): a ``None`` D1 cursor on a write statement now
    raises ``DualWriteAsymmetryError`` in non-strict mode as well.

    Legacy behaviour returned the half-asymmetric DualCursor list back
    to the caller; the drift counter still moved but callers had no
    syntactic signal to abort. Non-strict raise matches the
    ``executemany`` contract above so any batched write that lost a
    mirror surfaces to the surrounding transaction.
    """
    _strict_env(monkeypatch, False)
    drift = tmp_path / "drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift))

    fake_d1 = FakeD1Connection()

    def _batch(statements):
        # Same length as statements but second entry is ``None`` to
        # simulate a malformed CF response losing a write cursor.
        return [FakeD1Cursor(lastrowid=1, rowcount=1), None]

    fake_d1.batch_execute = _batch  # type: ignore[attr-defined]

    dual = DualConnection(sqlite_conn, fake_d1)
    with pytest.raises(DualWriteAsymmetryError):
        dual.batch_execute([
            ("INSERT INTO t (v) VALUES (?)", ("a",)),
            ("INSERT INTO t (v) VALUES (?)", ("b",)),
        ])

    assert dual.d1_failure_count >= 1, (
        "expected _record_d1_failure to fire on the None write cursor"
    )


def test_batch_execute_missing_d1_cursor_strict_mode_raises(
    sqlite_conn, monkeypatch,
):
    """P0-3 + P0-1: strict mode raises on dropped batch_execute write."""
    _strict_env(monkeypatch, True)
    fake_d1 = FakeD1Connection()

    def _batch(statements):
        return [FakeD1Cursor(lastrowid=1, rowcount=1), None]

    fake_d1.batch_execute = _batch  # type: ignore[attr-defined]
    dual = DualConnection(sqlite_conn, fake_d1)

    with pytest.raises(DualWriteStrictError):
        dual.batch_execute([
            ("INSERT INTO t (v) VALUES (?)", ("a",)),
            ("INSERT INTO t (v) VALUES (?)", ("b",)),
        ])


def test_bracket_quoted_table_name_recognised():
    """P1: [Table]-style identifiers must route through the guarded-table check."""
    from javdb.storage.dual_connection import (
        _extract_insert_table,
    )

    assert _extract_insert_table(
        "INSERT INTO [ReportSessions] (Id) VALUES (1)"
    ) == "ReportSessions"
    assert _extract_insert_table(
        "INSERT INTO \"ReportSessions\" (Id) VALUES (1)"
    ) == "ReportSessions"
    assert _extract_insert_table(
        "INSERT INTO ReportSessions (Id) VALUES (1)"
    ) == "ReportSessions"
