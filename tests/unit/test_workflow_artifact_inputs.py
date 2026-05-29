from __future__ import annotations

import csv
from pathlib import Path

from javdb.workflow.artifact_inputs import (
    CsvInputResolution,
    read_torrent_csv,
    resolve_qb_uploader_csv_path,
)


def test_resolve_qb_uploader_csv_path_uses_full_input_path(tmp_path):
    csv_path = tmp_path / "custom.csv"

    result = resolve_qb_uploader_csv_path(
        mode="daily",
        input_file=str(csv_path),
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/dated/{name}",
        latest_daily_finder=lambda: "daily.csv",
        latest_adhoc_finder=lambda: "adhoc.csv",
    )

    assert result == CsvInputResolution(path=str(csv_path), source="explicit-path")


def test_resolve_qb_uploader_csv_path_builds_dated_daily_path():
    result = resolve_qb_uploader_csv_path(
        mode="daily",
        input_file="daily.csv",
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/2026/05/{name}",
        latest_daily_finder=lambda: "unused-daily.csv",
        latest_adhoc_finder=lambda: "unused-adhoc.csv",
    )

    assert result == CsvInputResolution(
        path="reports/DailyReport/2026/05/daily.csv",
        source="explicit-name",
    )


def test_resolve_qb_uploader_csv_path_uses_latest_adhoc_when_no_input():
    result = resolve_qb_uploader_csv_path(
        mode="adhoc",
        input_file=None,
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/{name}",
        latest_daily_finder=lambda: "daily.csv",
        latest_adhoc_finder=lambda: "adhoc.csv",
    )

    assert result == CsvInputResolution(path="adhoc.csv", source="latest")


def test_resolve_qb_uploader_csv_path_latest_missing_returns_empty():
    result = resolve_qb_uploader_csv_path(
        mode="daily",
        input_file=None,
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/{name}",
        latest_daily_finder=lambda: None,
        latest_adhoc_finder=lambda: None,
    )

    assert result == CsvInputResolution(path="", source="latest")


def test_read_torrent_csv_returns_rows_and_success(tmp_path):
    csv_path = tmp_path / "torrents.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "magnet", "type"])
        writer.writeheader()
        writer.writerow({"title": "Movie A", "magnet": "magnet:?xt=urn:btih:abc", "type": "subtitle"})

    rows, ok = read_torrent_csv(str(csv_path))

    assert ok is True
    assert rows == [{"title": "Movie A", "magnet": "magnet:?xt=urn:btih:abc", "type": "subtitle"}]


def test_read_torrent_csv_missing_file_returns_false(tmp_path):
    rows, ok = read_torrent_csv(str(tmp_path / "missing.csv"))

    assert rows == []
    assert ok is False
