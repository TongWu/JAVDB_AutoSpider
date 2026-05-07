"""Tests for the unified rclone_manager script."""

import os
import sys
import csv
import pytest
from unittest.mock import patch, MagicMock

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from scripts.rclone_manager import (
    parse_arguments,
    parse_root_path,
    resolve_rclone_root,
    load_inventory_as_folder_structure,
    run_report_from_inventory,
    run_execute_from_csv,
    migrate_strip_drive_names,
    INVENTORY_FIELDNAMES,
)
from utils.rclone_helper import (
    FolderInfo,
    rclone_purge,
    strip_drive_name,
    get_configured_drive_name,
    prepend_drive_name,
)
from scripts.spider.services.dedup import (
    DedupRecord,
    append_dedup_record,
    load_dedup_csv,
    save_dedup_csv,
    mark_records_deleted,
)


# ============================================================================
# Test CLI argument parsing
# ============================================================================

class TestParseArguments:
    def test_scan_flag(self):
        args = parse_arguments(['--scan'])
        assert args.scan is True
        assert args.report is False
        assert args.execute is False

    def test_report_flag(self):
        args = parse_arguments(['--report'])
        assert args.report is True
        assert args.scan is False
        assert args.execute is False

    def test_execute_flag(self):
        args = parse_arguments(['--execute'])
        assert args.execute is True
        assert args.scan is False
        assert args.report is False
        assert args.dry_run is False
        assert args.dedup_csv is None

    def test_scan_report(self):
        args = parse_arguments(['--scan', '--report'])
        assert args.scan is True
        assert args.report is True
        assert args.execute is False

    def test_report_execute(self):
        args = parse_arguments(['--report', '--execute', '--dry-run'])
        assert args.report is True
        assert args.execute is True
        assert args.scan is False
        assert args.dry_run is True

    def test_scan_report_execute(self):
        args = parse_arguments(['--scan', '--report', '--execute'])
        assert args.scan is True
        assert args.report is True
        assert args.execute is True

    def test_scan_execute_without_report_rejected(self):
        with pytest.raises(SystemExit):
            parse_arguments(['--scan', '--execute'])

    def test_no_flags_rejected(self):
        with pytest.raises(SystemExit):
            parse_arguments([])

    def test_years_filter(self):
        args = parse_arguments(['--scan', '--years', '2025,2026'])
        assert args.years == '2025,2026'

    def test_workers_default(self):
        args = parse_arguments(['--scan'])
        assert args.workers == 4

    def test_workers_custom(self):
        args = parse_arguments(['--scan', '--workers', '8'])
        assert args.workers == 8

    def test_log_level(self):
        args = parse_arguments(['--scan', '--log-level', 'DEBUG'])
        assert args.log_level == 'DEBUG'

    def test_root_path(self):
        args = parse_arguments(['--scan', '--root-path', 'gdrive:/some/path'])
        assert args.root_path == 'gdrive:/some/path'

    def test_incremental(self):
        args = parse_arguments(['--report', '--incremental'])
        assert args.incremental is True

    def test_execute_with_csv(self):
        args = parse_arguments(['--execute', '--dedup-csv', '/tmp/d.csv'])
        assert args.dedup_csv == '/tmp/d.csv'

    def test_execute_dry_run(self):
        args = parse_arguments(['--execute', '--dry-run'])
        assert args.dry_run is True

    def test_flag_order_does_not_matter(self):
        args = parse_arguments(['--execute', '--report', '--scan'])
        assert args.scan is True
        assert args.report is True
        assert args.execute is True


def test_scan_inventory_counts_process_year_none_as_error(monkeypatch):
    import scripts.rclone_manager as rm

    callbacks = []
    monkeypatch.setattr(rm, "get_year_folders", lambda *_args: ["2026"])
    monkeypatch.setattr(rm, "_process_year", lambda *_args, **_kwargs: None)

    total, errors = rm.scan_inventory(
        "gdrive", "root", row_callback=lambda rows: callbacks.append(rows)
    )

    assert total == 0
    assert errors == 1
    assert callbacks == []


def test_scan_csv_temp_removed_on_scan_failure(monkeypatch, tmp_path):
    import scripts.rclone_manager as rm
    from packages.python.javdb_platform import config_helper

    output = tmp_path / "inventory.csv"
    row = {field: "" for field in INVENTORY_FIELDNAMES}
    row.update({
        "video_code": "ABC-001",
        "folder_path": "2026/a/ABC-001",
        "folder_size": 1,
        "file_count": 1,
    })

    def fake_scan(*_args, row_callback=None, **_kwargs):
        row_callback([row])
        return 1, 1

    monkeypatch.setattr(rm, "RCLONE_CONFIG_BASE64", "")
    monkeypatch.setattr(rm, "check_rclone_installed", lambda: (True, "ok"))
    monkeypatch.setattr(rm, "check_remote_exists", lambda _remote: (True, "ok"))
    monkeypatch.setattr(rm, "scan_inventory", fake_scan)
    monkeypatch.setattr(config_helper, "use_sqlite", lambda: False)
    monkeypatch.setattr(config_helper, "use_csv", lambda: True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rclone_manager",
            "--scan",
            "--root-path",
            "gdrive:/root",
            "--output",
            str(output),
        ],
    )

    assert rm.main() == 1
    assert not output.exists()
    assert list(tmp_path.glob("inventory.csv.*.tmp")) == []


