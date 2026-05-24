import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, List

import pytest

from javdb.storage.repos.system_state_repo import SystemStateRepo
from javdb.storage import dual_connection as _dual_module
from javdb.storage.dual_connection import DualConnection, _is_read

_SYSTEM_STATE_DDL = (
    "CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
    " updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
)


@pytest.fixture
def repo(tmp_path: Path) -> SystemStateRepo:
    db = tmp_path / "operations.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(_SYSTEM_STATE_DDL)
        conn.commit()
    conn = sqlite3.connect(str(db))
    return SystemStateRepo(conn)


def test_get_missing_returns_default(repo):
    assert repo.get("does-not-exist") is None
    assert repo.get("does-not-exist", default="fallback") == "fallback"


def test_put_then_get_roundtrip(repo):
    repo.put("onboarded", "true")
    assert repo.get("onboarded") == "true"


def test_put_json_helper(repo):
    repo.put_json("dismissed_hints", ["smtp", "pikpak"])
    assert repo.get_json("dismissed_hints") == ["smtp", "pikpak"]


def test_put_overwrites(repo):
    repo.put("k", "v1")
    repo.put("k", "v2")
    assert repo.get("k") == "v2"


def test_delete(repo):
    repo.put("k", "v")
    repo.delete("k")
    assert repo.get("k") is None


def test_init_db_creates_system_state_table(tmp_path, monkeypatch):
    """Regression: _OPERATIONS_DDL must declare the system_state table,
    not just the migration SQL file that nobody auto-loads."""
    from javdb.storage.db import _init_single_db, _OPERATIONS_DDL
    db = tmp_path / "ops.db"
    _init_single_db(str(db), _OPERATIONS_DDL, force=True)
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(str(db)).execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "system_state" in tables


# ── Fake D1 helpers ───────────────────────────────────────────────────────


class FakeD1Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.lastrowid = 1
        self.rowcount = len(self._rows) or 1

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeD1Connection:
    """Records every call so tests can assert on D1 behaviour."""

    def __init__(self, select_rows=None):
        self.executed: List[tuple] = []
        self.commits = 0
        self._select_rows = select_rows or []

    def execute(self, sql: str, params: Iterable[Any] = ()):
        self.executed.append((sql, list(params)))
        if _is_read(sql):
            return FakeD1Cursor(rows=self._select_rows)
        return FakeD1Cursor()

    def executemany(self, sql, seq):
        pass

    def commit(self):
        self.commits += 1

    def close(self):
        pass


# ── D1-mode tests ─────────────────────────────────────────────────────────


def test_repo_works_with_d1_connection():
    """SystemStateRepo correctly issues SQL against a D1-like connection."""
    fake_d1 = FakeD1Connection(select_rows=[{"value": "true"}])
    repo = SystemStateRepo(fake_d1)

    repo.put("onboarded", "true")
    # Verify an INSERT/UPSERT was sent
    write_sqls = [sql for sql, _ in fake_d1.executed if not _is_read(sql)]
    assert len(write_sqls) == 1
    assert "system_state" in write_sqls[0]

    value = repo.get("onboarded")
    # Verify a SELECT was sent and the dict row was read correctly
    read_sqls = [sql for sql, _ in fake_d1.executed if _is_read(sql)]
    assert any("system_state" in sql for sql in read_sqls)
    assert value == "true"


def test_get_handles_d1_dict_rows():
    """Regression for row[0] bug: D1 returns dict rows, not tuples."""
    fake_d1 = FakeD1Connection(select_rows=[{"value": "hello"}])
    repo = SystemStateRepo(fake_d1)
    assert repo.get("any-key") == "hello"


# ── Dual-mode tests ───────────────────────────────────────────────────────


@pytest.fixture
def sqlite_ops_conn(tmp_path):
    path = tmp_path / "operations.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(_SYSTEM_STATE_DDL)
    conn.commit()
    return conn


def test_repo_works_with_dual_connection(sqlite_ops_conn, tmp_path, monkeypatch):
    """Both SQLite and D1 backends receive writes; get() reads from D1."""
    drift_path = tmp_path / "d1_drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = FakeD1Connection(select_rows=[{"value": "dual-val"}])
    dual = DualConnection(sqlite_ops_conn, fake_d1, logical_name="operations")

    repo = SystemStateRepo(dual)
    repo.put("test-key", "dual-val")

    # SQLite received the write
    sqlite_ops_conn.commit()
    row = sqlite_ops_conn.execute(
        "SELECT value FROM system_state WHERE key = ?", ("test-key",)
    ).fetchone()
    assert row is not None
    assert row["value"] == "dual-val"

    # D1 also received the write
    write_sqls = [sql for sql, _ in fake_d1.executed if not _is_read(sql)]
    assert any("system_state" in sql for sql in write_sqls)

    # get() returns the D1 dict row value
    value = repo.get("test-key")
    assert value == "dual-val"
