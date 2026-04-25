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

from packages.python.javdb_platform.d1_client import (  # noqa: E402
    D1Connection,
    D1Cursor,
    D1PermanentError,
    D1TransientError,
    _split,
)
from packages.python.javdb_platform import d1_client as _d1_client_module  # noqa: E402
from packages.python.javdb_platform import dual_connection as _dual_module  # noqa: E402
from packages.python.javdb_platform.dual_connection import (  # noqa: E402
    DualConnection,
    _is_read,
)


# ── _is_read ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  SELECT * FROM x",
        "WITH foo AS (SELECT 1) SELECT * FROM foo",
        "PRAGMA table_info(x)",
        "EXPLAIN SELECT 1",
        "-- comment line\nSELECT 1",
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
    ],
)
def test_is_read_false(sql):
    assert not _is_read(sql)


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

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
    d1_conn.execute("SELECT * FROM t WHERE id = ?", (1,))
    assert isinstance(captured["json"], dict)
    assert captured["json"] == {"sql": "SELECT * FROM t WHERE id = ?", "params": [1]}


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

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
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

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
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

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
    d1_conn.execute("SELECT 1")
    assert len(calls) == 2
    assert no_sleep == [2.0]


def test_no_retry_on_4xx(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(status_code=400, text="bad request")

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
    with pytest.raises(D1PermanentError):
        d1_conn.execute("INSERT INTO x VALUES (1)")
    assert len(calls) == 1
    assert no_sleep == []


def test_retry_exhausted_raises_transient(monkeypatch, d1_conn, no_sleep):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(status_code=503, text="still down")

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
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

    monkeypatch.setattr(_d1_client_module.requests, "post", fake_post)
    d1_conn.execute("SELECT 1")
    assert len(calls) == 2


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


def test_current_backend_reflects_env(monkeypatch):
    """``current_backend()`` should reflect the active STORAGE_BACKEND."""
    from packages.python.javdb_platform import db as _db

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
