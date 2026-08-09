"""Unit tests for run_consumption (ADR-033 Phase 3, Task 8)."""
import sqlite3

import pytest

from javdb.ops.reconcile import service
from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import (
    ConsumptionOptions,
    MediaItem,
    UnresolvedMediaItemRecord,
)
from javdb.storage.repos.consumption_signal_repo import ConsumptionSignalRepo
from javdb.storage.repos.unresolved_media_item_repo import UnresolvedMediaItemRepo


def _unresolved_count(conn):
    """Row count behind the consumption-KPI "unresolved" card.

    This is the exact SQL emitted by the read-API builder
    ``build_consumption_summary_unresolved_count_query``
    (``SELECT COUNT(*) FROM UnresolvedMediaItem``). That builder ships in the
    read layer of PR #198, which is not merged into this writer-side branch, so
    we assert the equivalent literal here — keeping the test decoupled from the
    unmerged read API while still proving the KPI count drops on resolve."""
    return conn.execute("SELECT COUNT(*) FROM UnresolvedMediaItem").fetchone()[0]

_SIGNAL_DDL = """
CREATE TABLE ConsumptionSignal (
  video_code TEXT NOT NULL, source_type TEXT NOT NULL, instance TEXT NOT NULL,
  library_id TEXT NOT NULL, library_name TEXT, watched INTEGER, progress_pct INTEGER,
  play_count INTEGER, rating REAL, watched_at TEXT, resolved_confidence TEXT,
  observed_at TEXT, PRIMARY KEY (video_code, instance, library_id)
);
"""
_UNRESOLVED_DDL = """
CREATE TABLE UnresolvedMediaItem (
  instance TEXT NOT NULL, source_type TEXT, library_id TEXT NOT NULL,
  library_name TEXT, item_id TEXT NOT NULL, raw_title TEXT, file_path TEXT,
  observed_at TEXT, PRIMARY KEY (instance, library_id, item_id)
);
"""


@pytest.fixture
def repos():
    c = sqlite3.connect(":memory:")
    c.executescript(_SIGNAL_DDL + _UNRESOLVED_DDL)
    return ConsumptionSignalRepo(c), UnresolvedMediaItemRepo(c)


class _FakeAdapter:
    def __init__(self, config, items):
        self.config = config
        self._items = items

    def list_items(self, since):
        return self._items


class _BoomAdapter:
    def __init__(self, config):
        self.config = config

    def list_items(self, since):
        raise ConnectionError("401 unauthorized")


def _cfg(instance="plex-home", st="plex"):
    return MediaServerConfig(st, instance, "http://h", "TOKEN", ())


def test_resolved_item_writes_signal(repos):
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    item = MediaItem(instance="plex-home", source_type="plex", library_id="3",
                     library_name="JAV", item_id="1",
                     file_path="/m/ABC-123/ABC-123.mp4", watched=True, rating=8.0)
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )
    got = signal_repo.get("ABC-123", "plex-home", "3")
    assert got.watched is True
    assert got.resolved_confidence == "high"
    assert res.signals_updated == 1
    assert res.resolved_high == 1
    assert res.marked_unresolved == 0


def test_unresolved_item_is_persisted_and_counted(repos):
    signal_repo, unresolved_repo = repos
    cfg = _cfg("emby-nas", "emby")
    item = MediaItem(instance="emby-nas", source_type="emby", library_id="7",
                     library_name="Misc", item_id="42",
                     file_path="/m/clip.mp4", title="Family Vacation 2024")
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )
    assert res.marked_unresolved == 1
    assert res.signals_updated == 0
    assert unresolved_repo.get("emby-nas", "7", "42") is not None


