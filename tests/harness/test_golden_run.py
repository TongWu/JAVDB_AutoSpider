import os

import pytest

from tests.harness.golden_run import golden_path, load_golden, save_golden
from tests.harness.scenarios.golden_daily import golden_daily
from tests.harness.snapshot import capture_snapshot, diff_snapshots


def test_golden_round_trip(tmp_path, monkeypatch):
    import tests.harness.golden_run as gr
    monkeypatch.setattr(gr, "_GOLDEN_DIR", str(tmp_path / "golden_runs"))
    snap = {"qb_hashes": ["a" * 40], "events": ["RunStarted"]}
    save_golden("unit", snap)
    assert load_golden("unit") == snap
    assert golden_path("unit").endswith("unit/snapshot.json")


_GOLDEN_NAME = "daily"


def test_golden_daily_run_matches_snapshot(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())
    actual = capture_snapshot(pipeline_harness, result)

    # Only an explicit truthy value blesses (matches record_enabled()); a falsy
    # JAVDB_HARNESS_BLESS=0/false/off must NOT overwrite the committed baseline.
    if os.environ.get("JAVDB_HARNESS_BLESS", "").strip().lower() in ("1", "true", "yes", "on"):
        save_golden(_GOLDEN_NAME, actual)
        pytest.skip(f"blessed golden snapshot '{_GOLDEN_NAME}'")

    expected = load_golden(_GOLDEN_NAME)
    diffs = diff_snapshots(expected, actual)
    assert diffs == [], "golden-run drift detected:\n" + "\n".join(diffs)
