import sqlite3

import pytest

from javdb.ops.reconcile.models import UnresolvedMediaItemRecord
from javdb.storage.repos.unresolved_media_item_repo import UnresolvedMediaItemRepo

_DDL = """
CREATE TABLE UnresolvedMediaItem (
  instance TEXT NOT NULL, source_type TEXT, library_id TEXT NOT NULL,
  library_name TEXT, item_id TEXT NOT NULL, raw_title TEXT, file_path TEXT,
  observed_at TEXT, PRIMARY KEY (instance, library_id, item_id)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return UnresolvedMediaItemRepo(c)


def test_upsert_then_get(repo):
    repo.upsert(UnresolvedMediaItemRecord(
        instance="emby-nas", source_type="emby", library_id="7", item_id="42",
        raw_title="Mystery Movie", file_path="/m/x.mkv", observed_at="t",
    ))
    got = repo.get("emby-nas", "7", "42")
    assert got.raw_title == "Mystery Movie"


def test_reobservation_is_idempotent(repo):
    rec = UnresolvedMediaItemRecord(
        instance="emby-nas", library_id="7", item_id="42", observed_at="t1",
    )
    repo.upsert(rec)
    rec.observed_at = "t2"
    repo.upsert(rec)
    got = repo.get("emby-nas", "7", "42")
    assert got.observed_at == "t2"
    assert repo._conn.execute("SELECT COUNT(*) FROM UnresolvedMediaItem").fetchone()[0] == 1


def test_delete_removes_row(repo):
    repo.upsert(UnresolvedMediaItemRecord(
        instance="emby-nas", library_id="7", item_id="42", observed_at="t",
    ))
    assert repo.get("emby-nas", "7", "42") is not None
    repo.delete("emby-nas", "7", "42")
    assert repo.get("emby-nas", "7", "42") is None
    assert repo._conn.execute("SELECT COUNT(*) FROM UnresolvedMediaItem").fetchone()[0] == 0


def test_delete_missing_row_is_noop(repo):
    # Deleting a row that was never inserted must not raise (idempotent).
    repo.delete("emby-nas", "7", "does-not-exist")
    # And it must not disturb a sibling row sharing the same instance/library.
    repo.upsert(UnresolvedMediaItemRecord(
        instance="emby-nas", library_id="7", item_id="42", observed_at="t",
    ))
    repo.delete("emby-nas", "7", "other")
    assert repo.get("emby-nas", "7", "42") is not None
