from javdb.ops.reconcile.collectors import QbCollector


def test_collect_maps_progress_and_state():
    torrents = [
        {"hash": "a", "progress": 0.4, "state": "downloading"},
        {"hash": "b", "progress": 1.0, "state": "uploading"},
        {"hash": "c", "progress": 0.0, "state": "stalledDL"},
    ]
    obs = {o.qb_hash: o for o in QbCollector().collect(torrents)}
    assert obs["a"].state == "downloading"
    assert obs["b"].state == "completed"   # progress == 1.0
    assert obs["c"].state == "downloading"
    assert obs["a"].source == "qb"
    assert obs["a"].observed_at  # non-empty timestamp


def test_collect_skips_hashless_rows():
    obs = QbCollector().collect([{"progress": 1.0, "state": "uploading"}])
    assert obs == []
