"""Tests for the dedup_checker module."""

import os
import sys
import csv
import tempfile
import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.spider.dedup_checker import (
    RcloneEntry,
    DedupRecord,
    DEDUP_FIELDNAMES,
    load_rclone_inventory,
    is_in_rclone_inventory,
    should_skip_from_rclone,
    check_dedup_upgrade,
    append_dedup_record,
    load_dedup_csv,
    save_dedup_csv,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_inventory_csv(tmp_path, rows):
    """Create a rclone_inventory.csv with the given list-of-dicts."""
    path = str(tmp_path / 'rclone_inventory.csv')
    fieldnames = [
        'video_code', 'sensor_category', 'subtitle_category',
        'folder_path', 'folder_size', 'file_count', 'scan_datetime',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


def _inventory_row(code, sensor='有码', subtitle='中字', path='gdrive:/r/2025/a/f', size=1000, count=2):
    return {
        'video_code': code,
        'sensor_category': sensor,
        'subtitle_category': subtitle,
        'folder_path': path,
        'folder_size': str(size),
        'file_count': str(count),
        'scan_datetime': '2026-01-01 00:00:00',
    }


# ============================================================================
# Test load_rclone_inventory
# ============================================================================

class TestLoadRcloneInventory:
    def test_loads_normal_csv(self, tmp_path):
        path = _make_inventory_csv(tmp_path, [
            _inventory_row('ABC-123'),
            _inventory_row('XYZ-456', sensor='无码'),
        ])
        inv = load_rclone_inventory(path)
        assert len(inv) == 2
        assert 'ABC-123' in inv
        assert 'XYZ-456' in inv
        assert inv['ABC-123'][0].sensor_category == '有码'

    def test_multiple_entries_same_code(self, tmp_path):
        path = _make_inventory_csv(tmp_path, [
            _inventory_row('ABC-123', subtitle='无字'),
            _inventory_row('ABC-123', subtitle='中字'),
        ])
        inv = load_rclone_inventory(path)
        assert len(inv) == 1
        assert len(inv['ABC-123']) == 2

    def test_file_not_found(self, tmp_path):
        inv = load_rclone_inventory(str(tmp_path / 'missing.csv'))
        assert inv == {}

    def test_empty_csv(self, tmp_path):
        path = _make_inventory_csv(tmp_path, [])
        inv = load_rclone_inventory(path)
        assert inv == {}

    def test_case_insensitive_code(self, tmp_path):
        path = _make_inventory_csv(tmp_path, [
            _inventory_row('abc-123'),
        ])
        inv = load_rclone_inventory(path)
        assert 'ABC-123' in inv


# ============================================================================
# Test is_in_rclone_inventory / should_skip_from_rclone
# ============================================================================

class TestSkipLogic:
    def test_is_in_inventory(self):
        inv = {'ABC-123': [RcloneEntry('ABC-123', '有码', '中字', 'p', 100, 1, 't')]}
        assert is_in_rclone_inventory('ABC-123', inv) is True
        assert is_in_rclone_inventory('XYZ-999', inv) is False

    def test_case_insensitive(self):
        inv = {'ABC-123': [RcloneEntry('ABC-123', '有码', '中字', 'p', 100, 1, 't')]}
        assert is_in_rclone_inventory('abc-123', inv) is True

    def test_skip_when_has_zhongzi(self):
        inv = {'ABC-123': [RcloneEntry('ABC-123', '有码', '中字', 'p', 100, 1, 't')]}
        assert should_skip_from_rclone('ABC-123', inv, enable_dedup=False) is True

    def test_no_skip_when_only_wuzi(self):
        inv = {'ABC-123': [RcloneEntry('ABC-123', '有码', '无字', 'p', 100, 1, 't')]}
        assert should_skip_from_rclone('ABC-123', inv, enable_dedup=False) is False

    def test_no_skip_when_dedup_enabled(self):
        inv = {'ABC-123': [RcloneEntry('ABC-123', '有码', '中字', 'p', 100, 1, 't')]}
        assert should_skip_from_rclone('ABC-123', inv, enable_dedup=True) is False

    def test_no_skip_when_not_in_inventory(self):
        inv = {'ABC-123': [RcloneEntry('ABC-123', '有码', '中字', 'p', 100, 1, 't')]}
        assert should_skip_from_rclone('XYZ-999', inv, enable_dedup=False) is False


# ============================================================================
# Test check_dedup_upgrade
# ============================================================================

class TestCheckDedupUpgrade:
    def test_subtitle_upgrade(self):
        entries = [RcloneEntry('ABC-123', '有码', '无字', 'p', 1000, 1, 't')]
        types = {'subtitle': True, 'hacked_subtitle': False, 'hacked_no_subtitle': False, 'no_subtitle': False}
        records = check_dedup_upgrade('ABC-123', types, entries)
        assert len(records) == 1
        assert 'Subtitle upgrade' in records[0].deletion_reason

    def test_no_upgrade_same_subtitle(self):
        entries = [RcloneEntry('ABC-123', '有码', '中字', 'p', 1000, 1, 't')]
        types = {'subtitle': True, 'hacked_subtitle': False, 'hacked_no_subtitle': False, 'no_subtitle': False}
        records = check_dedup_upgrade('ABC-123', types, entries)
        assert len(records) == 0

    def test_sensor_upgrade_pojie_to_wuma(self):
        entries = [RcloneEntry('ABC-123', '无码破解', '中字', 'p', 1000, 1, 't')]
        types = {'subtitle': True, 'hacked_subtitle': False, 'hacked_no_subtitle': False, 'no_subtitle': False}
        records = check_dedup_upgrade('ABC-123', types, entries)
        assert len(records) == 1
        assert 'Sensor upgrade' in records[0].deletion_reason

    def test_no_sensor_upgrade_for_youma(self):
        entries = [RcloneEntry('ABC-123', '有码', '中字', 'p', 1000, 1, 't')]
        types = {'subtitle': True, 'hacked_subtitle': False, 'hacked_no_subtitle': False, 'no_subtitle': False}
        records = check_dedup_upgrade('ABC-123', types, entries)
        assert len(records) == 0

    def test_no_upgrade_when_only_no_subtitle(self):
        entries = [RcloneEntry('ABC-123', '有码', '中字', 'p', 1000, 1, 't')]
        types = {'subtitle': False, 'hacked_subtitle': False, 'hacked_no_subtitle': False, 'no_subtitle': True}
        records = check_dedup_upgrade('ABC-123', types, entries)
        assert len(records) == 0

    def test_multiple_entries_partial_upgrade(self):
        entries = [
            RcloneEntry('ABC-123', '有码', '无字', 'p1', 1000, 1, 't'),
            RcloneEntry('ABC-123', '有码', '中字', 'p2', 2000, 2, 't'),
        ]
        types = {'subtitle': True, 'hacked_subtitle': False, 'hacked_no_subtitle': False, 'no_subtitle': False}
        records = check_dedup_upgrade('ABC-123', types, entries)
        assert len(records) == 1
        assert records[0].existing_gdrive_path == 'p1'


# ============================================================================
# Test dedup.csv I/O
# ============================================================================

class TestDedupCsvIO:
    def test_append_creates_file(self, tmp_path):
        csv_path = str(tmp_path / 'dedup.csv')
        record = DedupRecord(
            video_code='ABC-123',
            existing_sensor='有码',
            existing_subtitle='无字',
            existing_gdrive_path='gdrive:/path',
            existing_folder_size=1000,
            new_torrent_category='中字',
            deletion_reason='Subtitle upgrade',
            detect_datetime='2026-01-01 12:00:00',
            is_deleted='False',
            delete_datetime='',
        )
        append_dedup_record(csv_path, record)
        rows = load_dedup_csv(csv_path)
        assert len(rows) == 1
        assert rows[0]['video_code'] == 'ABC-123'

    def test_append_preserves_existing(self, tmp_path):
        csv_path = str(tmp_path / 'dedup.csv')
        r1 = DedupRecord('A-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        r2 = DedupRecord('B-002', 's', 'sub', 'p', 200, 'cat', 'reason', 't', 'False', '')
        append_dedup_record(csv_path, r1)
        append_dedup_record(csv_path, r2)
        rows = load_dedup_csv(csv_path)
        assert len(rows) == 2

    def test_save_overwrites(self, tmp_path):
        csv_path = str(tmp_path / 'dedup.csv')
        r1 = DedupRecord('A-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        append_dedup_record(csv_path, r1)

        rows = load_dedup_csv(csv_path)
        rows[0]['is_deleted'] = 'True'
        rows[0]['delete_datetime'] = '2026-01-02 00:00:00'
        save_dedup_csv(csv_path, rows)

        reloaded = load_dedup_csv(csv_path)
        assert len(reloaded) == 1
        assert reloaded[0]['is_deleted'] == 'True'

    def test_load_missing_file(self, tmp_path):
        assert load_dedup_csv(str(tmp_path / 'nonexistent.csv')) == []

    def test_fieldnames_match(self, tmp_path):
        csv_path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        append_dedup_record(csv_path, r)
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == DEDUP_FIELDNAMES
