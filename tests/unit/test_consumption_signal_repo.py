import sqlite3

import pytest

from javdb.ops.reconcile.models import ConsumptionSignalRecord
from javdb.storage.repos.consumption_signal_repo import ConsumptionSignalRepo

_DDL = """
CREATE TABLE ConsumptionSignal (
  video_code TEXT NOT NULL, source_type TEXT NOT NULL, instance TEXT NOT NULL,
  library_id TEXT NOT NULL, library_name TEXT, watched INTEGER, progress_pct INTEGER,
  play_count INTEGER, rating REAL, watched_at TEXT, resolved_confidence TEXT,
  observed_at TEXT, PRIMARY KEY (video_code, instance, library_id)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ConsumptionSignalRepo(c)


def test_upsert_then_get(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=True, rating=8.0, resolved_confidence="high",
        observed_at="t",
    ))
    got = repo.get("ABC-123", "plex-home", "3")
    assert got.watched is True       # must be bool True, not int 1
    assert got.rating == 8.0
    assert got.resolved_confidence == "high"


def test_watched_false_round_trips_as_bool(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="XYZ-999", source_type="emby", instance="emby-home",
        library_id="5", watched=False, observed_at="t",
    ))
    got = repo.get("XYZ-999", "emby-home", "5")
    assert got.watched is False      # must be bool False, not int 0 or None


def test_watched_none_round_trips_as_none(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="XYZ-000", source_type="emby", instance="emby-home",
        library_id="5", watched=None, observed_at="t",
    ))
    got = repo.get("XYZ-000", "emby-home", "5")
    assert got.watched is None


def test_upsert_full_replace_latest_wins(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=False, play_count=1, observed_at="t1",
    ))
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=True, play_count=3, observed_at="t2",
    ))
    got = repo.get("ABC-123", "plex-home", "3")
    assert got.watched is True       # replaced, not coalesced; must be bool True
    assert got.play_count == 3
    assert got.observed_at == "t2"


def test_distinct_instances_are_separate_rows(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=True, observed_at="t",
    ))
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="emby", instance="emby-nas",
        library_id="7", watched=False, observed_at="t",
    ))
    rows = repo.list_by_video_code("ABC-123")
    assert len(rows) == 2
    assert {r.instance for r in rows} == {"plex-home", "emby-nas"}
