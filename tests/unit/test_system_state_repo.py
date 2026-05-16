import json
import sqlite3
from pathlib import Path

import pytest

from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo


@pytest.fixture
def repo(tmp_path: Path) -> SystemStateRepo:
    db = tmp_path / "operations.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
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
    from packages.python.javdb_platform.db import _init_single_db, _OPERATIONS_DDL
    db = tmp_path / "ops.db"
    _init_single_db(str(db), _OPERATIONS_DDL, force=True)
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(str(db)).execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "system_state" in tables
