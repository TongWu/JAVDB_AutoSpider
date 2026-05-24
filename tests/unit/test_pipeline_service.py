from __future__ import annotations

from types import SimpleNamespace

import pytest

from javdb.pipeline.models import StepResult
from javdb.pipeline.result_io import read_pipeline_result
from javdb.pipeline import service as pipeline_service
from javdb.spider.app.result import (
    SpiderRunResult,
    SpiderRunStats,
    write_spider_result_atomic,
)


class FakeStepRunner:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}

    def run(self, policy, command, *, result_path=None):
        self.calls.append((policy, tuple(command), result_path))
        if policy.name == "spider" and result_path:
            write_spider_result_atomic(
                result_path,
                SpiderRunResult(
                    csv_path="reports/DailyReport/2026/03/Javdb_Test.csv",
                    session_id="273",
                    dedup_csv_path=None,
                    stats=SpiderRunStats(
                        pages="1-10",
                        found=10,
                        parsed=8,
                        skipped=1,
                        failed=0,
                        no_new=1,
                    ),
                    mode="daily",
                    url=None,
                    phase="all",
                    page_range="1-10",
                    started_at="2026-05-20T01:00:00Z",
                    finished_at="2026-05-20T01:02:00Z",
                    exit_code=0,
                    failure_reason=None,
                ),
            )
        status, exit_code, failure_reason = self.outcomes.get(
            policy.name,
            ("success", 0, None),
        )
        return StepResult(
            name=policy.name,
            status=status,
            required=policy.required,
            run_on_failure=policy.run_on_failure,
            command=list(command),
            started_at="2026-05-20T01:00:00Z",
            finished_at="2026-05-20T01:01:00Z",
            exit_code=exit_code,
            failure_reason=failure_reason,
            result_path=result_path,
        )


class SpiderFailureStepRunner(FakeStepRunner):
    def run(self, policy, command, *, result_path=None):
        if policy.name == "spider" and result_path:
            self.calls.append((policy, tuple(command), result_path))
            write_spider_result_atomic(
                result_path,
                SpiderRunResult(
                    csv_path=None,
                    session_id="273",
                    dedup_csv_path=None,
                    stats=None,
                    mode="daily",
                    url=None,
                    phase="all",
                    page_range="1-10",
                    started_at="2026-05-20T01:00:00Z",
                    finished_at="2026-05-20T01:02:00Z",
                    exit_code=2,
                    failure_reason="proxy ban detected",
                ),
            )
            return StepResult(
                name=policy.name,
                status="failed",
                required=policy.required,
                run_on_failure=policy.run_on_failure,
                command=list(command),
                started_at="2026-05-20T01:00:00Z",
                finished_at="2026-05-20T01:01:00Z",
                exit_code=2,
                failure_reason="proxy ban detected",
                result_path=result_path,
            )
        return super().run(policy, command, result_path=result_path)


class DedupExceptionStepRunner(FakeStepRunner):
    def run(self, policy, command, *, result_path=None):
        if policy.name == "rclone_dedup":
            self.calls.append((policy, tuple(command), result_path))
            raise RuntimeError("failed to launch rclone")
        return super().run(policy, command, result_path=result_path)


def _make_args(**overrides):
    defaults = {
        'url': None,
        'start_page': None,
        'end_page': None,
        'all': False,
        'ignore_history': False,
        'phase': None,
        'output_file': 'Javdb_Test.csv',
        'dry_run': False,
        'ignore_release_date': False,
        'use_proxy': False,
        'no_proxy': False,
        'always_bypass_time': None,
        'pikpak_individual': False,
        'enable_dedup': False,
        'no_redownload': False,
        'redownload_threshold': None,
        'result_json': None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_runner(monkeypatch, runner):
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args())
    monkeypatch.setattr(pipeline_service, 'check_rust_core_status', lambda: None)
    monkeypatch.setattr(pipeline_service, 'SubprocessStepRunner', lambda: runner, raising=False)
    monkeypatch.setattr(
        pipeline_service,
        'run_command',
        lambda *args, **kwargs: pytest.fail("run_command() should not be used by pipeline core"),
    )


def _command_for(runner, module_name):
    return next(call for call in runner.calls if module_name in call[1])


