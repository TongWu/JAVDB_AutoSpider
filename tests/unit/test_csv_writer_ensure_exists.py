"""Tests for ensure_csv_exists (header-only CSV for zero-entry runs).

Incremental CSV writes only create the file once the first row is persisted.
A run where every index entry is filtered out therefore reported a
``csv_filename`` that did not exist on disk, and the qBittorrent uploader
fail-fasted on the missing file — turning a legitimate "no new torrents" day
into a pipeline failure. ``ensure_csv_exists`` closes that gap.
"""

from __future__ import annotations

import csv
from pathlib import Path

from javdb.infra.csv_writer import ensure_csv_exists
from javdb.integrations.qb.uploader.service import read_csv_file
from javdb.workflow.artifact_inputs import read_torrent_csv

FIELDNAMES = [
    'href', 'video_code', 'page', 'actor', 'rate', 'comment_number',
    'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
]


class TestEnsureCsvExists:
    def test_creates_header_only_file_when_missing(self, tmp_path: Path, storage_mode_duo):
        csv_path = tmp_path / "Javdb_TodayTitle_20260805.csv"

        assert ensure_csv_exists(str(csv_path), FIELDNAMES) is True
        assert csv_path.exists()

        with open(csv_path, newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == FIELDNAMES
            assert list(reader) == []

    def test_creates_missing_parent_directory(self, tmp_path: Path, storage_mode_duo):
        csv_path = tmp_path / "DailyReport" / "2026" / "08" / "report.csv"

        assert ensure_csv_exists(str(csv_path), FIELDNAMES) is True
        assert csv_path.exists()

    def test_does_not_overwrite_existing_file(self, tmp_path: Path, storage_mode_duo):
        csv_path = tmp_path / "report.csv"
        csv_path.write_text("href,video_code\n/v/ABC,ABC-123\n", encoding='utf-8')

        assert ensure_csv_exists(str(csv_path), FIELDNAMES) is False
        assert "ABC-123" in csv_path.read_text(encoding='utf-8')

    def test_noop_on_dry_run(self, tmp_path: Path, storage_mode_duo):
        csv_path = tmp_path / "report.csv"

        assert ensure_csv_exists(str(csv_path), FIELDNAMES, dry_run=True) is False
        assert not csv_path.exists()

    def test_noop_without_csv_path(self, storage_mode_duo):
        assert ensure_csv_exists(None, FIELDNAMES) is False
        assert ensure_csv_exists('', FIELDNAMES) is False

    def test_noop_when_csv_storage_disabled(self, tmp_path: Path, storage_mode_db):
        csv_path = tmp_path / "report.csv"

        assert ensure_csv_exists(str(csv_path), FIELDNAMES) is False
        assert not csv_path.exists()


class TestUploaderReadsHeaderOnlyCsv:
    """The header-only file must read as an empty-but-valid CSV.

    ``read_torrent_csv`` returning ok=False is what made the uploader refuse
    to run; a header-only file has to come back as ``([], True)`` so the
    uploader treats it as "no work to do" rather than "incomplete data".
    """

    def test_header_only_csv_reads_as_ok_with_no_rows(self, tmp_path: Path, storage_mode_duo):
        csv_path = tmp_path / "report.csv"
        ensure_csv_exists(str(csv_path), FIELDNAMES)

        rows, ok = read_torrent_csv(str(csv_path))

        assert ok is True
        assert rows == []

    def test_missing_csv_still_reads_as_not_ok(self, tmp_path: Path):
        rows, ok = read_torrent_csv(str(tmp_path / "absent.csv"))

        assert ok is False
        assert rows == []

    def test_uploader_accepts_header_only_csv(self, tmp_path: Path, storage_mode_duo):
        """The guard that failed the pipeline must now pass."""
        csv_path = tmp_path / "report.csv"
        ensure_csv_exists(str(csv_path), FIELDNAMES)

        torrents, csv_ok = read_csv_file(str(csv_path))

        assert csv_ok is True
        assert torrents == []

    def test_uploader_still_rejects_missing_csv(self, tmp_path: Path, storage_mode_duo):
        torrents, csv_ok = read_csv_file(str(tmp_path / "absent.csv"))

        assert csv_ok is False
        assert torrents == []
