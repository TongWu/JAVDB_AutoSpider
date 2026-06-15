"""ADR-055 D8: behavioral smoke for the WatchIntent upsert (column->value mapping)."""

import pathlib
import sqlite3

import pytest

from javdb.storage import db as _db
from javdb.storage.repos.watchlist_repo import WatchIntentRepo

_DDL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    return WatchIntentRepo(db_path=path)


def test_upsert_maps_each_column_to_the_right_value(repo):
    # Distinct value per column so a swapped bind order would be caught.
    row = repo.upsert(video_code="AAA-111", href="/v/aaa", status="want", notes="mine")
    assert row["video_code"] == "AAA-111"
    assert row["href"] == "/v/aaa"
    assert row["status"] == "want"
    assert row["notes"] == "mine"


def test_upsert_preserves_notes_when_omitted(repo):
    repo.upsert(video_code="AAA-111", href="/v/aaa", status="want", notes="keep me")
    # status-only update with notes=None must NOT wipe the stored note (COALESCE).
    row = repo.upsert(video_code="AAA-111", href="/v/aaa", status="viewed", notes=None)
    assert row["status"] == "viewed"
    assert row["notes"] == "keep me"