def test_pipeline_main_uses_auto_proxy_by_default(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    spider_call = _command_for(runner, 'apps.cli.spider')
    uploader_call = _command_for(runner, 'apps.cli.qb.uploader')
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')

    assert '--use-proxy' not in spider_call[1]
    assert '--no-proxy' not in spider_call[1]
    assert '--use-proxy' not in uploader_call[1]
    assert '--no-proxy' not in uploader_call[1]
    assert '--use-proxy' not in pikpak_call[1]
    assert '--no-proxy' not in pikpak_call[1]
    assert '--enable-redownload' in spider_call[1]
    assert '--result-json' in spider_call[1]
    assert spider_call[2] == spider_call[1][spider_call[1].index('--result-json') + 1]
    assert '--session-id' in uploader_call[1]
    assert uploader_call[1][uploader_call[1].index('--session-id') + 1] == '273'
    assert '--session-id' in pikpak_call[1]
    assert pikpak_call[1][pikpak_call[1].index('--session-id') + 1] == '273'


def test_pipeline_main_force_enables_proxy_for_all_steps(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(use_proxy=True))

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    spider_call = _command_for(runner, 'apps.cli.spider')
    uploader_call = _command_for(runner, 'apps.cli.qb.uploader')
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')

    assert '--use-proxy' in spider_call[1]
    assert '--use-proxy' in uploader_call[1]
    assert '--use-proxy' in pikpak_call[1]
    assert '--enable-redownload' in spider_call[1]


def test_pipeline_main_force_disables_proxy_for_all_steps(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(no_proxy=True))

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    spider_call = _command_for(runner, 'apps.cli.spider')
    uploader_call = _command_for(runner, 'apps.cli.qb.uploader')
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')

    assert '--no-proxy' in spider_call[1]
    assert '--no-proxy' in uploader_call[1]
    assert '--no-proxy' in pikpak_call[1]
    assert '--enable-redownload' in spider_call[1]


def test_pipeline_main_can_disable_redownload(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(no_redownload=True))

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0
    spider_call = _command_for(runner, 'apps.cli.spider')
    assert '--enable-redownload' not in spider_call[1]


def test_pipeline_main_writes_success_result_json(monkeypatch, tmp_path):
    runner = FakeStepRunner()
    result_path = tmp_path / "pipeline-result.json"
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(
        pipeline_service,
        'parse_arguments',
        lambda: _make_args(result_json=str(result_path)),
    )

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0
    result = read_pipeline_result(result_path)
    assert result.status == "success"
    assert result.exit_code == 0
    assert result.failure_reason is None
    assert result.spider_result["csv_path"] == "reports/DailyReport/2026/03/Javdb_Test.csv"
    assert [step.name for step in result.steps] == [
        "spider",
        "qb_uploader",
        "pikpak_bridge",
        "email_notification",
    ]
    email_call = _command_for(runner, 'apps.cli.notify.email')
    assert '--csv-path' in email_call[1]
    assert email_call[1][email_call[1].index('--csv-path') + 1] == (
        "reports/DailyReport/2026/03/Javdb_Test.csv"
    )


def test_pipeline_main_writes_failure_result_json_after_required_step_failure(monkeypatch, tmp_path):
    runner = FakeStepRunner(outcomes={"qb_uploader": ("failed", 2, "exit code 2")})
    result_path = tmp_path / "pipeline-result.json"
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(
        pipeline_service,
        'parse_arguments',
        lambda: _make_args(result_json=str(result_path)),
    )

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 1
    result = read_pipeline_result(result_path)
    assert result.status == "failed"
    assert result.exit_code == 1
    assert "qb_uploader" in result.failure_reason
    assert result.spider_result["session_id"] == "273"
    assert [step.name for step in result.steps] == [
        "spider",
        "qb_uploader",
        "email_notification_failure",
    ]
    failure_email = result.steps[-1]
    assert failure_email.run_on_failure is True
    assert failure_email.required is False


def test_pipeline_main_records_optional_dedup_failure_without_failing(monkeypatch, tmp_path):
    runner = FakeStepRunner(outcomes={"rclone_dedup": ("failed", 2, "exit code 2")})
    result_path = tmp_path / "pipeline-result.json"
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(
        pipeline_service,
        'parse_arguments',
        lambda: _make_args(enable_dedup=True, result_json=str(result_path)),
    )

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0
    result = read_pipeline_result(result_path)
    assert result.status == "success"
    assert [step.name for step in result.steps] == [
        "spider",
        "qb_uploader",
        "pikpak_bridge",
        "rclone_dedup",
        "email_notification",
    ]
    dedup_step = next(step for step in result.steps if step.name == "rclone_dedup")
    assert dedup_step.required is False
    assert dedup_step.status == "failed"


def test_pipeline_main_records_optional_dedup_exception_without_failing(monkeypatch, tmp_path):
    runner = DedupExceptionStepRunner()
    result_path = tmp_path / "pipeline-result.json"
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(
        pipeline_service,
        'parse_arguments',
        lambda: _make_args(enable_dedup=True, result_json=str(result_path)),
    )

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0
    result = read_pipeline_result(result_path)
    assert result.status == "success"
    assert [step.name for step in result.steps] == [
        "spider",
        "qb_uploader",
        "pikpak_bridge",
        "rclone_dedup",
        "email_notification",
    ]
    dedup_step = next(step for step in result.steps if step.name == "rclone_dedup")
    assert dedup_step.required is False
    assert dedup_step.status == "failed"
    assert dedup_step.exit_code is None
    assert dedup_step.failure_reason == "failed to launch rclone"


def test_pipeline_main_forwards_dry_run_to_pikpak_and_email(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(dry_run=True))

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')
    email_call = _command_for(runner, 'apps.cli.notify.email')
    assert '--dry-run' in pikpak_call[1]
    assert '--dry-run' in email_call[1]


def test_pipeline_main_preserves_partial_spider_result_on_spider_failure(monkeypatch, tmp_path):
    runner = SpiderFailureStepRunner()
    result_path = tmp_path / "pipeline-result.json"
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(
        pipeline_service,
        'parse_arguments',
        lambda: _make_args(result_json=str(result_path)),
    )

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 1
    result = read_pipeline_result(result_path)
    assert result.status == "failed"
    assert result.spider_result["session_id"] == "273"
    assert result.spider_result["failure_reason"] == "proxy ban detected"
    assert [step.name for step in result.steps] == [
        "spider",
        "email_notification_failure",
    ]
