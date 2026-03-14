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
    mark_records_deleted,
    cleanup_deleted_records,
    _raw_csv_read,
)
import utils.db as db_mod


# ============================================================================
# Helpers
# ============================================================================

def _seed_inventory(rows):
    """Seed SQLite rclone_inventory with the given list-of-dicts."""
    db_mod.db_replace_rclone_inventory(rows)


def _inventory_row(code, sensor='有码', subtitle='中字', path='gdrive:/r/2025/a/f', size=1000, count=2):
    return {
        'video_code': code,
        'sensor_category': sensor,
        'subtitle_category': subtitle,
        'folder_path': path,
        'folder_size': size,
        'file_count': count,
        'scan_datetime': '2026-01-01 00:00:00',
    }


# ============================================================================
# Test load_rclone_inventory
# ============================================================================

class TestLoadRcloneInventory:
    def test_loads_normal(self):
        _seed_inventory([
            _inventory_row('ABC-123'),
            _inventory_row('XYZ-456', sensor='无码'),
        ])
        inv = load_rclone_inventory('')
        assert len(inv) == 2
        assert 'ABC-123' in inv
        assert 'XYZ-456' in inv
        assert inv['ABC-123'][0].sensor_category == '有码'

    def test_multiple_entries_same_code(self):
        _seed_inventory([
            _inventory_row('ABC-123', subtitle='无字'),
            _inventory_row('ABC-123', subtitle='中字'),
        ])
        inv = load_rclone_inventory('')
        assert len(inv) == 1
        assert len(inv['ABC-123']) == 2

    def test_empty_inventory(self):
        inv = load_rclone_inventory('')
        assert inv == {}

    def test_case_insensitive_code(self):
        _seed_inventory([_inventory_row('abc-123')])
        inv = load_rclone_inventory('')
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

