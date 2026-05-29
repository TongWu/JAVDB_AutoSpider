from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class CsvInputResolution:
    path: str
    source: Literal["explicit-path", "explicit-name", "latest"]


def resolve_qb_uploader_csv_path(
    *,
    mode: Literal["daily", "adhoc"],
    input_file: str | None,
    daily_report_dir: str,
    adhoc_dir: str,
    dated_path_resolver: Callable[[str, str], str],
    latest_daily_finder: Callable[[], str | None],
    latest_adhoc_finder: Callable[[], str | None],
) -> CsvInputResolution:
    if input_file:
        if os.path.sep in input_file or input_file.startswith("reports"):
            return CsvInputResolution(path=input_file, source="explicit-path")
        root = adhoc_dir if mode == "adhoc" else daily_report_dir
        return CsvInputResolution(
            path=dated_path_resolver(root, input_file),
            source="explicit-name",
        )

    finder = latest_adhoc_finder if mode == "adhoc" else latest_daily_finder
    return CsvInputResolution(path=finder() or "", source="latest")


def read_torrent_csv(filename: str) -> tuple[list[dict[str, str]], bool]:
    if not filename or not os.path.exists(filename):
        return [], False

    rows: list[dict[str, str]] = []
    try:
        with open(filename, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append({str(key): str(value or "") for key, value in row.items()})
    except Exception:
        return rows, False

    return rows, True