def test_scan_sqlite_uses_staging_when_no_active_session(
    monkeypatch, tmp_path, storage_mode_db
):
    import scripts.rclone_manager as rm
    from utils.infra import db as db_mod

    output = tmp_path / "inventory.csv"
    seed = {
        "VideoCode": "OLD-001",
        "FolderPath": "2026/old/OLD-001",
        "FolderSize": 1,
        "FileCount": 1,
        "DateTimeScanned": "2026-05-04 00:00:00",
    }
    incoming = {field: "" for field in INVENTORY_FIELDNAMES}
    incoming.update({
        "video_code": "NEW-001",
        "folder_path": "2026/new/NEW-001",
        "folder_size": 1,
        "file_count": 1,
        "scan_datetime": "2026-05-05 00:00:00",
    })

    db_mod.set_active_session_id(None)
    db_mod.db_replace_rclone_inventory([seed])

    def fake_scan(*_args, row_callback=None, **_kwargs):
        row_callback([incoming])
        return 1, 1

    monkeypatch.setattr(rm, "RCLONE_CONFIG_BASE64", "")
    monkeypatch.setattr(rm, "check_rclone_installed", lambda: (True, "ok"))
    monkeypatch.setattr(rm, "check_remote_exists", lambda _remote: (True, "ok"))
    monkeypatch.setattr(rm, "scan_inventory", fake_scan)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rclone_manager",
            "--scan",
            "--root-path",
            "gdrive:/root",
            "--output",
            str(output),
        ],
    )

    assert rm.main() == 1
    with db_mod.get_db() as conn:
        rows = conn.execute(
            "SELECT VideoCode FROM RcloneInventory ORDER BY VideoCode"
        ).fetchall()
        staging_tables = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE name LIKE 'RcloneInventoryStaging_%'"
        ).fetchall()
    assert [row["VideoCode"] for row in rows] == ["OLD-001"]
    assert staging_tables == []


def test_scan_marks_local_session_committed_after_inventory_swap(
    monkeypatch, tmp_path, storage_mode_db
):
    import scripts.rclone_manager as rm
    from utils.infra import db as db_mod

    output = tmp_path / "inventory.csv"
    incoming = {field: "" for field in INVENTORY_FIELDNAMES}
    incoming.update({
        "video_code": "NEW-001",
        "folder_path": "2026/new/NEW-001",
        "folder_size": 1,
        "file_count": 1,
        "scan_datetime": "2026-05-05 00:00:00",
    })
    order = []

    def fake_scan(*_args, row_callback=None, **_kwargs):
        row_callback([incoming])
        return 1, 0

    monkeypatch.setattr(rm, "RCLONE_CONFIG_BASE64", "")
    monkeypatch.setattr(rm, "check_rclone_installed", lambda: (True, "ok"))
    monkeypatch.setattr(rm, "check_remote_exists", lambda _remote: (True, "ok"))
    monkeypatch.setattr(rm, "scan_inventory", fake_scan)
    monkeypatch.setattr(rm, "export_db_to_csv", lambda _path: order.append("export"))
    monkeypatch.setattr(db_mod, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(db_mod, "get_active_session_id", lambda: None)
    monkeypatch.setattr(
        db_mod,
        "db_create_report_session",
        lambda **_kwargs: order.append("create_session") or 123,
    )
    monkeypatch.setattr(
        db_mod,
        "db_open_rclone_staging",
        lambda sid: order.append(("open_staging", sid)),
    )
    monkeypatch.setattr(
        db_mod,
        "db_append_rclone_staging",
        lambda rows, session_id: order.append(("append_staging", session_id)),
    )
    monkeypatch.setattr(
        db_mod,
        "db_mark_session_committed",
        lambda sid: order.append(("mark_committed", sid)) or 1,
    )
    monkeypatch.setattr(
        db_mod,
        "db_swap_rclone_inventory",
        lambda session_id: order.append(("swap_inventory", session_id)) or 1,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rclone_manager",
            "--scan",
            "--root-path",
            "gdrive:/root",
            "--output",
            str(output),
        ],
    )

    assert rm.main() == 0
    assert order.index(("swap_inventory", 123)) < order.index(("mark_committed", 123))


