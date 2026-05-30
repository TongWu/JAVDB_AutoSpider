from dataclasses import FrozenInstanceError

import pytest

from javdb.ops.reconcile.models import (
    AcquisitionOutcomeRecord,
    Observation,
    ReconcileOptions,
    ReconcileResult,
    utc_now_iso,
)


def test_utc_now_iso_has_trailing_z():
    assert utc_now_iso().endswith("Z")


def test_record_defaults_to_queued():
    rec = AcquisitionOutcomeRecord(qb_hash="abc", href="/v/1")
    assert rec.state == "queued"
    assert rec.video_code is None


def test_observation_is_frozen():
    obs = Observation(source="qb", qb_hash="abc", state="downloading", observed_at="t")
    assert obs.source == "qb"
    with pytest.raises(FrozenInstanceError):
        obs.source = "local"  # type: ignore[misc]


def test_reconcile_result_starts_empty():
    res = ReconcileResult()
    assert res.observed == 0
    assert res.errors == []


def test_reconcile_options_defaults():
    opts = ReconcileOptions()
    assert opts.sources == ("qb",)
    assert opts.stalled_after_days == 7
    assert opts.dry_run is False
