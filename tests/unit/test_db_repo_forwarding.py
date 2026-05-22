"""Legacy ``db.py`` facade forwarding tests for ADR-005 PR-2."""

from unittest.mock import patch

import pytest

from javdb.storage.db import db


def _fail_raw_call(*args, **kwargs):
    raise AssertionError("facade called raw db_* module")


def _raw_patch(path: str):
    return patch(path, side_effect=_fail_raw_call)


HISTORY_CASES = [
    (
        "db_load_history",
        "load_history",
        (),
        {"phase": 2, "db_path": "/tmp/history.db"},
        (),
        {"phase": 2},
        {"history": True},
        "javdb.storage.db.db_history_read.db_load_history",
    ),
    (
        "db_check_torrent_in_history",
        "check_torrent_in_history",
        ("/movies/abc", "subtitle"),
        {"db_path": "/tmp/history.db"},
        ("/movies/abc", "subtitle"),
        {},
        True,
        "javdb.storage.db.db_history_read.db_check_torrent_in_history",
    ),
    (
        "db_get_all_history_records",
        "get_all_history_records",
        (),
        {"db_path": "/tmp/history.db"},
        (),
        {},
        [{"Id": 1}],
        "javdb.storage.db.db_history_read.db_get_all_history_records",
    ),
    (
        "db_load_history_snapshot",
        "load_history_snapshot",
        ("sess-1",),
        {"db_path": "/tmp/history.db"},
        ("sess-1",),
        {},
        {"snapshot": True},
        "javdb.storage.db.db_history_read.db_load_history_snapshot",
    ),
]


@pytest.mark.parametrize(
    "facade_name,method_name,args,kwargs,method_args,method_kwargs,"
    "return_value,raw_path",
    HISTORY_CASES,
)
def test_history_facade_forwards_to_history_repo(
    facade_name,
    method_name,
    args,
    kwargs,
    method_args,
    method_kwargs,
    return_value,
    raw_path,
):
    with patch("javdb.storage.db.db.HistoryRepo", create=True) as repo_cls:
        repo = repo_cls.return_value
        getattr(repo, method_name).return_value = return_value
        with _raw_patch(raw_path):
            result = getattr(db, facade_name)(*args, **kwargs)

    assert result == return_value
    repo_cls.assert_called_once_with(db_path="/tmp/history.db")
    getattr(repo, method_name).assert_called_once_with(
        *method_args, **method_kwargs,
    )


def test_stage_history_write_facade_forwards_to_history_repo():
    payload = {"Href": "/movies/abc"}
    with patch("javdb.storage.db.db.HistoryRepo", create=True) as repo_cls:
        repo = repo_cls.return_value
        repo.stage_history_write.return_value = "SEQ-1"
        with patch("javdb.storage.db.db.get_db", side_effect=_fail_raw_call):
            result = db.db_stage_history_write(
                "sess-1", "movie", payload, db_path="/tmp/history.db",
            )

    assert result == "SEQ-1"
    repo_cls.assert_called_once_with(db_path="/tmp/history.db")
    repo.stage_history_write.assert_called_once_with(
        "sess-1", "movie", payload,
    )


