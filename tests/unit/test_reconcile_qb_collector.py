from javdb.ops.reconcile.collectors import QbCollector


def test_collect_maps_progress_and_state():
    torrents = [
        {"hash": "a", "progress": 0.4, "state": "downloading"},
        {"hash": "b", "progress": 1.0, "state": "downloading"},
        {"hash": "c", "progress": 0.9, "state": "stalledUP"},
        {"hash": "d", "progress": 0.8, "state": "seeding"},
        {"hash": "e", "progress": 0.0, "state": "stalledDL"},
    ]
    obs = {o.qb_hash: o for o in QbCollector().collect(torrents)}
    assert obs["a"].state == "downloading"
    assert obs["b"].state == "completed"   # progress == 1.0
    assert obs["c"].state == "completed"   # completed-like qB state
    assert obs["d"].state == "completed"   # seeding counts as completed
    assert obs["e"].state == "downloading"
    assert obs["a"].source == "qb"
    assert obs["a"].observed_at  # non-empty timestamp


def test_collect_skips_hashless_rows():
    obs = QbCollector().collect([{"progress": 1.0, "state": "uploading"}])
    assert obs == []
