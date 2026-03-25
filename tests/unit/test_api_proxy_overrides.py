import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.schemas.payloads import (  # noqa: E402
    AdhocTaskPayload,
    DailyTaskPayload,
    HealthCheckPayload,
    SpiderJobPayload,
)
from apps.api.services import spider_jobs, system_service, task_service  # noqa: E402


@pytest.mark.parametrize(
    ("payload_cls", "kwargs"),
    [
        (SpiderJobPayload, {}),
        (DailyTaskPayload, {}),
        (AdhocTaskPayload, {"url": "https://javdb.com/v/abcde"}),
        (HealthCheckPayload, {}),
    ],
)
def test_cli_proxy_payloads_reject_conflicting_overrides(payload_cls, kwargs):
    with pytest.raises(ValidationError, match="use_proxy and no_proxy cannot both be true"):
        payload_cls(use_proxy=True, no_proxy=True, **kwargs)


def test_validate_task_command_allows_no_proxy_flag():
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
        "--no-proxy",
    ]

    assert task_service._validate_task_command(command) == command


def test_trigger_daily_task_uses_auto_proxy_by_default(monkeypatch):
    captured = {}

    def fake_spawn(job_prefix, command, metadata=None):
        captured["job_prefix"] = job_prefix
        captured["command"] = command
        captured["metadata"] = metadata or {}
        return {"job_id": "daily-job"}

    monkeypatch.setattr(task_service, "_spawn_job", fake_spawn)

    task_service.trigger_daily_task(DailyTaskPayload(), "tester")

    assert captured["job_prefix"] == "daily"
    assert "--use-proxy" not in captured["command"]
    assert "--no-proxy" not in captured["command"]


def test_trigger_daily_task_supports_force_disable_proxy(monkeypatch):
    captured = {}

    def fake_spawn(job_prefix, command, metadata=None):
        captured["command"] = command
        return {"job_id": "daily-job"}

    monkeypatch.setattr(task_service, "_spawn_job", fake_spawn)

    task_service.trigger_daily_task(DailyTaskPayload(no_proxy=True), "tester")

    assert "--no-proxy" in captured["command"]
    assert "--use-proxy" not in captured["command"]


def test_trigger_adhoc_task_uses_auto_proxy_by_default(monkeypatch):
    captured = {}

    def fake_spawn(job_prefix, command, metadata=None):
        captured["job_prefix"] = job_prefix
        captured["command"] = command
        return {"job_id": "adhoc-job"}

    monkeypatch.setattr(task_service, "_spawn_job", fake_spawn)

    payload = AdhocTaskPayload(url="https://javdb.com/v/abcde")
    task_service.trigger_adhoc_task(payload, "tester")

    assert captured["job_prefix"] == "adhoc"
    assert "--use-proxy" not in captured["command"]
    assert "--no-proxy" not in captured["command"]


def test_trigger_adhoc_task_supports_force_disable_proxy(monkeypatch):
    captured = {}

    def fake_spawn(job_prefix, command, metadata=None):
        captured["command"] = command
        return {"job_id": "adhoc-job"}

    monkeypatch.setattr(task_service, "_spawn_job", fake_spawn)

    payload = AdhocTaskPayload(url="https://javdb.com/v/abcde", no_proxy=True)
    task_service.trigger_adhoc_task(payload, "tester")

    assert "--no-proxy" in captured["command"]
    assert "--use-proxy" not in captured["command"]


def test_spider_job_payload_to_cli_args_supports_no_proxy():
    args = spider_jobs._payload_to_cli_args(SpiderJobPayload(no_proxy=True))
    assert "--no-proxy" in args
    assert "--use-proxy" not in args


def test_run_health_check_payload_supports_no_proxy(monkeypatch):
    captured = {}

    def fake_run(command, cwd, capture_output, text, timeout):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(system_service.subprocess, "run", fake_run)

    payload = HealthCheckPayload(check_smtp=True, no_proxy=True)
    result = asyncio.run(system_service.run_health_check_payload(payload, "tester"))

    assert result["status"] == "ok"
    assert "--check-smtp" in captured["command"]
    assert "--no-proxy" in captured["command"]
    assert "--use-proxy" not in captured["command"]