STATS_CASES = [
    (
        "db_save_spider_stats",
        "save_spider_stats",
        ("sess-1", {"Total": 1}),
        11,
        "javdb.storage.db.db_stats.db_save_spider_stats",
    ),
    (
        "db_save_uploader_stats",
        "save_uploader_stats",
        ("sess-1", {"Uploaded": 1}),
        12,
        "javdb.storage.db.db_stats.db_save_uploader_stats",
    ),
    (
        "db_save_pikpak_stats",
        "save_pikpak_stats",
        ("sess-1", {"Transferred": 1}),
        13,
        "javdb.storage.db.db_stats.db_save_pikpak_stats",
    ),
    (
        "db_get_spider_stats",
        "get_spider_stats",
        ("sess-1",),
        {"Spider": True},
        "javdb.storage.db.db_stats.db_get_spider_stats",
    ),
    (
        "db_get_uploader_stats",
        "get_uploader_stats",
        ("sess-1",),
        {"Uploader": True},
        "javdb.storage.db.db_stats.db_get_uploader_stats",
    ),
    (
        "db_get_pikpak_stats",
        "get_pikpak_stats",
        ("sess-1",),
        {"Pikpak": True},
        "javdb.storage.db.db_stats.db_get_pikpak_stats",
    ),
    (
        "db_get_spider_stats_local",
        "get_spider_stats_local",
        ("sess-1",),
        {"SpiderLocal": True},
        "javdb.storage.db.db_stats.db_get_spider_stats_local",
    ),
    (
        "db_get_uploader_stats_local",
        "get_uploader_stats_local",
        ("sess-1",),
        {"UploaderLocal": True},
        "javdb.storage.db.db_stats.db_get_uploader_stats_local",
    ),
    (
        "db_get_pikpak_stats_local",
        "get_pikpak_stats_local",
        ("sess-1",),
        {"PikpakLocal": True},
        "javdb.storage.db.db_stats.db_get_pikpak_stats_local",
    ),
]


@pytest.mark.parametrize(
    "facade_name,method_name,args,return_value,raw_path",
    STATS_CASES,
)
def test_stats_facade_forwards_to_stats_repo(
    facade_name, method_name, args, return_value, raw_path,
):
    with patch("javdb.storage.db.db.StatsRepo", create=True) as repo_cls:
        repo = repo_cls.return_value
        getattr(repo, method_name).return_value = return_value
        with _raw_patch(raw_path):
            result = getattr(db, facade_name)(
                *args, db_path="/tmp/reports.db",
            )

    assert result == return_value
    repo_cls.assert_called_once_with(db_path="/tmp/reports.db")
    getattr(repo, method_name).assert_called_once_with(*args)