def test_scan_does_not_mark_local_session_committed_when_swap_fails(
    monkeypatch, tmp_path, storage_mode_db
):
    import scripts.rclone_manager as rm
    from utils.infra import db as db_mod

    output = tmp_path / "inventory.csv"
    incoming = {field: "" for field in INVENTORY_FIELDNAMES}
    incoming.update({
        "video_code": "NEW-001",
        "folder_path": "2026/new/NEW-001",
        "folder_size": 1,
        "file_count": 1,
        "scan_datetime": "2026-05-05 00:00:00",
    })
    order = []

    def fake_scan(*_args, row_callback=None, **_kwargs):
        row_callback([incoming])
        return 1, 0

    def fail_swap(session_id):
        order.append(("swap_inventory", session_id))
        raise RuntimeError("swap failed")

    monkeypatch.setattr(rm, "RCLONE_CONFIG_BASE64", "")
    monkeypatch.setattr(rm, "check_rclone_installed", lambda: (True, "ok"))
    monkeypatch.setattr(rm, "check_remote_exists", lambda _remote: (True, "ok"))
    monkeypatch.setattr(rm, "scan_inventory", fake_scan)
    monkeypatch.setattr(db_mod, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(db_mod, "get_active_session_id", lambda: None)
    monkeypatch.setattr(
        db_mod,
        "db_create_report_session",
        lambda **_kwargs: order.append("create_session") or 123,
    )
    monkeypatch.setattr(
        db_mod,
        "db_open_rclone_staging",
        lambda sid: order.append(("open_staging", sid)),
    )
    monkeypatch.setattr(
        db_mod,
        "db_append_rclone_staging",
        lambda rows, session_id: order.append(("append_staging", session_id)),
    )
    monkeypatch.setattr(
        db_mod,
        "db_mark_session_committed",
        lambda sid: order.append(("mark_committed", sid)) or 1,
    )
    monkeypatch.setattr(db_mod, "db_swap_rclone_inventory", fail_swap)
    monkeypatch.setattr(
        db_mod,
        "db_drop_rclone_staging",
        lambda sid: order.append(("drop_staging", sid)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rclone_manager",
            "--scan",
            "--root-path",
            "gdrive:/root",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(RuntimeError, match="swap failed"):
        rm.main()
    assert ("mark_committed", 123) not in order
    assert ("drop_staging", 123) in order


def test_scan_succeeds_when_post_swap_commit_marking_fails(
    monkeypatch, tmp_path, storage_mode_db
):
    import scripts.rclone_manager as rm
    from utils.infra import db as db_mod

    output = tmp_path / "inventory.csv"
    incoming = {field: "" for field in INVENTORY_FIELDNAMES}
    incoming.update({
        "video_code": "NEW-001",
        "folder_path": "2026/new/NEW-001",
        "folder_size": 1,
        "file_count": 1,
        "scan_datetime": "2026-05-05 00:00:00",
    })
    order = []

    def fake_scan(*_args, row_callback=None, **_kwargs):
        row_callback([incoming])
        return 1, 0

    def fail_mark(sid):
        order.append(("mark_committed", sid))
        raise RuntimeError("mark failed")

    monkeypatch.setattr(rm, "RCLONE_CONFIG_BASE64", "")
    monkeypatch.setattr(rm, "check_rclone_installed", lambda: (True, "ok"))
    monkeypatch.setattr(rm, "check_remote_exists", lambda _remote: (True, "ok"))
    monkeypatch.setattr(rm, "scan_inventory", fake_scan)
    monkeypatch.setattr(rm, "export_db_to_csv", lambda _path: order.append("export"))
    monkeypatch.setattr(db_mod, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(db_mod, "get_active_session_id", lambda: None)
    monkeypatch.setattr(
        db_mod,
        "db_create_report_session",
        lambda **_kwargs: order.append("create_session") or 123,
    )
    monkeypatch.setattr(
        db_mod,
        "db_open_rclone_staging",
        lambda sid: order.append(("open_staging", sid)),
    )
    monkeypatch.setattr(
        db_mod,
        "db_append_rclone_staging",
        lambda rows, session_id: order.append(("append_staging", session_id)),
    )
    monkeypatch.setattr(db_mod, "db_mark_session_committed", fail_mark)
    monkeypatch.setattr(
        db_mod,
        "db_swap_rclone_inventory",
        lambda session_id: order.append(("swap_inventory", session_id)) or 1,
    )
    monkeypatch.setattr(
        db_mod,
        "db_drop_rclone_staging",
        lambda sid: order.append(("drop_staging", sid)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rclone_manager",
            "--scan",
            "--root-path",
            "gdrive:/root",
            "--output",
            str(output),
        ],
    )

    assert rm.main() == 0
    assert order.count(("mark_committed", 123)) == 3
    assert ("drop_staging", 123) not in order


def test_scan_aborts_when_sqlite_staging_init_fails(
    monkeypatch, tmp_path, storage_mode_duo
):
    import scripts.rclone_manager as rm
    from utils.infra import db as db_mod

    output = tmp_path / "inventory.csv"
    order = []

    def fake_scan(*_args, **_kwargs):
        order.append("scan")
        return 1, 0

    monkeypatch.setattr(rm, "RCLONE_CONFIG_BASE64", "")
    monkeypatch.setattr(rm, "check_rclone_installed", lambda: (True, "ok"))
    monkeypatch.setattr(rm, "check_remote_exists", lambda _remote: (True, "ok"))
    monkeypatch.setattr(rm, "scan_inventory", fake_scan)
    monkeypatch.setattr(db_mod, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(db_mod, "get_active_session_id", lambda: None)
    monkeypatch.setattr(
        db_mod,
        "db_create_report_session",
        lambda **_kwargs: order.append("create_session") or 123,
    )
    monkeypatch.setattr(
        db_mod,
        "db_open_rclone_staging",
        lambda sid: (_ for _ in ()).throw(RuntimeError("staging failed")),
    )
    monkeypatch.setattr(
        db_mod,
        "db_drop_rclone_staging",
        lambda sid: order.append(("drop_staging", sid)),
    )
    monkeypatch.setattr(
        db_mod,
        "db_mark_session_failed",
        lambda sid: order.append(("mark_failed", sid)) or 1,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rclone_manager",
            "--scan",
            "--report",
            "--root-path",
            "gdrive:/root",
            "--output",
            str(output),
        ],
    )

    assert rm.main() == 1
    assert "scan" not in order
    assert ("drop_staging", 123) in order
    assert ("mark_failed", 123) in order
    assert not output.exists()


# ============================================================================
# Test parse_root_path
# ============================================================================

class TestParseRootPath:
    def test_normal(self):
        remote, folder = parse_root_path('gdrive:/movies')
        assert remote == 'gdrive'
        assert folder == 'movies'

    def test_missing_colon(self):
        with pytest.raises(ValueError):
            parse_root_path('no-colon')


# ============================================================================
# Test resolve_rclone_root
# ============================================================================


class TestResolveRcloneRoot:
    def test_cli_root_path(self):
        assert resolve_rclone_root('remote:/a/b') == ('remote', 'a/b')

    def test_cli_empty_falls_through_to_config(self):
        import scripts.rclone_manager as rm

        with patch.object(rm, 'RCLONE_FOLDER_PATH', 'gdrive:/x'):
            assert resolve_rclone_root('  ') == ('gdrive', 'x')

    def test_from_rclone_folder_path(self):
        import scripts.rclone_manager as rm

        with patch.object(rm, 'RCLONE_FOLDER_PATH', 'gdrive:/shows/jav'):
            assert resolve_rclone_root(None) == ('gdrive', 'shows/jav')

    def test_legacy_drive_and_root(self):
        import scripts.rclone_manager as rm

        with patch.object(rm, 'RCLONE_FOLDER_PATH', None):

            def fake_cfg(name, default):
                if name == 'RCLONE_DRIVE_NAME':
                    return 'gdrive'
                if name == 'RCLONE_ROOT_FOLDER':
                    return '/legacy/path'
                return default

            with patch.object(rm, 'cfg', side_effect=fake_cfg):
                assert resolve_rclone_root(None) == ('gdrive', 'legacy/path')


# ============================================================================
# Test load_inventory_as_folder_structure
# ============================================================================

class TestLoadInventoryAsFolderStructure:
    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    def test_loads_from_csv(self, _mock_dn, tmp_path, storage_mode_csv):
        csv_path = str(tmp_path / 'inventory.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
            writer.writeheader()
            writer.writerow({
                'video_code': 'ABC-123',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': 'root/2025/Actor/ABC-123 [有码-中字]',
                'folder_size': '1000',
                'file_count': '3',
                'scan_datetime': '2026-01-01 00:00:00',
            })
            writer.writerow({
                'video_code': 'DEF-456',
                'sensor_category': '无码',
                'subtitle_category': '无字',
                'folder_path': 'root/2025/ActorB/DEF-456 [无码-无字]',
                'folder_size': '2000',
                'file_count': '5',
                'scan_datetime': '2026-01-01 00:00:00',
            })

        structure = load_inventory_as_folder_structure(csv_path)
        assert len(structure) > 0

        all_folders = []
        for actors in structure.values():
            for folders in actors.values():
                all_folders.extend(folders)
        assert len(all_folders) == 2

        codes = {f.movie_code for f in all_folders}
        assert 'ABC-123' in codes
        assert 'DEF-456' in codes
        assert all_folders[0].full_path.startswith('gdrive:')

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    @patch('scripts.rclone_manager.get_configured_root_folder', return_value='root')
    def test_loads_from_db(self, _mock_root, _mock_dn, storage_mode_db):
        from utils.infra.db import db_replace_rclone_inventory
        db_replace_rclone_inventory([
            {
                'video_code': 'DB-001',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': 'root/2025/Actor/DB-001 [有码-中字]',
                'folder_size': 500,
                'file_count': 2,
                'scan_datetime': '2026-01-01 00:00:00',
            },
        ])

        structure = load_inventory_as_folder_structure('/nonexistent.csv')
        all_folders = []
        for actors in structure.values():
            for folders in actors.values():
                all_folders.extend(folders)
        assert len(all_folders) == 1
        assert all_folders[0].movie_code == 'DB-001'
        assert all_folders[0].full_path == 'gdrive:root/2025/Actor/DB-001 [有码-中字]'

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    def test_db_priority_over_csv(self, _mock_dn, tmp_path, storage_mode_db):
        """When DB has data, CSV should not be loaded even if it exists."""
        from utils.infra.db import db_replace_rclone_inventory
        db_replace_rclone_inventory([
            {
                'video_code': 'DB-ONLY',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': 'root/2025/A/DB-ONLY [有码-中字]',
                'folder_size': 100,
                'file_count': 1,
                'scan_datetime': '2026-01-01 00:00:00',
            },
        ])

        csv_path = str(tmp_path / 'inventory.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
            writer.writeheader()
            writer.writerow({
                'video_code': 'CSV-ONLY',
                'sensor_category': '無碼',
                'subtitle_category': '中字',
                'folder_path': 'root/2025/B/CSV-ONLY [無碼-中字]',
                'folder_size': '200',
                'file_count': '2',
                'scan_datetime': '2026-01-01 00:00:00',
            })

        structure = load_inventory_as_folder_structure(csv_path)
        all_folders = []
        for actors in structure.values():
            for folders in actors.values():
                all_folders.extend(folders)
        codes = {f.movie_code for f in all_folders}
        assert 'DB-ONLY' in codes
        assert 'CSV-ONLY' not in codes

    def test_empty_returns_empty(self, tmp_path, storage_mode_csv):
        csv_path = str(tmp_path / 'empty.csv')
        structure = load_inventory_as_folder_structure(csv_path)
        assert structure == {}

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    @patch('scripts.rclone_manager.get_configured_root_folder', return_value='root')
    def test_loads_new_layout_with_code_dir(self, _mock_root, _mock_dn, tmp_path, storage_mode_csv):
        """New layout: <root>/<year>/<actor>/<movie_code>/<sensor-subtitle>."""
        csv_path = str(tmp_path / 'inventory.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
            writer.writeheader()
            writer.writerow({
                'video_code': 'ABC-123',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': '2025/Actor/ABC-123/有码-中字',
                'folder_size': '1000',
                'file_count': '3',
                'scan_datetime': '2026-01-01 00:00:00',
            })
            writer.writerow({
                'video_code': 'DEF-456',
                'sensor_category': '无码流出',
                'subtitle_category': '无字',
                'folder_path': '2025/ActorB/DEF-456/无码流出-无字',
                'folder_size': '2000',
                'file_count': '5',
                'scan_datetime': '2026-01-01 00:00:00',
            })

        structure = load_inventory_as_folder_structure(csv_path)
        all_folders = [
            f for actors in structure.values()
            for folders in actors.values() for f in folders
        ]
        by_code = {f.movie_code: f for f in all_folders}
        assert set(by_code) == {'ABC-123', 'DEF-456'}
        assert by_code['ABC-123'].year == '2025'
        assert by_code['ABC-123'].actor == 'Actor'
        assert by_code['ABC-123'].folder_name == '有码-中字'
        assert by_code['ABC-123'].full_path == 'gdrive:root/2025/Actor/ABC-123/有码-中字'
        assert by_code['DEF-456'].actor == 'ActorB'
        assert by_code['DEF-456'].folder_name == '无码流出-无字'

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    @patch('scripts.rclone_manager.get_configured_root_folder', return_value='root')
    def test_folder_info_fields(self, _mock_root, _mock_dn, tmp_path, storage_mode_csv):
        csv_path = str(tmp_path / 'inventory.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
            writer.writeheader()
            writer.writerow({
                'video_code': 'XYZ-789',
                'sensor_category': '无码流出',
                'subtitle_category': '无字',
                'folder_path': 'root/2024/SomeActor/XYZ-789 [无码流出-无字]',
                'folder_size': '5000',
                'file_count': '10',
                'scan_datetime': '2026-03-01 12:00:00',
            })

        structure = load_inventory_as_folder_structure(csv_path)
        all_folders = []
        for actors in structure.values():
            for folders in actors.values():
                all_folders.extend(folders)
        assert len(all_folders) == 1
        fi = all_folders[0]
        assert fi.movie_code == 'XYZ-789'
        assert fi.sensor_category == '无码流出'
        assert fi.subtitle_category == '无字'
        assert fi.size == 5000
        assert fi.file_count == 10
        assert fi.full_path == 'gdrive:root/2024/SomeActor/XYZ-789 [无码流出-无字]'

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    @patch('scripts.rclone_manager.get_configured_root_folder', return_value='root')
    def test_skips_paths_without_4digit_year_segment(
        self, _mock_root, _mock_dn, tmp_path, storage_mode_csv, caplog,
    ):
        """A folder whose suffix-by-position would land on a non-year segment
        (e.g. extra prefix slashes) must be skipped with a WARNING instead of
        being silently misclassified with ``year='Actor'``.
        """
        csv_path = str(tmp_path / 'inventory.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
            writer.writeheader()
            writer.writerow({
                'video_code': 'OK-001',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': '2025/Actor/OK-001/有码-中字',
                'folder_size': '1', 'file_count': '1',
                'scan_datetime': '2026-01-01 00:00:00',
            })
            writer.writerow({
                # 5 segments — parts[-4]='Actor' is not a 4-digit year, so
                # this should be skipped, not stored under year='Actor'.
                'video_code': 'BAD-001',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': 'unexpected/Actor/BAD-001/有码-中字',
                'folder_size': '1', 'file_count': '1',
                'scan_datetime': '2026-01-01 00:00:00',
            })

        caplog.set_level('WARNING')
        structure = load_inventory_as_folder_structure(csv_path)
        all_folders = [
            f for actors in structure.values()
            for folders in actors.values() for f in folders
        ]
        codes = {f.movie_code for f in all_folders}
        assert codes == {'OK-001'}
        assert any(
            'missing 4-digit year' in r.getMessage() for r in caplog.records
        )


# ============================================================================
# Test dedup CSV filtering (is_deleted skip logic) — migrated from executor
# ============================================================================

class TestDedupCsvFiltering:
    def _create_dedup_csv(self, tmp_path, records):
        path = str(tmp_path / 'dedup.csv')
        for rec in records:
            append_dedup_record(path, rec)
        return path

    def test_filters_already_deleted(self, tmp_path):
        path = self._create_dedup_csv(tmp_path, [
            DedupRecord('A-001', 's', 'sub', 'p1', 100, 'cat', 'r', 't', 'True', '2026-01-01'),
            DedupRecord('B-002', 's', 'sub', 'p2', 200, 'cat', 'r', 't', 'False', ''),
        ])
        rows = load_dedup_csv(path)
        pending = [r for r in rows if r.get('is_deleted', 'False') != 'True']
        assert len(pending) == 1
        assert pending[0].get('VideoCode', pending[0].get('video_code')) == 'B-002'

    def test_all_deleted(self, tmp_path):
        path = self._create_dedup_csv(tmp_path, [
            DedupRecord('A-001', 's', 'sub', 'p1', 100, 'cat', 'r', 't', 'True', '2026-01-01'),
        ])
        rows = load_dedup_csv(path)
        pending = [r for r in rows if r.get('is_deleted', 'False') != 'True']
        assert len(pending) == 0


# ============================================================================
# Test is_deleted column update
# ============================================================================

class TestIsDeletedUpdate:
    def test_mark_records_deleted_preserves_structure(self, tmp_path):
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 'sensor', 'sub', 'path', 100, 'cat', 'reason', 'time', 'False', '')
        append_dedup_record(path, r)

        updated = mark_records_deleted(path, [('path', '2026-03-09 12:00:00')])
        assert updated == 1

        reloaded = load_dedup_csv(path)
        assert reloaded[0]['is_deleted'] == 'True'
        assert reloaded[0].get('DateTimeDeleted', reloaded[0].get('delete_datetime')) == '2026-03-09 12:00:00'
        assert reloaded[0].get('VideoCode', reloaded[0].get('video_code')) == 'A-001'
        assert reloaded[0].get('ExistingSensor', reloaded[0].get('existing_sensor')) == 'sensor'


# ============================================================================
# Test execute mode (dry-run)
# ============================================================================

class TestExecuteMode:
    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    @patch('utils.rclone_helper.subprocess.run')
    def test_dry_run_does_not_update_csv(self, mock_run, _mock_dn, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 's', 'sub', '/p', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record(path, r)

        result = run_execute_from_csv(path, dry_run=True)
        assert result == 0

        reloaded = load_dedup_csv(path)
        assert reloaded[0]['is_deleted'] == 'False'

    def test_run_execute_no_csv(self, tmp_path):
        path = str(tmp_path / 'nonexistent.csv')
        result = run_execute_from_csv(path)
        assert result == 0

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='gdrive')
    @patch('scripts.rclone_manager.export_dedup_history')
    @patch('utils.rclone_helper.subprocess.run')
    def test_run_execute_live(self, mock_run, mock_export, _mock_dn, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 's', 'sub', '/test/path', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record(path, r)

        result = run_execute_from_csv(path, dry_run=False)
        assert result == 0

        reloaded = load_dedup_csv(path)
        assert reloaded[0]['is_deleted'] == 'True'
        assert reloaded[0].get('DateTimeDeleted', reloaded[0].get('delete_datetime', '')) != ''

    def test_run_execute_all_already_deleted(self, tmp_path):
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 's', 'sub', 'p', 100, 'cat', 'r', 't', 'True', '2026-01-01')
        append_dedup_record(path, r)

        result = run_execute_from_csv(path)
        assert result == 0

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='')
    @patch('utils.rclone_helper.subprocess.run')
    def test_run_execute_refuses_when_no_drive_name(self, mock_run, _mock_dn, tmp_path):
        """Without a remote prefix and without a configured drive name, the
        executor must refuse rather than letting rclone treat the relative
        path as a LOCAL filesystem path (which would either error out or,
        worse, silently delete a real local directory)."""
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord(
            'A-001', 's', 'sub',
            '剧集/不可以色色/JAV-Sync/2012/Actor/A-001 [有码-无字]',
            100, 'cat', 'r', 't', 'False', '',
        )
        append_dedup_record(path, r)

        with pytest.raises(RuntimeError, match='drive name is not configured'):
            run_execute_from_csv(path, dry_run=False)

        mock_run.assert_not_called()
        reloaded = load_dedup_csv(path)
        assert reloaded[0]['is_deleted'] == 'False'

    @patch('scripts.rclone_manager.get_configured_drive_name', return_value='')
    @patch('scripts.rclone_manager.export_dedup_history')
    @patch('utils.rclone_helper.subprocess.run')
    def test_run_execute_allows_explicit_remote_prefix_without_drive_name(
        self, mock_run, _mock_export, _mock_dn, tmp_path,
    ):
        """When every pending row already carries an explicit ``remote:`` prefix
        the guard must NOT trigger, since rclone will route correctly."""
        mock_run.return_value = MagicMock(returncode=0)
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord(
            'A-001', 's', 'sub',
            'gdrive:剧集/不可以色色/JAV-Sync/2012/Actor/A-001 [有码-无字]',
            100, 'cat', 'r', 't', 'False', '',
        )
        append_dedup_record(path, r)

        result = run_execute_from_csv(path, dry_run=False)
        assert result == 0


# ============================================================================
# Test drive-name utility functions
# ============================================================================

class TestStripDriveName:
    def test_strips_gdrive(self):
        assert strip_drive_name('gdrive:path/to/folder') == 'path/to/folder'

    def test_strips_paula(self):
        assert strip_drive_name('paula:剧集/JAV') == '剧集/JAV'

    def test_no_colon_unchanged(self):
        assert strip_drive_name('path/to/folder') == 'path/to/folder'

    def test_empty_string(self):
        assert strip_drive_name('') == ''

    def test_colon_only(self):
        assert strip_drive_name('gdrive:') == ''

    def test_multiple_colons(self):
        assert strip_drive_name('a:b:c') == 'b:c'

    def test_colon_after_slash_unchanged(self):
        assert strip_drive_name('root/2025/folder:with:colons') == 'root/2025/folder:with:colons'


class TestPrependDriveName:
    def test_prepends_given_drive(self):
        assert prepend_drive_name('path/to/folder', 'gdrive') == 'gdrive:path/to/folder'

    def test_already_has_drive(self):
        assert prepend_drive_name('gdrive:path/to/folder', 'other') == 'gdrive:path/to/folder'

    def test_colon_in_segment_gets_prepended(self):
        assert prepend_drive_name('root/folder:name', 'gdrive') == 'gdrive:root/folder:name'

    def test_no_drive_name_given(self):
        with patch('utils.rclone_helper.get_configured_drive_name', return_value='auto'):
            assert prepend_drive_name('path') == 'auto:path'

    def test_no_drive_configured(self):
        with patch('utils.rclone_helper.get_configured_drive_name', return_value=''):
            assert prepend_drive_name('path') == 'path'

    def test_empty_path(self):
        assert prepend_drive_name('', 'gdrive') == 'gdrive:'


class TestGetConfiguredDriveName:
    def test_from_rclone_folder_path(self):
        with patch('packages.python.javdb_platform.config_helper.cfg') as mock_cfg:
            mock_cfg.side_effect = lambda name, default: 'gdrive:/path' if name == 'RCLONE_FOLDER_PATH' else default
            assert get_configured_drive_name() == 'gdrive'

    def test_from_rclone_drive_name(self):
        with patch('packages.python.javdb_platform.config_helper.cfg') as mock_cfg:
            def fake_cfg(name, default):
                if name == 'RCLONE_FOLDER_PATH':
                    return None
                if name == 'RCLONE_DRIVE_NAME':
                    return 'paula'
                return default
            mock_cfg.side_effect = fake_cfg
            assert get_configured_drive_name() == 'paula'

    def test_returns_empty_when_not_configured(self):
        with patch('packages.python.javdb_platform.config_helper.cfg') as mock_cfg:
            mock_cfg.return_value = None
            assert get_configured_drive_name() == ''


# ============================================================================
# Test DB migration
# ============================================================================

class TestMigrateStripDriveNames:
    def test_strips_drive_names_in_db(self):
        from utils.infra.db import db_replace_rclone_inventory, get_db, OPERATIONS_DB_PATH
        db_replace_rclone_inventory([
            {
                'video_code': 'MIG-001',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': 'gdrive:root/2025/Actor/MIG-001 [有码-中字]',
                'folder_size': 500,
                'file_count': 2,
                'scan_datetime': '2026-01-01 00:00:00',
            },
        ])

        updated = migrate_strip_drive_names()
        assert updated >= 1

        with get_db(OPERATIONS_DB_PATH) as conn:
            row = conn.execute("SELECT FolderPath FROM RcloneInventory WHERE VideoCode='MIG-001'").fetchone()
        assert row is not None
        assert ':' not in row[0]
        assert row[0] == 'root/2025/Actor/MIG-001 [有码-中字]'

    def test_idempotent(self):
        from utils.infra.db import db_replace_rclone_inventory, get_db, OPERATIONS_DB_PATH
        db_replace_rclone_inventory([
            {
                'video_code': 'MIG-002',
                'sensor_category': '无码',
                'subtitle_category': '无字',
                'folder_path': 'root/2025/Actor/MIG-002 [无码-无字]',
                'folder_size': 300,
                'file_count': 1,
                'scan_datetime': '2026-01-01 00:00:00',
            },
            {
                'video_code': 'MIG-003',
                'sensor_category': '有码',
                'subtitle_category': '中字',
                'folder_path': 'gdrive:root/2025/Actor/MIG-003 [有码-中字]',
                'folder_size': 400,
                'file_count': 2,
                'scan_datetime': '2026-01-01 00:00:00',
            },
        ])

        first = migrate_strip_drive_names()
        assert first >= 1

        second = migrate_strip_drive_names()
        assert second == 0

        with get_db(OPERATIONS_DB_PATH) as conn:
            row = conn.execute(
                "SELECT FolderPath FROM RcloneInventory WHERE VideoCode='MIG-003'",
            ).fetchone()
        assert row is not None
        assert row[0] == 'root/2025/Actor/MIG-003 [有码-中字]'
        assert ':' not in row[0]


# ============================================================================
# Path validation & self-healing
# ============================================================================

from scripts.rclone_manager import (
    validate_dedup_records_against_inventory,
    run_validate_inventory,
    ORPHAN_REASON_SUFFIX,
)


def _add_inventory(rows):
    from utils.infra.db import db_replace_rclone_inventory
    entries = []
    for code, path in rows:
        entries.append({
            'video_code': code, 'sensor_category': '有码',
            'subtitle_category': '中字', 'folder_path': path,
            'folder_size': 1, 'file_count': 1,
            'scan_datetime': '2026-01-01 00:00:00',
        })
    db_replace_rclone_inventory(entries)


def _add_dedup_pending(code, path, reason='Subtitle upgrade'):
    from utils.infra.db import db_append_dedup_record
    db_append_dedup_record({
        'video_code': code, 'existing_sensor': '有码',
        'existing_subtitle': '中字', 'existing_gdrive_path': path,
        'existing_folder_size': 100, 'new_torrent_category': '有码',
        'deletion_reason': reason, 'detect_datetime': '2026-01-01 00:00:00',
        'is_deleted': 0, 'delete_datetime': None,
    })


class TestValidateDedupRecords:
    def test_marks_only_orphan_pendings(self, storage_mode_db, tmp_path, monkeypatch):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))

        _add_inventory([
            ('A', '2025/Actor/A/有码-中字'),
            ('B', '2025/Actor/B/有码-中字'),
        ])
        _add_dedup_pending('A', '2025/Actor/A/有码-中字')
        _add_dedup_pending('B', '2025/Actor/B/有码-中字')
        _add_dedup_pending('C', '2025/Actor/C/有码-中字')

        count, orphans = validate_dedup_records_against_inventory()
        assert count == 1
        assert len(orphans) == 1
        assert orphans[0]['VideoCode'] == 'C'

        from utils.infra.db import db_load_dedup_records
        rows = db_load_dedup_records()
        deleted = [r for r in rows if int(r.get('IsDeleted') or 0) == 1]
        pending = [r for r in rows if int(r.get('IsDeleted') or 0) == 0]
        assert {r['VideoCode'] for r in deleted} == {'C'}
        assert {r['VideoCode'] for r in pending} == {'A', 'B'}
        c_row = deleted[0]
        assert ORPHAN_REASON_SUFFIX in c_row['DeletionReason']
        assert c_row['DateTimeDeleted']

    def test_no_orphans_returns_zero(self, storage_mode_db, tmp_path, monkeypatch):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))
        _add_inventory([('A', '2025/Actor/A/有码-中字')])
        _add_dedup_pending('A', '2025/Actor/A/有码-中字')
        count, orphans = validate_dedup_records_against_inventory()
        assert count == 0 and orphans == []

    def test_empty_inventory_skips(self, storage_mode_db, tmp_path, monkeypatch):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))
        _add_dedup_pending('X', '2025/Actor/X/有码-中字')
        count, orphans = validate_dedup_records_against_inventory()
        assert count == 0 and orphans == []
        from utils.infra.db import db_load_dedup_records
        rows = db_load_dedup_records()
        assert int(rows[0].get('IsDeleted') or 0) == 0

    def test_writes_orphan_csv(self, storage_mode_db, tmp_path, monkeypatch):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))
        _add_inventory([('A', 'p/A')])
        _add_dedup_pending('B', 'p/B')
        validate_dedup_records_against_inventory()
        # Find the orphan CSV under DEDUP_DIR
        files = list((tmp_path / 'Dedup').rglob('orphans-*.csv'))
        assert len(files) == 1
        with open(files[0], encoding='utf-8') as f:
            text = f.read()
        assert 'p/B' in text
        assert ORPHAN_REASON_SUFFIX in text


