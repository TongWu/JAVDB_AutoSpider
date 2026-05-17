"""Unit tests for task_service metadata helpers: _extract_params_from_command,
_compute_duration, and the stream response shape."""
from __future__ import annotations

import os
import sys

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from apps.api.services.task_service import (
    _compute_duration,
    _extract_params_from_command,
)


def test_extract_params_daily_basic():
    cmd = [
        "python3", "-u", "-m", "apps.cli.pipeline",
        "--start-page", "1", "--end-page", "10", "--dry-run",
    ]
    params = _extract_params_from_command(cmd)
    assert params["start_page"] == 1
    assert params["end_page"] == 10
    assert params["dry_run"] is True
    assert "use_proxy" not in params


def test_extract_params_adhoc_with_url():
    cmd = [
        "python3", "-u", "-m", "apps.cli.pipeline",
        "--url", "https://javdb.com/actors/EvkJ",
        "--phase", "1", "--ignore-release-date", "--use-proxy",
    ]
    params = _extract_params_from_command(cmd)
    assert params["url"] == "https://javdb.com/actors/EvkJ"
    assert params["phase"] == 1
    assert params["ignore_release_date"] is True
    assert params["use_proxy"] is True


def test_extract_params_empty_command():
    params = _extract_params_from_command([])
    assert params == {}


def test_compute_duration_valid():
    assert _compute_duration(
        "2026-05-17T10:00:00+00:00",
        "2026-05-17T10:05:30+00:00",
    ) == 330


def test_compute_duration_missing():
    assert _compute_duration("", "") is None
    assert _compute_duration("2026-05-17T10:00:00+00:00", "") is None


def test_compute_duration_invalid():
    assert _compute_duration("not-a-date", "also-not") is None