OPERATIONS_CASES = [
    (
        "db_load_rclone_inventory",
        "load_rclone_inventory",
        (),
        {},
        (),
        {},
        {"ABC-123": [{"FolderPath": "/a"}]},
        "javdb.storage.db.db_operations.db_load_rclone_inventory",
    ),
    (
        "db_open_rclone_staging",
        "open_rclone_staging",
        ("sess-1",),
        {},
        ("sess-1",),
        {},
        "RcloneInventoryStaging_sess_1",
        "javdb.storage.db.db_operations.db_open_rclone_staging",
    ),
    (
        "db_append_rclone_staging",
        "append_rclone_staging",
        ([{"VideoCode": "ABC-123"}],),
        {"session_id": "sess-1"},
        ([{"VideoCode": "ABC-123"}], "sess-1"),
        {},
        1,
        "javdb.storage.db.db_operations.db_append_rclone_staging",
    ),
    (
        "db_swap_rclone_inventory",
        "swap_rclone_inventory",
        ("sess-1",),
        {},
        ("sess-1",),
        {},
        2,
        "javdb.storage.db.db_operations.db_swap_rclone_inventory",
    ),
    (
        "db_merge_rclone_inventory_from_stage",
        "merge_rclone_inventory_from_stage",
        ("sess-1",),
        {"years": ["2025", "2026"]},
        ("sess-1", ["2025", "2026"]),
        {},
        3,
        "javdb.storage.db.db_operations.db_merge_rclone_inventory_from_stage",
    ),
    (
        "db_drop_rclone_staging",
        "drop_rclone_staging",
        ("sess-1",),
        {},
        ("sess-1",),
        {},
        None,
        "javdb.storage.db.db_operations.db_drop_rclone_staging",
    ),
    (
        "db_clear_rclone_inventory",
        "clear_rclone_inventory",
        (),
        {},
        (),
        {},
        None,
        "javdb.storage.db.db_operations.db_clear_rclone_inventory",
    ),
    (
        "db_delete_rclone_inventory_paths",
        "delete_rclone_inventory_paths",
        (["/a", "/b"],),
        {},
        (["/a", "/b"],),
        {},
        2,
        "javdb.storage.db.db_operations.db_delete_rclone_inventory_paths",
    ),
    (
        "db_load_dedup_records",
        "load_dedup_records",
        (),
        {},
        (),
        {},
        [{"Id": 1}],
        "javdb.storage.db.db_operations.db_load_dedup_records",
    ),
    (
        "db_save_dedup_records",
        "save_dedup_records",
        ([{"VideoCode": "ABC-123"}],),
        {},
        ([{"VideoCode": "ABC-123"}],),
        {},
        None,
        "javdb.storage.db.db_operations.db_save_dedup_records",
    ),
    (
        "db_append_dedup_record",
        "append_dedup_record",
        ({"VideoCode": "ABC-123"},),
        {"session_id": "sess-1"},
        ({"VideoCode": "ABC-123"},),
        {"session_id": "sess-1"},
        4,
        "javdb.storage.db.db_operations.db_append_dedup_record",
    ),
    (
        "db_mark_records_deleted",
        "mark_records_deleted",
        ([("/g/a", "2026-01-01")],),
        {"session_id": "sess-1"},
        ([("/g/a", "2026-01-01")],),
        {"session_id": "sess-1"},
        5,
        "javdb.storage.db.db_operations.db_mark_records_deleted",
    ),
    (
        "db_mark_orphan_records",
        "mark_orphan_records",
        (["/g/a"], "stale", "2026-01-01"),
        {"session_id": "sess-1"},
        (["/g/a"], "stale", "2026-01-01"),
        {"session_id": "sess-1"},
        6,
        "javdb.storage.db.db_operations.db_mark_orphan_records",
    ),
    (
        "db_cleanup_deleted_records",
        "cleanup_deleted_records",
        (),
        {"older_than_days": 14},
        (),
        {"older_than_days": 14},
        7,
        "javdb.storage.db.db_operations.db_cleanup_deleted_records",
    ),
    (
        "db_append_pikpak_history",
        "append_pikpak_history",
        ({"TorrentHash": "hash"},),
        {"session_id": "sess-1"},
        ({"TorrentHash": "hash"},),
        {"session_id": "sess-1"},
        8,
        "javdb.storage.db.db_operations.db_append_pikpak_history",
    ),
    (
        "db_upsert_align_no_exact_match",
        "upsert_align_no_exact_match",
        ("ABC-123",),
        {"reason": "manual", "session_id": "sess-1"},
        ("ABC-123",),
        {"reason": "manual", "session_id": "sess-1"},
        None,
        "javdb.storage.db.db_operations.db_upsert_align_no_exact_match",
    ),
    (
        "db_load_align_no_exact_match_codes",
        "load_align_no_exact_match_codes",
        (),
        {},
        (),
        {},
        {"ABC-123"},
        "javdb.storage.db.db_operations.db_load_align_no_exact_match_codes",
    ),
    (
        "db_delete_align_no_exact_match",
        "delete_align_no_exact_match",
        ("ABC-123",),
        {},
        ("ABC-123",),
        {},
        None,
        "javdb.storage.db.db_operations.db_delete_align_no_exact_match",
    ),
]


@pytest.mark.parametrize(
    "facade_name,method_name,args,kwargs,method_args,method_kwargs,"
    "return_value,raw_path",
    OPERATIONS_CASES,
)
def test_operations_facade_forwards_to_operations_repo(
    facade_name,
    method_name,
    args,
    kwargs,
    method_args,
    method_kwargs,
    return_value,
    raw_path,
):
    with patch("javdb.storage.db.db.OperationsRepo", create=True) as repo_cls:
        repo = repo_cls.return_value
        getattr(repo, method_name).return_value = return_value
        with _raw_patch(raw_path):
            result = getattr(db, facade_name)(
                *args, **kwargs, db_path="/tmp/operations.db",
            )

    assert result == return_value
    repo_cls.assert_called_once_with(db_path="/tmp/operations.db")
    getattr(repo, method_name).assert_called_once_with(
        *method_args, **method_kwargs,
    )
