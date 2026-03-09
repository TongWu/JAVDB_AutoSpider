"""Tests for the rclone_inventory script."""

import os
import sys
import csv
import base64
import tempfile
import pytest
from unittest.mock import patch, MagicMock

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.rclone_inventory import (
    setup_rclone_config_from_base64,
    parse_root_path,
    extract_video_code_from_filename,
    write_inventory_csv,
    INVENTORY_FIELDNAMES,
)
from scripts.rclone_dedup import FolderInfo


# ============================================================================
# Test setup_rclone_config_from_base64
# ============================================================================

class TestSetupRcloneConfigFromBase64:
    def test_empty_config(self):
        assert setup_rclone_config_from_base64('') is False

    def test_valid_base64(self, tmp_path, monkeypatch):
        config_content = b'[gdrive]\ntype = drive\n'
        b64 = base64.b64encode(config_content).decode()
        fake_rclone_dir = str(tmp_path / '.config' / 'rclone')
        monkeypatch.setattr(
            'scripts.rclone_inventory.os.path.expanduser',
            lambda path: path.replace('~', str(tmp_path)),
        )
        result = setup_rclone_config_from_base64(b64)
        assert result is True
        config_path = os.path.join(fake_rclone_dir, 'rclone.conf')
        written = open(config_path, 'rb').read()
        assert written == config_content

    def test_invalid_base64(self):
        assert setup_rclone_config_from_base64('not-valid-base64!!!') is False


# ============================================================================
# Test parse_root_path
# ============================================================================

class TestParseRootPath:
    def test_normal_path(self):
        remote, folder = parse_root_path('gdrive:/剧集/不可以色色/JAV-Sync')
        assert remote == 'gdrive'
        assert folder == '剧集/不可以色色/JAV-Sync'

    def test_path_with_trailing_slash(self):
        remote, folder = parse_root_path('myremote:/some/path/')
        assert remote == 'myremote'
        assert folder == 'some/path'

    def test_missing_colon(self):
        with pytest.raises(ValueError, match="missing ':'"):
            parse_root_path('no-colon-here')

    def test_root_only(self):
        remote, folder = parse_root_path('gdrive:/')
        assert remote == 'gdrive'
        assert folder == ''


# ============================================================================
# Test extract_video_code_from_filename
# ============================================================================

class TestExtractVideoCodeFromFilename:
    def test_standard_code(self):
        assert extract_video_code_from_filename('ABC-123.mp4') == 'ABC-123'

    def test_code_in_path(self):
        assert extract_video_code_from_filename('[SUB] SSIS-456 Director Cut.mkv') == 'SSIS-456'

    def test_no_code(self):
        assert extract_video_code_from_filename('random_movie.mp4') is None

    def test_code_with_many_digits(self):
        assert extract_video_code_from_filename('STARS-12345.mp4') == 'STARS-12345'


# ============================================================================
# Test write_inventory_csv
# ============================================================================

class TestWriteInventoryCsv:
    def test_writes_correct_csv(self, tmp_path):
        folders = [
            FolderInfo(
                full_path='gdrive:root/2025/Actor/ABC-123 [有码-中字]',
                year='2025', actor='Actor', movie_code='ABC-123',
                sensor_category='有码', subtitle_category='中字',
                folder_name='ABC-123 [有码-中字]',
                size=1024000, file_count=3,
            ),
        ]
        output = str(tmp_path / 'inventory.csv')
        count = write_inventory_csv(folders, output, 'gdrive', 'root')
        assert count == 1

        with open(output, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]['video_code'] == 'ABC-123'
        assert rows[0]['sensor_category'] == '有码'
        assert rows[0]['subtitle_category'] == '中字'
        assert rows[0]['folder_size'] == '1024000'
        assert rows[0]['file_count'] == '3'

    def test_multiple_entries_same_code(self, tmp_path):
        folders = [
            FolderInfo(
                full_path='gdrive:root/2025/ActorA/XYZ-001 [无码-无字]',
                year='2025', actor='ActorA', movie_code='XYZ-001',
                sensor_category='无码', subtitle_category='无字',
                folder_name='XYZ-001 [无码-无字]',
                size=500, file_count=1,
            ),
            FolderInfo(
                full_path='gdrive:root/2025/ActorB/XYZ-001 [无码-中字]',
                year='2025', actor='ActorB', movie_code='XYZ-001',
                sensor_category='无码', subtitle_category='中字',
                folder_name='XYZ-001 [无码-中字]',
                size=600, file_count=2,
            ),
        ]
        output = str(tmp_path / 'inventory.csv')
        count = write_inventory_csv(folders, output, 'gdrive', 'root')
        assert count == 2

    def test_empty_folder_list(self, tmp_path):
        output = str(tmp_path / 'inventory.csv')
        count = write_inventory_csv([], output, 'gdrive', 'root')
        assert count == 0
        with open(output, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 0
