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


def test_add_same_video_code_under_two_actors_keeps_both(db_path):
    # issue #223: a release that surfaces under two followed actors must persist
    # one row per actor; the old single-column video_code PK dropped the second.
    repo = NewWorksRepo(db_path=db_path)
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/A"
    ) is True
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/B"
    ) is True

    _, total = repo.list()
    assert total == 2
    items_a, total_a = repo.list(actor_href="/actors/A")
    items_b, total_b = repo.list(actor_href="/actors/B")
    assert total_a == 1 and items_a[0]["video_code"] == "ABC-001"
    assert total_b == 1 and items_b[0]["video_code"] == "ABC-001"
    # Re-scraping the same (actor, video) stays idempotent per actor.
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/B"
    ) is False


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


# -- In-place schema upgrade (issue #223) ----------------------------------

_OLD_NEWWORKS_DDL = """
CREATE TABLE NewWorks (
  video_code    TEXT PRIMARY KEY,
  href          TEXT NOT NULL,
  actor_href    TEXT NOT NULL,
  title         TEXT,
  release_date  TEXT,
  discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  dismissed     INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0,1))
);
"""


def _pk_cols(conn):
    cols = conn.execute("PRAGMA table_info(NewWorks)").fetchall()
    return [r[1] for r in sorted((c for c in cols if c[5] > 0), key=lambda c: c[5])]


def test_ensure_newworks_composite_pk_upgrades_old_single_column_pk(tmp_path):
    from javdb.storage.db._db_migrations import _ensure_newworks_composite_pk

    path = str(tmp_path / "old_schema.db")
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_NEWWORKS_DDL)
    conn.execute(
        "INSERT INTO NewWorks(video_code, href, actor_href, title, dismissed) "
        "VALUES ('ABC-001', '/v/abc001', '/actors/A', 't1', 1)"
    )
    conn.commit()
    assert _pk_cols(conn) == ["video_code"]

    _ensure_newworks_composite_pk(conn)

    # PK is now composite and the existing row (with dismissed state) survived.
    assert _pk_cols(conn) == ["actor_href", "video_code"]
    row = conn.execute(
        "SELECT actor_href, title, dismissed FROM NewWorks WHERE video_code='ABC-001'"
    ).fetchone()
    assert row == ("/actors/A", "t1", 1)
    # The cross-actor write that used to collide on the old PK now persists.
    conn.execute(
        "INSERT OR IGNORE INTO NewWorks(video_code, href, actor_href) "
        "VALUES ('ABC-001', '/v/abc001', '/actors/B')"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM NewWorks WHERE video_code='ABC-001'"
    ).fetchone()[0] == 2

    # Idempotent: a second run is a no-op on the already-composite table.
    _ensure_newworks_composite_pk(conn)
    assert _pk_cols(conn) == ["actor_href", "video_code"]
    conn.close()


def test_ensure_newworks_composite_pk_noops_on_fresh_schema(db_path):
    # The db_path fixture builds NewWorks from the canonical (composite) DDL.
    from javdb.storage.db._db_migrations import _ensure_newworks_composite_pk

    conn = sqlite3.connect(db_path)
    assert _pk_cols(conn) == ["actor_href", "video_code"]
    _ensure_newworks_composite_pk(conn)  # must not raise / rebuild
    assert _pk_cols(conn) == ["actor_href", "video_code"]
    conn.close()
