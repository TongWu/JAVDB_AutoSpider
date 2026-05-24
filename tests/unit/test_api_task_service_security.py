import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.services import context, task_service  # noqa: E402


def test_validate_task_command_allows_known_pipeline_command():
    command = [
        "python3",
        "-u",
        "-m",
        "apps.cli.pipeline",
        "--start-page",
        "1",
        "--end-page",
        "2",
        "--phase",
        "all",
    ]

    assert task_service._validate_task_command(command) == command


def test_validate_task_command_rejects_unapproved_flag():
    with pytest.raises(HTTPException, match="Invalid task command"):
        task_service._validate_task_command(
            [
                "python3",
                "-u",
                "-m",
                "apps.cli.pipeline",
                "--shell",
                "sh",
            ]
        )


def test_validate_task_command_rejects_non_allowlisted_url():
    with pytest.raises(HTTPException):
        task_service._validate_task_command(
            [
                "python3",
                "-u",
                "-m",
                "apps.cli.pipeline",
                "--url",
                "https://evil.example/path",
            ]
        )


def test_resolved_path_under_job_log_dir_stays_inside_job_log_dir():
    path = task_service._resolved_path_under_job_log_dir(
        "daily-20260325-010203-abcd",
        ".log",
    )

    assert path.parent == context.RESOLVED_JOB_LOG_DIR
    assert path.name == "daily-20260325-010203-abcd.log"


def test_validate_task_command_rejects_result_json_traversal():
    with pytest.raises(HTTPException, match="Invalid task command"):
        task_service._validate_task_command(
            [
                "python3",
                "-u",
                "-m",
                "apps.cli.pipeline",
                "--result-json",
                "../daily-20260325-010203-abcd.result.json",
            ]
        )


def test_validate_task_command_rejects_result_json_outside_job_log_dir():
    outside_path = str(Path("/tmp/daily-20260325-010203-abcd.result.json"))

    with pytest.raises(HTTPException, match="Invalid task command"):
        task_service._validate_task_command(
            [
                "python3",
                "-u",
                "-m",
                "apps.cli.pipeline",
                "--result-json",
                outside_path,
            ]
        )


def test_validate_task_command_rejects_result_json_wrong_suffix():
    with pytest.raises(HTTPException, match="Invalid task command"):
        task_service._validate_task_command(
            [
                "python3",
                "-u",
                "-m",
                "apps.cli.pipeline",
                "--result-json",
                "logs/jobs/daily-20260325-010203-abcd.json",
            ]
        )


def test_validate_task_command_rejects_duplicate_result_json_flags():
    first_path = context.RESOLVED_JOB_LOG_DIR / "first.result.json"
    second_path = context.RESOLVED_JOB_LOG_DIR / "second.result.json"

    with pytest.raises(HTTPException, match="Invalid task command"):
        task_service._validate_task_command(
            [
                "python3",
                "-u",
                "-m",
                "apps.cli.pipeline",
                "--result-json",
                str(first_path),
                "--result-json",
                str(second_path),
            ]
        )


def test_spawn_job_adds_pipeline_result_path_to_metadata(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append((list(command), kwargs))
        return FakeProcess()

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(task_service.context, "JOB_LOG_DIR", jobs_dir)
    monkeypatch.setattr(task_service.context, "RESOLVED_JOB_LOG_DIR", jobs_dir.resolve())
    monkeypatch.setattr(task_service.subprocess, "Popen", fake_popen)

    job = task_service._spawn_job(
        "daily",
        ["python3", "-u", "-m", "apps.cli.pipeline", "--dry-run"],
    )
    try:
        result_path = task_service.JOBS[job["job_id"]]["result_path"]
        meta = task_service._read_job_meta(job["job_id"])

        assert result_path == str(
            jobs_dir.resolve() / f"{job['job_id']}.result.json"
        )
        assert task_service.JOBS[job["job_id"]]["command"][-2:] == [
            "--result-json",
            result_path,
        ]
        assert meta["result_path"] == result_path
        assert meta["command"][-2:] == ["--result-json", result_path]
        assert popen_calls[0][0][-2:] == ["--result-json", result_path]
        assert (jobs_dir / f"{job['job_id']}.log").exists()
        assert (jobs_dir / f"{job['job_id']}.meta.json").exists()
    finally:
        task_service.JOBS.pop(job["job_id"], None)


def test_spawn_job_records_explicit_relative_result_path_from_repo_root(
    monkeypatch,
    tmp_path,
):
    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append((list(command), kwargs))
        return FakeProcess()

    repo_root = tmp_path / "repo"
    jobs_dir = repo_root / "logs" / "jobs"
    jobs_dir.mkdir(parents=True)
    monkeypatch.setattr(task_service.context, "REPO_ROOT", repo_root)
    monkeypatch.setattr(task_service.context, "JOB_LOG_DIR", jobs_dir)
    monkeypatch.setattr(task_service.context, "RESOLVED_JOB_LOG_DIR", jobs_dir.resolve())
    monkeypatch.setattr(task_service.subprocess, "Popen", fake_popen)

    explicit_result = "logs/jobs/explicit.result.json"
    job = task_service._spawn_job(
        "daily",
        [
            "python3",
            "-u",
            "-m",
            "apps.cli.pipeline",
            "--dry-run",
            "--result-json",
            explicit_result,
        ],
    )
    try:
        expected_result_path = str((repo_root / explicit_result).resolve())
        meta = task_service._read_job_meta(job["job_id"])

        assert task_service.JOBS[job["job_id"]]["result_path"] == expected_result_path
        assert meta["result_path"] == expected_result_path
        assert popen_calls[0][0][-2:] == ["--result-json", explicit_result]
        assert popen_calls[0][1]["cwd"] == repo_root
    finally:
        task_service.JOBS.pop(job["job_id"], None)
