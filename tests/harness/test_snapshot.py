import json

from tests.harness.scenarios.golden_daily import golden_daily
from tests.harness.snapshot import capture_snapshot, diff_snapshots


def test_capture_snapshot_is_normalized(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())
    snap = capture_snapshot(pipeline_harness, result)

    # Stable, known canonical outputs of the clean daily run.
    assert snap["qb_hashes"] == ["a" * 40, "b" * 40]
    assert snap["events"] == [
        "RunStarted",
        "MovieDiscovered", "MovieDiscovered", "MovieSelected", "MovieSelected",
        "TorrentSelected", "TorrentSelected", "TorrentQueued", "TorrentQueued",
    ]
    assert len(snap["movies"]) == 2
    assert len(snap["torrents"]) == 2
    assert sorted(o["state"] for o in snap["acquisition"]) == ["queued", "queued"]

    # No nondeterministic fields leaked into the structured parts.
    for movie in snap["movies"]:
        assert set(movie) == {"video_code", "href"}
    for outcome in snap["acquisition"]:
        assert set(outcome) == {"qb_hash", "state"}
    blob = json.dumps(snap)
    assert "SessionId" not in blob
    assert "DateTime" not in blob


def test_diff_snapshots_reports_only_differences():
    a = {"qb_hashes": ["x"], "events": ["RunStarted"]}
    assert diff_snapshots(a, dict(a)) == []
    diffs = diff_snapshots(a, {"qb_hashes": ["y"], "events": ["RunStarted"]})
    assert len(diffs) == 1
    assert "qb_hashes" in diffs[0]
