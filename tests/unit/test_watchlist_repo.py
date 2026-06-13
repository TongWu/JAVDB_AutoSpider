"""Unit tests for WatchIntentRepo (ADR-054 WS1)."""

import pathlib
import sqlite3

import pytest

from javdb.storage.repos.watchlist_repo import WatchIntentRepo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WATCH_INTENT_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_watch.db")
    conn = sqlite3.connect(path)
    conn.executescript(_WATCH_INTENT_DDL)
    conn.commit()
    conn.close()
    return path


def test_upsert_creates_row(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    row = repo.upsert(video_code="ABC-001", href="/v/abc001", status="want")
    assert row["video_code"] == "ABC-001"
    assert row["href"] == "/v/abc001"
    assert row["status"] == "want"
    assert row["updated_at"]


def test_upsert_updates_status_in_place(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    repo.upsert(video_code="ABC-001", href="/v/abc001", status="want")
    row = repo.upsert(video_code="ABC-001", href="/v/abc001", status="viewed")
    assert row["status"] == "viewed"
    items, total = repo.list()
    assert total == 1  # upsert, not a second row


def test_get_returns_none_when_absent(db_path):
    assert WatchIntentRepo(db_path=db_path).get("NOPE-999") is None


def test_list_filters_by_status(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    repo.upsert(video_code="A-1", href="/v/a1", status="want")
    repo.upsert(video_code="B-2", href="/v/b2", status="viewed")
    items, total = repo.list(status="want")
    assert total == 1
    assert items[0]["video_code"] == "A-1"


def test_delete_removes_row(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    repo.upsert(video_code="A-1", href="/v/a1", status="want")
    assert repo.delete("A-1") is True
    assert repo.get("A-1") is None
    assert repo.delete("A-1") is False  # already gone
