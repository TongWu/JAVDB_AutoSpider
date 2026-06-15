"""Unit tests for ActorSubscriptionRepo + NewWorksRepo (ADR-054 WS2)."""

import pathlib
import sqlite3

import pytest

from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_subs.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return path


# -- ActorSubscriptionRepo -------------------------------------------------


def test_upsert_creates_subscription(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    row = repo.upsert(actor_href="/actors/EvkJ", actor_name="Some Name")
    assert row["actor_href"] == "/actors/EvkJ"
    assert row["actor_name"] == "Some Name"
    assert row["active"] == 1
    assert row["updated_at"]


def test_upsert_updates_in_place(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/EvkJ", actor_name="Old")
    repo.upsert(actor_href="/actors/EvkJ", actor_name="New", active=0)
    items, total = repo.list()
    assert total == 1
    assert items[0]["actor_name"] == "New"
    assert items[0]["active"] == 0


def test_upsert_preserves_cursor_on_follow_update(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/EvkJ", actor_name="Old")
    repo.advance_cursor("/actors/EvkJ", last_seen_href="/v/newest")

    repo.upsert(actor_href="/actors/EvkJ", actor_name="New", active=1)

    row = repo.get("/actors/EvkJ")
    assert row["last_seen_href"] == "/v/newest"
    assert row["last_checked_at"]


def test_get_returns_none_when_absent(db_path):
    assert ActorSubscriptionRepo(db_path=db_path).get("/actors/nope") is None


def test_list_active_only_filters(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    repo.upsert(actor_href="/actors/B", actor_name="B", active=0)
    items, total = repo.list(active_only=True)
    assert total == 1
    assert items[0]["actor_href"] == "/actors/A"


def test_list_active_hrefs(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    repo.upsert(actor_href="/actors/B", actor_name="B", active=0)
    assert repo.list_active_hrefs() == ["/actors/A"]


def test_advance_cursor_sets_last_seen_and_checked(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    repo.advance_cursor("/actors/A", last_seen_href="/v/newest")
    row = repo.get("/actors/A")
    assert row["last_seen_href"] == "/v/newest"
    assert row["last_checked_at"]


def test_delete_removes_subscription(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    assert repo.delete("/actors/A") is True
    assert repo.get("/actors/A") is None
    assert repo.delete("/actors/A") is False


# -- NewWorksRepo ----------------------------------------------------------


def test_add_new_work_is_idempotent(db_path):
    repo = NewWorksRepo(db_path=db_path)
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/A"
    ) is True
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/A"
    ) is False
    items, total = repo.list()
    assert total == 1
    assert items[0]["video_code"] == "ABC-001"


def test_list_excludes_dismissed_by_default(db_path):
    repo = NewWorksRepo(db_path=db_path)
    repo.add(video_code="A-1", href="/v/a1", actor_href="/actors/A")
    repo.add(video_code="B-2", href="/v/b2", actor_href="/actors/A")
    assert repo.dismiss("B-2") is True
    items, total = repo.list()
    assert total == 1
    assert items[0]["video_code"] == "A-1"
    _, total_all = repo.list(include_dismissed=True)
    assert total_all == 2


def test_list_filters_by_actor(db_path):
    repo = NewWorksRepo(db_path=db_path)
    repo.add(video_code="A-1", href="/v/a1", actor_href="/actors/A")
    repo.add(video_code="B-1", href="/v/b1", actor_href="/actors/B")
    items, total = repo.list(actor_href="/actors/B")
    assert total == 1
    assert items[0]["video_code"] == "B-1"


def test_dismiss_returns_false_when_absent(db_path):
    repo = NewWorksRepo(db_path=db_path)
    assert repo.dismiss("NOPE-999") is False
