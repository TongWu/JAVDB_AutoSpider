import os
import sys

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
