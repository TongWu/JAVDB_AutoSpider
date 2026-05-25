from __future__ import annotations

from types import SimpleNamespace

from javdb.pipeline.models import StepPolicy
from javdb.pipeline.step_runner import InProcessSpiderStepRunner
from javdb.spider.app.result import SpiderRunResult


def test_in_process_spider_step_runner_returns_success_and_spider_result():
    calls = []

    def run_spider(options):
        calls.append(options)
        return SpiderRunResult(
            csv_path=None,
            session_id="session-1",
            dedup_csv_path=None,
            stats=None,
            mode="daily",
            url=None,
            phase="all",
            page_range=None,
            started_at="2026-05-20T01:00:00Z",
            finished_at="2026-05-20T01:01:00Z",
            exit_code=0,
            failure_reason=None,
        )

    runner = InProcessSpiderStepRunner(run_spider=run_spider)
    policy = StepPolicy(name="spider", required=True, timeout_sec=10)
    options = SimpleNamespace(result_json="/tmp/spider-result.json")

    result, spider_result = runner.run(policy, options=options, command_label=("python", "-m", "spider"))

    assert result.status == "success"
    assert result.exit_code == 0
    assert result.command == ["python", "-m", "spider"]
    assert result.result_path == "/tmp/spider-result.json"
    assert spider_result is not None
    assert spider_result.exit_code == 0
    assert spider_result.failure_reason is None
    assert spider_result.csv_path is None
    assert spider_result.session_id == "session-1"
    assert spider_result.mode == "daily"
    assert spider_result.phase == "all"
    assert calls == [options]


def test_in_process_spider_step_runner_maps_runtime_error_to_failure():
    def run_spider(options):
        raise RuntimeError("boom")

    runner = InProcessSpiderStepRunner(run_spider=run_spider)
    policy = StepPolicy(name="spider", required=True, timeout_sec=10)
    options = SimpleNamespace(result_json="/tmp/spider-result.json")

    result, spider_result = runner.run(policy, options=options, command_label=("python", "-m", "spider"))

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.failure_reason == "boom"
    assert result.command == ["python", "-m", "spider"]
    assert result.result_path == "/tmp/spider-result.json"
    assert spider_result is None


def test_in_process_spider_step_runner_maps_system_exit_to_failed_step():
    def run_spider(options):
        raise SystemExit(2)

    runner = InProcessSpiderStepRunner(run_spider=run_spider)
    policy = StepPolicy(name="spider", required=True, timeout_sec=10)
    options = SimpleNamespace(result_json="/tmp/spider-result.json")

    result, spider_result = runner.run(policy, options=options, command_label=("python", "-m", "spider"))

    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.failure_reason == "exit code 2"
    assert result.result_path == "/tmp/spider-result.json"
    assert spider_result is None


def test_in_process_spider_step_runner_uses_exit_code_fallback_for_failed_result():
    def run_spider(options):
        return SpiderRunResult(
            csv_path=None,
            session_id=None,
            dedup_csv_path=None,
            stats=None,
            mode="daily",
            url=None,
            phase="all",
            page_range=None,
            started_at="2026-05-20T01:00:00Z",
            finished_at="2026-05-20T01:01:00Z",
            exit_code=3,
            failure_reason=None,
        )

    runner = InProcessSpiderStepRunner(run_spider=run_spider)
    policy = StepPolicy(name="spider", required=True, timeout_sec=10)
    options = SimpleNamespace(result_json=None)

    result, spider_result = runner.run(policy, options=options, command_label=("in-process", "spider"))

    assert result.status == "failed"
    assert result.exit_code == 3
    assert result.failure_reason == "exit code 3"
    assert spider_result is not None


def test_in_process_spider_step_runner_maps_system_exit_zero_to_success():
    def run_spider(options):
        raise SystemExit(0)

    runner = InProcessSpiderStepRunner(run_spider=run_spider)
    policy = StepPolicy(name="spider", required=True, timeout_sec=10)
    options = SimpleNamespace(result_json=None)

    result, spider_result = runner.run(policy, options=options, command_label=("in-process", "spider"))

    assert result.status == "success"
    assert result.exit_code == 0
    assert result.failure_reason is None
    assert spider_result is None
