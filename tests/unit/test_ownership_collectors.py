# tests/unit/test_ownership_collectors.py
from javdb.ops.reconcile.collectors import (
    GdriveOwnershipCollector,
    NasOwnershipCollector,
    PikpakOwnershipCollector,
    QbOwnershipCollector,
)
from javdb.spider.services.dedup_types import RcloneEntry


def _gdrive_inventory():
    return {
        "ABC-1": [
            RcloneEntry("ABC-1", "无码破解", "中字", "/g/small", 10, 1, "t"),
            RcloneEntry("ABC-1", "无码破解", "中字", "/g/big", 99, 1, "t"),  # same key, bigger
            RcloneEntry("ABC-1", "有码", "无字", "/g/other", 5, 1, "t"),     # different key
        ]
    }


def test_gdrive_composite_category_and_max_size_collapse():
    obs = GdriveOwnershipCollector().collect(_gdrive_inventory())
    by_cat = {o.category: o for o in obs}
    assert set(by_cat) == {"无码破解|中字", "有码|无字"}
    assert by_cat["无码破解|中字"].path == "/g/big"   # max folder_size wins (D-P2-3)
    assert by_cat["无码破解|中字"].size == 99
    assert all(o.source == "gdrive" and o.video_code == "ABC-1" for o in obs)


def test_qb_owner_bridges_video_code_from_outcomes():
    outcomes = [
        {"video_code": "ABC-1", "category": "subtitle"},
        {"video_code": None, "category": "no_subtitle"},   # no code → skipped
    ]
    obs = QbOwnershipCollector().collect(outcomes)
    assert len(obs) == 1
    assert obs[0].source == "qb"
    assert obs[0].category == "subtitle"      # English namespace, unchanged


def test_pikpak_only_success_rows():
    rows = [
        {"TransferStatus": "success", "TorrentName": "ABC-1", "video_code": "ABC-1"},
        {"TransferStatus": "failed", "TorrentName": "DEF-2", "video_code": "DEF-2"},
    ]
    obs = PikpakOwnershipCollector().collect(rows)
    assert [o.video_code for o in obs] == ["ABC-1"]
    assert obs[0].source == "pikpak"
    assert obs[0].category == ""


def test_nas_is_an_explicit_empty_stub(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        obs = NasOwnershipCollector().collect()
    assert obs == []
    assert any("nas ownership not collected" in r.message for r in caplog.records)