class TestRunValidateInventory:
    def test_prunes_inventory_and_chains_dedup_self_heal(
        self, storage_mode_db, tmp_path, monkeypatch,
    ):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))
        monkeypatch.setattr(
            rm, 'RCLONE_INVENTORY_CSV', 'rclone_inventory.csv'
        )

        _add_inventory([
            ('A', '2025/Actor/A/有码-中字'),
            ('B', '2025/Actor/B/有码-中字'),
            ('X', '2025/Actor/X/有码-中字'),
        ])
        _add_dedup_pending('A', '2025/Actor/A/有码-中字')
        _add_dedup_pending('X', '2025/Actor/X/有码-中字')

        # Mock the remote-listing to drop 'X' and the year-folder discovery.
        def fake_year_folders(*_a, **_k):
            return ['2025']

        def fake_list(remote_name, root_folder, year):
            return [
                '2025/Actor/A/有码-中字',
                '2025/Actor/B/有码-中字',
            ]

        monkeypatch.setattr(rm, 'get_year_folders', fake_year_folders)
        monkeypatch.setattr(rm, '_list_remote_dirs_for_year', fake_list)

        rc = run_validate_inventory(
            'gdrive', 'root', year_filter=None, max_workers=1, prune=True,
        )
        assert rc == 0

        from utils.infra.db import db_load_rclone_inventory, db_load_dedup_records
        inv = db_load_rclone_inventory()
        assert 'A' in inv and 'B' in inv
        assert 'X' not in inv

        rows = db_load_dedup_records()
        x_row = [r for r in rows if r['VideoCode'] == 'X'][0]
        assert int(x_row['IsDeleted']) == 1
        assert ORPHAN_REASON_SUFFIX in x_row['DeletionReason']

        # Inventory orphan CSV written.
        orphans_csv = tmp_path / 'inventory_orphans.csv'
        assert orphans_csv.exists()
        assert '2025/Actor/X/有码-中字' in orphans_csv.read_text(encoding='utf-8')

    def test_no_prune_keeps_inventory(self, storage_mode_db, tmp_path, monkeypatch):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))

        _add_inventory([
            ('A', '2025/Actor/A/有码-中字'),
            ('X', '2025/Actor/X/有码-中字'),
        ])

        monkeypatch.setattr(rm, 'get_year_folders', lambda *_a, **_k: ['2025'])
        monkeypatch.setattr(
            rm, '_list_remote_dirs_for_year',
            lambda *_a, **_k: ['2025/Actor/A/有码-中字'],
        )

        rc = run_validate_inventory(
            'gdrive', 'root', year_filter=None, max_workers=1, prune=False,
        )
        assert rc == 0
        from utils.infra.db import db_load_rclone_inventory
        inv = db_load_rclone_inventory()
        assert 'A' in inv and 'X' in inv  # not pruned

    def test_aborts_when_remote_returns_zero(self, storage_mode_db, tmp_path, monkeypatch):
        import scripts.rclone_manager as rm
        monkeypatch.setattr(rm, 'REPORTS_DIR', str(tmp_path))
        monkeypatch.setattr(rm, 'DEDUP_DIR', str(tmp_path / 'Dedup'))

        _add_inventory([('A', '2025/Actor/A/有码-中字')])
        monkeypatch.setattr(rm, 'get_year_folders', lambda *_a, **_k: ['2025'])
        monkeypatch.setattr(rm, '_list_remote_dirs_for_year', lambda *_a, **_k: [])

        rc = run_validate_inventory(
            'gdrive', 'root', year_filter=None, max_workers=1, prune=True,
        )
        assert rc == 1
        from utils.infra.db import db_load_rclone_inventory
        assert 'A' in db_load_rclone_inventory()


class TestValidateCli:
    def test_validate_alone_ok(self):
        args = parse_arguments(['--validate'])
        assert args.validate is True
        assert args.validate_prune is True

    def test_no_validate_prune(self):
        args = parse_arguments(['--validate', '--no-validate-prune'])
        assert args.validate_prune is False

    def test_validate_conflicts_with_scan(self):
        with pytest.raises(SystemExit):
            parse_arguments(['--validate', '--scan'])

    def test_validate_conflicts_with_report(self):
        with pytest.raises(SystemExit):
            parse_arguments(['--validate', '--report'])
