"""ADR-055 D8: behavioral smoke for the system_state upsert (insert + conflict)."""
import sqlite3

import pytest

from javdb.storage.repos.system_state_repo import SystemStateRepo

_DDL = (
    "CREATE TABLE system_state ("
    "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
    "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    yield c
    c.close()


def test_put_inserts_then_updates_on_conflict(conn):
    repo = SystemStateRepo(conn)
    repo.put("onboarded", "false")
    assert repo.get("onboarded") == "false"
    repo.put("onboarded", "true")
    assert repo.get("onboarded") == "true"


def test_put_binds_key_and_value_in_order(conn):
    repo = SystemStateRepo(conn)
    repo.put("alpha", "A")
    repo.put("beta", "B")
    assert repo.get("alpha") == "A"
    assert repo.get("beta") == "B"
