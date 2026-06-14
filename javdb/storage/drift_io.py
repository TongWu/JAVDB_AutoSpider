"""Shared helpers for drift JSONL input/output.

This module deliberately depends only on the Python standard library so
standalone migration tools can import it before the rest of the application
configuration is available.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import List

logger = logging.getLogger(__name__)

_DRIFT_LOG_LOCK = threading.Lock()


def _values_equal(a, b) -> bool:
    """Row-cell equality with type-loose comparison.

    SQLite returns Python ints / floats / str / None; D1's HTTP API returns
    JSON-decoded values which may swap int/float. We coerce to ``float`` only
    when at least one side is genuinely a float, since ``float(big_int)``
    silently loses precision above 2**53 and would falsely report distinct
    large integers (e.g. magnet hashes, AUTOINCREMENT IDs near 2**60) as equal.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    # Booleans are ints in Python; treat them as numeric here.
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row)


def read_jsonl(path: str) -> List[dict]:
    """Read JSONL records from *path*, warning and skipping malformed lines."""
    if not os.path.exists(path):
        return []
    records: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Skipping malformed JSONL line %s in %s: %s",
                    line_no, path, exc,
                )
    return records


def drift_log_path(reports_dir: str | None = None) -> str:
    """Resolve the drift JSONL path at call time."""
    base = reports_dir or os.environ.get("REPORTS_DIR") or "reports"
    return os.path.join(base, "D1", "d1_drift.jsonl")


def append_jsonl_record(
    record: dict,
    *,
    reports_dir: str | None = None,
    filename: str = "d1_drift.jsonl",
) -> None:
    """Append *record* as one JSON line to ``<reports_dir>/D1/<filename>``.

    The function never raises: diagnostic metric emission must not block the
    primary operation. Under pytest, writes to the default tracked
    ``reports/D1/d1_drift.jsonl`` path are refused unless the caller isolates
    the sink with ``reports_dir`` or ``REPORTS_DIR``.
    """
    base = reports_dir or os.environ.get("REPORTS_DIR") or "reports"
    path = os.path.join(base, "D1", filename)
    tracked_default = os.path.abspath(os.path.join("reports", "D1", filename))
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.path.abspath(path) == tracked_default
    ):
        logger.warning(
            "Refusing to append %s to the default reports/ dir under "
            "PYTEST_CURRENT_TEST=%s; the test must set REPORTS_DIR or pass "
            "reports_dir= to isolate the metric sink.",
            filename, os.environ.get("PYTEST_CURRENT_TEST"),
        )
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _DRIFT_LOG_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to append JSONL record to %s: %s", path, exc)