def test_dead_instance_fails_open(repos):
    signal_repo, unresolved_repo = repos
    good = _cfg("plex-home", "plex")
    bad = _cfg("emby-dead", "emby")
    good_item = MediaItem(instance="plex-home", source_type="plex", library_id="3",
                          item_id="1", file_path="/m/MIDV-001.mp4", watched=True)
    res = service.run_consumption(
        ConsumptionOptions(servers=[bad, good]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={good.instance: _FakeAdapter(good, [good_item]),
                  bad.instance: _BoomAdapter(bad)},
    )
    # bad instance recorded an error but did not abort the good one.
    assert any("emby-dead" in e or "401" in e for e in res.errors)
    assert signal_repo.get("MIDV-001", "plex-home", "3") is not None
    assert res.instances_observed == 1   # only the reachable one counted as observed


def test_dry_run_writes_nothing(repos):
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    item = MediaItem(instance="plex-home", source_type="plex", library_id="3",
                     item_id="1", file_path="/m/ABC-123.mp4", watched=True)
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg], dry_run=True),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )
    # DB stays empty — nothing written
    assert signal_repo.get("ABC-123", "plex-home", "3") is None
    # Read/resolve counters still accumulate (pre-gate)
    assert res.instances_observed == 1
    assert res.items_observed == 1
    assert res.resolved_high == 1
    assert res.resolved_medium == 0
    assert res.resolved_low == 0
    assert res.marked_unresolved == 0
    # signals_updated is 0 — no writes occurred
    assert res.signals_updated == 0


def test_full_replace_overwrites_on_second_run(repos):
    """Second run for the same (video_code, instance, library_id) replaces, not preserves."""
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    base = dict(instance="plex-home", source_type="plex", library_id="3",
                item_id="1", file_path="/m/ABC-123.mp4")

    first_item = MediaItem(**base, watched=False, rating=6.0)
    service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [first_item])},
    )
    first_row = signal_repo.get("ABC-123", "plex-home", "3")
    assert first_row is not None
    assert first_row.watched is False
    assert first_row.rating == pytest.approx(6.0)

    second_item = MediaItem(**base, watched=True, rating=9.5)
    res2 = service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [second_item])},
    )
    second_row = signal_repo.get("ABC-123", "plex-home", "3")
    assert second_row.watched is True
    assert second_row.rating == pytest.approx(9.5)
    assert res2.signals_updated == 1
    # Exactly one row for this key
    rows = signal_repo._conn.execute(
        "SELECT COUNT(*) FROM ConsumptionSignal WHERE video_code='ABC-123'"
        " AND instance='plex-home' AND library_id='3'"
    ).fetchone()[0]
    assert rows == 1


def test_resolved_item_deletes_stale_unresolved_row(repos):
    """A previously-unresolved item that LATER resolves is removed from
    UnresolvedMediaItem, so the consumption KPI stops over-reporting it as
    unresolved (Codex review on PR #198). The two tables share no key, so this
    delete-on-resolve in the writer is the only place the stale row can go."""
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    pk = dict(instance="plex-home", library_id="3", item_id="1")

    # First observation failed code resolution → a stale unresolved row exists.
    unresolved_repo.upsert(UnresolvedMediaItemRecord(
        source_type="plex", library_name="JAV", raw_title="mystery",
        file_path="/m/mystery.mp4", observed_at="2026-01-01T00:00:00Z", **pk,
    ))
    assert unresolved_repo.get("plex-home", "3", "1") is not None
    assert _unresolved_count(unresolved_repo._conn) == 1

    # Same server-side item now resolves to a code (filename fixed / resolver
    # improved): same (instance, library_id, item_id), resolvable file_path.
    item = MediaItem(source_type="plex", library_name="JAV",
                     file_path="/m/ABC-123/ABC-123.mp4", watched=True, **pk)
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )

    # Signal landed AND the stale unresolved row is gone (KPI count drops 1→0).
    assert signal_repo.get("ABC-123", "plex-home", "3") is not None
    assert res.signals_updated == 1
    assert unresolved_repo.get("plex-home", "3", "1") is None
    assert _unresolved_count(unresolved_repo._conn) == 0


def test_dry_run_does_not_delete_unresolved_row(repos):
    """dry_run resolves codes but writes nothing — the stale unresolved row and
    its KPI count must survive untouched."""
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    pk = dict(instance="plex-home", library_id="3", item_id="1")
    unresolved_repo.upsert(UnresolvedMediaItemRecord(
        source_type="plex", file_path="/m/mystery.mp4", observed_at="t", **pk,
    ))

    item = MediaItem(source_type="plex", file_path="/m/ABC-123/ABC-123.mp4",
                     watched=True, **pk)
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg], dry_run=True),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )

    assert res.signals_updated == 0
    assert unresolved_repo.get("plex-home", "3", "1") is not None
    assert _unresolved_count(unresolved_repo._conn) == 1
