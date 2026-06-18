"""ADR-055 D8: behavioral smoke for the ActorSubscription upsert (column->value)."""
import pathlib
import sqlite3

import pytest

from javdb.storage import db as _db
from javdb.storage.repos.subscription_repo import ActorSubscriptionRepo

_DDL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    return ActorSubscriptionRepo(db_path=path)


def test_upsert_maps_each_column_to_the_right_value(repo):
    row = repo.upsert(actor_href="/actors/EvkJ", actor_name="Alice", active=1)
    assert row["actor_href"] == "/actors/EvkJ"
    assert row["actor_name"] == "Alice"
    assert row["active"] == 1


def test_upsert_refreshes_name_and_active_on_conflict(repo):
    repo.upsert(actor_href="/actors/EvkJ", actor_name="Alice", active=1)
    row = repo.upsert(actor_href="/actors/EvkJ", actor_name="Alicia", active=0)
    assert row["actor_href"] == "/actors/EvkJ"  # primary key preserved on conflict
    assert row["actor_name"] == "Alicia"
    assert row["active"] == 0