class TestDedupIO:
    """Tests for dedup record I/O (SQLite-backed)."""

    def test_append_and_load(self):
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
        append_dedup_record('', record)
        rows = load_dedup_csv('')
        assert len(rows) == 1
        assert rows[0]['video_code'] == 'ABC-123'

    def test_append_preserves_existing(self):
        r1 = DedupRecord('A-001', 's', 'sub', 'p1', 100, 'cat', 'reason', 't', 'False', '')
        r2 = DedupRecord('B-002', 's', 'sub', 'p2', 200, 'cat', 'reason', 't', 'False', '')
        append_dedup_record('', r1)
        append_dedup_record('', r2)
        rows = load_dedup_csv('')
        assert len(rows) == 2

    def test_save_updates_deleted(self):
        r1 = DedupRecord('A-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        append_dedup_record('', r1)

        rows = load_dedup_csv('')
        rows[0]['is_deleted'] = 'True'
        rows[0]['delete_datetime'] = '2026-01-02 00:00:00'
        save_dedup_csv('', rows)

        reloaded = load_dedup_csv('')
        assert len(reloaded) == 1
        assert reloaded[0]['is_deleted'] == 'True'

    def test_load_empty(self):
        assert load_dedup_csv('') == []

    def test_append_skips_duplicate_path(self):
        """Same existing_gdrive_path should be skipped (Fix 2)."""
        r1 = DedupRecord('A-001', 's', 'sub', 'gdrive:/same_path', 100, 'cat', 'r', 't1', 'False', '')
        r2 = DedupRecord('A-001', 's', 'sub', 'gdrive:/same_path', 200, 'cat', 'r', 't2', 'False', '')
        assert append_dedup_record('', r1) is True
        assert append_dedup_record('', r2) is False
        rows = load_dedup_csv('')
        assert len(rows) == 1

    def test_append_allows_after_deleted(self):
        """Same path re-append after deletion should succeed (Edge 2b)."""
        r = DedupRecord('A-001', 's', 'sub', 'gdrive:/path', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r)
        mark_records_deleted('', [('gdrive:/path', '2026-01-01 00:00:00')])
        assert append_dedup_record('', r) is True
        rows = load_dedup_csv('')
        assert len(rows) == 2

    def test_append_same_code_different_paths(self):
        """Same video_code but different paths are both allowed (Edge 2a)."""
        r1 = DedupRecord('A-001', 's', 'sub', 'gdrive:/path1', 100, 'cat', 'r', 't', 'False', '')
        r2 = DedupRecord('A-001', 's', 'sub', 'gdrive:/path2', 200, 'cat', 'r', 't', 'False', '')
        assert append_dedup_record('', r1) is True
        assert append_dedup_record('', r2) is True
        assert len(load_dedup_csv('')) == 2


# ── mark_records_deleted tests ───────────────────────────────────────────

class TestMarkRecordsDeleted:
    def test_marks_pending_record(self):
        r = DedupRecord('A-001', 's', 'sub', 'gdrive:/p1', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r)
        updated = mark_records_deleted('', [('gdrive:/p1', '2026-01-02 00:00:00')])
        assert updated == 1
        rows = load_dedup_csv('')
        assert rows[0]['is_deleted'] == 'True'
        assert rows[0]['delete_datetime'] == '2026-01-02 00:00:00'

    def test_idempotent(self):
        r = DedupRecord('A-001', 's', 'sub', 'gdrive:/p1', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r)
        mark_records_deleted('', [('gdrive:/p1', '2026-01-02 00:00:00')])
        updated = mark_records_deleted('', [('gdrive:/p1', '2026-01-03 00:00:00')])
        assert updated == 0

    def test_db_only_no_csv(self, tmp_path):
        """mark_records_deleted updates DB only; no CSV file is written."""
        csv_path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 's', 'sub', 'gdrive:/p1', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record(csv_path, r)
        mark_records_deleted(csv_path, [('gdrive:/p1', '2026-01-02 00:00:00')])
        rows = load_dedup_csv('')
        assert rows[0]['is_deleted'] == 'True'
        assert not os.path.exists(csv_path)

    def test_invalidates_cache(self):
        """After marking deleted, append of same path should succeed."""
        r = DedupRecord('A-001', 's', 'sub', 'gdrive:/p1', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r)
        mark_records_deleted('', [('gdrive:/p1', '2026-01-02 00:00:00')])
        assert append_dedup_record('', r) is True


# ── cleanup_deleted_records tests ────────────────────────────────────────

class TestCleanupDeletedRecords:
    def test_removes_old_records(self):
        r1 = DedupRecord('OLD', 's', 'sub', 'gdrive:/old', 100, 'cat', 'r', 't', 'False', '')
        r2 = DedupRecord('NEW', 's', 'sub', 'gdrive:/new', 200, 'cat', 'r', 't', 'False', '')
        r3 = DedupRecord('PENDING', 's', 'sub', 'gdrive:/pend', 300, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r1)
        append_dedup_record('', r2)
        append_dedup_record('', r3)
        mark_records_deleted('', [
            ('gdrive:/old', '2020-01-01 00:00:00'),
            ('gdrive:/new', '2099-01-01 00:00:00'),
        ])
        removed = cleanup_deleted_records('', older_than_days=30)
        assert removed == 1
        codes = {r['video_code'] for r in load_dedup_csv('')}
        assert 'OLD' not in codes
        assert 'NEW' in codes
        assert 'PENDING' in codes

    def test_db_cleanup_no_csv(self, tmp_path):
        """cleanup_deleted_records operates on DB only; no CSV written."""
        csv_path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('OLD', 's', 'sub', 'gdrive:/old', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record(csv_path, r)
        mark_records_deleted(csv_path, [('gdrive:/old', '2020-01-01 00:00:00')])
        cleanup_deleted_records(csv_path, older_than_days=30)
        rows = load_dedup_csv('')
        assert not any(r['video_code'] == 'OLD' for r in rows)
        assert not os.path.exists(csv_path)

    def test_zero_retention(self):
        """Edge 3b: retention_days=0 removes everything with valid timestamp."""
        r = DedupRecord('A', 's', 'sub', 'gdrive:/p', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r)
        mark_records_deleted('', [('gdrive:/p', '2026-03-01 00:00:00')])
        removed = cleanup_deleted_records('', older_than_days=0)
        assert removed == 1


# ── STORAGE_MODE tests ──────────────────────────────────────────────────

class TestStorageModeDb:
    """db mode: reads/writes only hit SQLite."""

    def test_append_and_load(self, storage_mode_db):
        r = DedupRecord('DB-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        append_dedup_record('/nonexistent.csv', r)
        rows = load_dedup_csv('/nonexistent.csv')
        assert len(rows) == 1
        assert rows[0]['video_code'] == 'DB-001'

    def test_inventory_reads_sqlite(self, storage_mode_db):
        _seed_inventory([_inventory_row('DB-INV')])
        inv = load_rclone_inventory('/nonexistent.csv')
        assert 'DB-INV' in inv


class TestStorageModeCsv:
    """csv mode: dedup always writes to DB (force=True), no CSV mirror."""

    def test_append_db_only(self, tmp_path, storage_mode_csv):
        csv_path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('CSV-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        append_dedup_record(csv_path, r)
        # DB should still be written (dedup forces DB init)
        rows = db_mod.db_load_dedup_records()
        assert any(row['video_code'] == 'CSV-001' for row in rows)
        # CSV is no longer written as a mirror
        assert not os.path.exists(csv_path)

    def test_inventory_reads_csv(self, tmp_path, storage_mode_csv):
        csv_path = str(tmp_path / 'inv.csv')
        import csv as csv_mod
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            w = csv_mod.DictWriter(f, fieldnames=[
                'video_code', 'sensor_category', 'subtitle_category',
                'folder_path', 'folder_size', 'file_count', 'scan_datetime',
            ])
            w.writeheader()
            w.writerow(_inventory_row('CSV-INV'))
        inv = load_rclone_inventory(csv_path)
        assert 'CSV-INV' in inv


class TestStorageModeDuo:
    """duo mode: dedup writes to DB only (no CSV mirror)."""

    def test_append_db_only(self, tmp_path, storage_mode_duo):
        csv_path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('DUO-001', 's', 'sub', 'p', 100, 'cat', 'reason', 't', 'False', '')
        append_dedup_record(csv_path, r)
        rows_sqlite = db_mod.db_load_dedup_records()
        assert any(row['video_code'] == 'DUO-001' for row in rows_sqlite)
        assert not os.path.exists(csv_path)


class TestExportDedupDbToCsv:
    """Tests for the export_dedup_db_to_csv function."""

    def test_export_creates_csv(self, tmp_path):
        from scripts.spider.dedup_checker import export_dedup_db_to_csv
        r = DedupRecord('EXP-001', 's', 'sub', 'gdrive:/export', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record('', r)
        output = str(tmp_path / 'dedup_history.csv')
        count = export_dedup_db_to_csv(output)
        assert count == 1
        assert os.path.exists(output)
        rows = _raw_csv_read(output)
        assert len(rows) == 1
        assert rows[0]['video_code'] == 'EXP-001'

    def test_export_empty_db(self, tmp_path):
        from scripts.spider.dedup_checker import export_dedup_db_to_csv
        output = str(tmp_path / 'dedup_history.csv')
        count = export_dedup_db_to_csv(output)
        assert count == 0
        assert not os.path.exists(output)
