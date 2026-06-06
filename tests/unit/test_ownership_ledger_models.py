import dataclasses

import pytest

from javdb.ops.reconcile.models import (
    OWNERSHIP_SOURCES,
    PERSISTENT_OWNERSHIP_SOURCES,
    OwnershipLedgerRecord,
    OwnershipObservation,
    OwnershipOptions,
    OwnershipResult,
)


def test_ownership_sources_vocabulary():
    assert OWNERSHIP_SOURCES == ("qb", "nas", "gdrive", "pikpak")
    assert PERSISTENT_OWNERSHIP_SOURCES == frozenset({"gdrive", "nas"})


def test_record_defaults():
    rec = OwnershipLedgerRecord(video_code="ABC-1", source="gdrive", category="无码破解|中字")
    assert rec.present == 1
    assert rec.path is None
    assert rec.size is None


def test_category_defaults_to_empty_not_none():
    rec = OwnershipLedgerRecord(video_code="ABC-1", source="pikpak")
    assert rec.category == ""  # NOT NULL contract (D-P2-1)


def test_options_default_to_all_sources():
    opts = OwnershipOptions()
    assert set(opts.sources) == set(OWNERSHIP_SOURCES)
    assert opts.dry_run is False


def test_result_starts_empty():
    res = OwnershipResult()
    assert res.observed == 0
    assert res.upserted == 0
    assert res.swept_absent == 0
    assert res.marked_in_library == 0
    assert res.errors == []


def test_ownership_observation_is_frozen_with_defaults():
    obs = OwnershipObservation(source="nas", video_code="ABC-1")
    assert obs.category == ""
    assert obs.path is None
    assert obs.size is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.source = "qb"  # type: ignore[misc]
