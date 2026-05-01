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
        "WITH foo AS (SELECT 1) SELECT * FROM foo",
        "WITH foo AS (SELECT 1) INSERT INTO x SELECT * FROM foo",
        "WITH foo AS (SELECT 1) UPDATE x SET y=1",
        "WITH foo AS (SELECT 1) DELETE FROM x",
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

    monkeypatch.setattr(d1_conn, "_post_request", fake_post)
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

    from packages.python.javdb_platform import db as _db

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

    import packages.python.javdb_platform.config_helper as _cfg
    from packages.python.javdb_platform import db as _db

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
