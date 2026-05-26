"""Unit tests for task_service metadata helpers: _extract_params_from_command,
_compute_duration, and the stream response shape."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from apps.api.services import context
from apps.api.services import task_service
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


def test_pipeline_command_accepts_result_json_flag():
    command = [
        "python3", "-u", "-m", "apps.cli.pipeline",
        "--result-json", "logs/jobs/daily-20260520-010203-abcd.result.json",
    ]

    assert task_service._validate_task_command(command) == command


def test_extract_params_includes_result_json_path():
    command = [
        "python3", "-u", "-m", "apps.cli.pipeline",
        "--result-json", "logs/jobs/daily-20260520-010203-abcd.result.json",
    ]

    params = task_service._extract_params_from_command(command)

    assert params["result_json"] == "logs/jobs/daily-20260520-010203-abcd.result.json"


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


def test_job_payload_includes_result_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "RESOLVED_JOB_LOG_DIR", tmp_path)
    monkeypatch.setattr(context, "JOB_LOG_DIR", tmp_path)
    job_id = "daily-20260520-010203-abcd"
    result_path = tmp_path / f"{job_id}.result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "pipeline_run_result",
                "generated_at": "2026-05-20T01:00:00Z",
                "status": "success",
                "mode": "daily",
                "url": None,
                "started_at": "2026-05-20T01:00:00Z",
                "finished_at": "2026-05-20T01:02:00Z",
                "exit_code": 0,
                "failure_reason": None,
                "spider_result": None,
                "steps": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{job_id}.log").write_text("done\n", encoding="utf-8")
    (tmp_path / f"{job_id}.meta.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "kind": "daily",
                "mode": "pipeline",
                "url": "",
                "status": "success",
                "command": ["python3", "-u", "-m", "apps.cli.pipeline"],
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    payload = task_service.get_task_payload(job_id, "tester")

    assert payload["result_path"] == str(result_path)
    assert payload["result_summary"] == {
        "kind": "pipeline_run_result",
        "schema_version": "1.0",
        "status": "success",
        "exit_code": 0,
        "failure_reason": None,
    }


def test_job_payload_ignores_invalid_result_json(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "RESOLVED_JOB_LOG_DIR", tmp_path)
    monkeypatch.setattr(context, "JOB_LOG_DIR", tmp_path)
    job_id = "daily-20260520-010203-abcd"
    result_path = tmp_path / f"{job_id}.result.json"
    result_path.write_text("{not valid json", encoding="utf-8")
    (tmp_path / f"{job_id}.log").write_text("done\n", encoding="utf-8")
    (tmp_path / f"{job_id}.meta.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "kind": "daily",
                "mode": "pipeline",
                "url": "",
                "status": "success",
                "command": ["python3", "-u", "-m", "apps.cli.pipeline"],
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    payload = task_service.get_task_payload(job_id, "tester")

    assert payload["result_path"] == str(result_path)
    assert payload["result_summary"] is None


def test_job_payload_ignores_non_utf8_result_json(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "RESOLVED_JOB_LOG_DIR", tmp_path)
    monkeypatch.setattr(context, "JOB_LOG_DIR", tmp_path)
    job_id = "daily-20260520-010203-abcd"
    result_path = tmp_path / f"{job_id}.result.json"
    result_path.write_bytes(b"\xff\xfe\xfa")
    (tmp_path / f"{job_id}.log").write_text("done\n", encoding="utf-8")
    (tmp_path / f"{job_id}.meta.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "kind": "daily",
                "mode": "pipeline",
                "url": "",
                "status": "success",
                "command": ["python3", "-u", "-m", "apps.cli.pipeline"],
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    payload = task_service.get_task_payload(job_id, "tester")

    assert payload["result_path"] == str(result_path)
    assert payload["result_summary"] is None


def test_job_payload_does_not_summarize_result_outside_job_log_dir(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(context, "RESOLVED_JOB_LOG_DIR", tmp_path / "jobs")
    monkeypatch.setattr(context, "JOB_LOG_DIR", tmp_path / "jobs")
    context.RESOLVED_JOB_LOG_DIR.mkdir()
    job_id = "daily-20260520-010203-abcd"
    result_path = tmp_path / f"{job_id}.result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "pipeline_run_result",
                "status": "success",
                "exit_code": 0,
                "failure_reason": None,
            }
        ),
        encoding="utf-8",
    )
    (context.RESOLVED_JOB_LOG_DIR / f"{job_id}.log").write_text(
        "done\n",
        encoding="utf-8",
    )
    (context.RESOLVED_JOB_LOG_DIR / f"{job_id}.meta.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "kind": "daily",
                "mode": "pipeline",
                "url": "",
                "status": "success",
                "command": ["python3", "-u", "-m", "apps.cli.pipeline"],
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    payload = task_service.get_task_payload(job_id, "tester")

    assert payload["result_path"] == str(result_path)
    assert payload["result_summary"] is None
