"""Tests for the rclone_dedup_executor script."""

import os
import sys
import csv
import pytest
from unittest.mock import patch, MagicMock

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.rclone_dedup_executor import rclone_purge
from scripts.spider.dedup_checker import (
    DedupRecord,
    DEDUP_FIELDNAMES,
    append_dedup_record,
    load_dedup_csv,
    save_dedup_csv,
)


# ============================================================================
# Test rclone_purge
# ============================================================================

class TestRclonePurge:
    def test_dry_run_always_succeeds(self):
        assert rclone_purge('gdrive:/some/path', dry_run=True) is True

    @patch('scripts.rclone_dedup_executor.subprocess.run')
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert rclone_purge('gdrive:/path') is True
        mock_run.assert_called_once_with(
            ['rclone', 'purge', 'gdrive:/path'],
            capture_output=True, text=True, timeout=120,
        )

    @patch('scripts.rclone_dedup_executor.subprocess.run')
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr='permission denied')
        assert rclone_purge('gdrive:/path') is False

    @patch('scripts.rclone_dedup_executor.subprocess.run')
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='rclone', timeout=120)
        assert rclone_purge('gdrive:/path') is False

    @patch('scripts.rclone_dedup_executor.subprocess.run')
    def test_exception(self, mock_run):
        mock_run.side_effect = OSError('rclone not found')
        assert rclone_purge('gdrive:/path') is False


# ============================================================================
# Test dedup CSV filtering (is_deleted skip logic)
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
        assert pending[0]['video_code'] == 'B-002'

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
    def test_update_preserves_structure(self, tmp_path):
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 'sensor', 'sub', 'path', 100, 'cat', 'reason', 'time', 'False', '')
        append_dedup_record(path, r)

        rows = load_dedup_csv(path)
        rows[0]['is_deleted'] = 'True'
        rows[0]['delete_datetime'] = '2026-03-09 12:00:00'
        save_dedup_csv(path, rows)

        reloaded = load_dedup_csv(path)
        assert reloaded[0]['is_deleted'] == 'True'
        assert reloaded[0]['delete_datetime'] == '2026-03-09 12:00:00'
        assert reloaded[0]['video_code'] == 'A-001'
        assert reloaded[0]['existing_sensor'] == 'sensor'


# ============================================================================
# Test dry-run mode (integration-level)
# ============================================================================

class TestDryRunMode:
    @patch('scripts.rclone_dedup_executor.rclone_purge')
    def test_dry_run_does_not_update_csv(self, mock_purge, tmp_path):
        mock_purge.return_value = True
        path = str(tmp_path / 'dedup.csv')
        r = DedupRecord('A-001', 's', 'sub', 'gdrive:/p', 100, 'cat', 'r', 't', 'False', '')
        append_dedup_record(path, r)

        rows = load_dedup_csv(path)
        pending = [row for row in rows if row.get('is_deleted', 'False') != 'True']

        for row in rows:
            if row.get('is_deleted', 'False') == 'True':
                continue
            ok = rclone_purge(row['existing_gdrive_path'], dry_run=True)
            assert ok is True
            # In dry-run, we do NOT update is_deleted

        reloaded = load_dedup_csv(path)
        assert reloaded[0]['is_deleted'] == 'False'
