# tests/unit/test_actor_metadata_repo.py
import sqlite3

import pytest

from javdb.storage.repos.actor_metadata_repo import ActorMetadataRepo

_DDL = """
CREATE TABLE ActorMetadata (
  actor_href TEXT PRIMARY KEY, actor_name TEXT, birthdate TEXT, source TEXT,
  source_url TEXT, resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
"""


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return ActorMetadataRepo(conn)


def test_get_missing_returns_none(repo):
    assert repo.get("/actors/x") is None


def test_upsert_then_get(repo):
    repo.upsert("/actors/x", "Some Name", "1990-05-20", "minnano-av", "https://m/x")
    row = repo.get("/actors/x")
    assert row["birthdate"] == "1990-05-20"
    assert row["source"] == "minnano-av"
    assert int(row["resolved"]) == 1


def test_upsert_negative_cache(repo):
    repo.upsert("/actors/y", "Unknown", None, "", "")
    row = repo.get("/actors/y")
    assert row["birthdate"] is None
    assert int(row["resolved"]) == 1  # looked up, not found


def test_upsert_overwrites(repo):
    repo.upsert("/actors/x", "N", None, "", "")
    repo.upsert("/actors/x", "N", "1988-01-02", "minnano-av", "https://m/1")
    assert repo.get("/actors/x")["birthdate"] == "1988-01-02"


def test_delete(repo):
    repo.upsert("/actors/x", "N", "1990-01-01", "minnano-av", "u")
    repo.delete("/actors/x")
    assert repo.get("/actors/x") is None


def test_list_all_order_and_keys(repo):
    # Insert out of order; expect results sorted by actor_href ascending.
    repo.upsert("/actors/z", "Last", "1985-03-15", "minnano-av", "https://m/z")
    repo.upsert("/actors/a", "First", "1990-05-20", "minnano-av", "https://m/a")
    # Negative-cache row (birthdate None)
    repo.upsert("/actors/m", "Middle", None, "", "")

    rows = repo.list_all()
    assert len(rows) == 3
    assert [r["actor_href"] for r in rows] == ["/actors/a", "/actors/m", "/actors/z"]

    expected_keys = {"actor_href", "actor_name", "birthdate", "source", "source_url", "resolved"}
    for row in rows:
        assert set(row.keys()) >= expected_keys

    # Negative-cache row
    neg = next(r for r in rows if r["actor_href"] == "/actors/m")
    assert neg["birthdate"] is None
    assert int(neg["resolved"]) == 1

    # Normal row
    first = rows[0]
    assert first["actor_href"] == "/actors/a"
    assert first["birthdate"] == "1990-05-20"
    assert int(first["resolved"]) == 1
