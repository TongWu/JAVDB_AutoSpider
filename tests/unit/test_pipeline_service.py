from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from javdb.pipeline.models import StepResult
from javdb.pipeline.result_io import read_pipeline_result
from javdb.pipeline import service as pipeline_service
from javdb.spider.app.options import SpiderRunOptions
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


def _successful_spider_result():
    return SpiderRunResult(
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
    )


def _partial_failed_spider_result():
    return SpiderRunResult(
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
    )


class FakeInProcessSpiderStepRunner:
    instances = []

    def __init__(self, *, run_spider):
        self.run_spider = run_spider
        self.calls = []
        self.result = _successful_spider_result()
        self.step_status = "success"
        FakeInProcessSpiderStepRunner.instances.append(self)

    def run(self, policy, *, options, command_label):
        self.calls.append((policy, options, tuple(command_label)))
        return (
            StepResult(
                name=policy.name,
                status=self.step_status,
                required=policy.required,
                run_on_failure=policy.run_on_failure,
                command=list(command_label),
                started_at="2026-05-20T01:00:00Z",
                finished_at="2026-05-20T01:01:00Z",
                exit_code=self.result.exit_code,
                failure_reason=self.result.failure_reason,
                result_path=options.result_json,
            ),
            self.result,
        )


class SpiderFailureSidecarStepRunner(FakeInProcessSpiderStepRunner):
    def __init__(self, *, run_spider=None):
        super().__init__(run_spider=run_spider)
        self.result = _partial_failed_spider_result()
        self.step_status = "failed"

    def run(self, policy, *, options, command_label):
        self.calls.append((policy, options, tuple(command_label)))
        write_spider_result_atomic(options.result_json, self.result)
        return (
            StepResult(
                name=policy.name,
                status="failed",
                required=policy.required,
                run_on_failure=policy.run_on_failure,
                command=list(command_label),
                started_at="2026-05-20T01:00:00Z",
                finished_at="2026-05-20T01:01:00Z",
                exit_code=self.result.exit_code,
                failure_reason=self.result.failure_reason,
                result_path=options.result_json,
            ),
            None,
        )


class DedupExceptionStepRunner(FakeStepRunner):
    def run(self, policy, command, *, result_path=None):
        if policy.name == "rclone_dedup":
            self.calls.append((policy, tuple(command), result_path))
            raise RuntimeError("failed to launch rclone")
        return super().run(policy, command, result_path=result_path)


class FailureEmailExceptionStepRunner(FakeStepRunner):
    def run(self, policy, command, *, result_path=None):
        if policy.name == "email_notification_failure":
            self.calls.append((policy, tuple(command), result_path))
            raise RuntimeError("failed to launch failure email")
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
    FakeInProcessSpiderStepRunner.instances.clear()
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args())
    monkeypatch.setattr(pipeline_service, 'check_rust_core_status', lambda: None)
    monkeypatch.setattr(pipeline_service, 'SubprocessStepRunner', lambda: runner, raising=False)
    monkeypatch.setattr(
        pipeline_service,
        'InProcessSpiderStepRunner',
        FakeInProcessSpiderStepRunner,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline_service,
        'run_spider',
        lambda options: pytest.fail("fake spider runner should own execution"),
        raising=False,
    )

def _command_for(runner, module_name):
    return next(call for call in runner.calls if module_name in call[1])


def _spider_runner():
    assert len(FakeInProcessSpiderStepRunner.instances) == 1
    return FakeInProcessSpiderStepRunner.instances[0]


def _spider_options():
    runner = _spider_runner()
    assert len(runner.calls) == 1
    options = runner.calls[0][1]
    assert isinstance(options, SpiderRunOptions)
    return options


def _assert_no_spider_subprocess(runner):
    assert not any("apps.cli.spider" in call[1] for call in runner.calls)


def _assert_spider_options(
    options,
    *,
    url=None,
    start_page=None,
    end_page=None,
    parse_all=False,
    ignore_history=False,
    phase="all",
    output_file="Javdb_Test.csv",
    dry_run=False,
    ignore_release_date=False,
    use_proxy=False,
    no_proxy=False,
    always_bypass_time=None,
    enable_dedup=False,
    enable_redownload=True,
    redownload_threshold=None,
):
    assert options.mode == ("adhoc" if url else "daily")
    assert options.url == url
    assert options.start_page == start_page
    assert options.end_page == end_page
    assert options.parse_all is parse_all
    assert options.ignore_history is ignore_history
    assert options.phase == phase
    assert options.output_file == output_file
    assert options.dry_run is dry_run
    assert options.ignore_release_date is ignore_release_date
    assert options.use_proxy is use_proxy
    assert options.no_proxy is no_proxy
    assert options.always_bypass_time == always_bypass_time
    assert options.enable_dedup is enable_dedup
    assert options.enable_redownload is enable_redownload
    assert options.redownload_threshold == redownload_threshold
    assert options.use_history is False
    assert options.from_pipeline is True
    assert options.max_movies_phase1 is None
    assert options.max_movies_phase2 is None
    assert options.sequential is False
    assert options.no_rclone_filter is False
    assert options.disable_all_filters is False
    assert options.result_json is not None
    assert Path(options.result_json).name == "spider-result.json"
    assert Path(options.result_json).parent.name.startswith("pipeline-result-")


def test_pipeline_main_uses_auto_proxy_by_default(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    uploader_call = _command_for(runner, 'apps.cli.qb.uploader')
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')
    spider_options = _spider_options()

    assert '--use-proxy' not in uploader_call[1]
    assert '--no-proxy' not in uploader_call[1]
    assert '--use-proxy' not in pikpak_call[1]
    assert '--no-proxy' not in pikpak_call[1]
    _assert_no_spider_subprocess(runner)
    _assert_spider_options(
        spider_options,
        output_file='Javdb_Test.csv',
        enable_redownload=True,
    )
    assert _spider_runner().calls[0][2] == (
        'in-process',
        'javdb.spider.app.run_service.run_spider',
    )
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

    uploader_call = _command_for(runner, 'apps.cli.qb.uploader')
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')
    spider_options = _spider_options()

    assert '--use-proxy' in uploader_call[1]
    assert '--use-proxy' in pikpak_call[1]
    _assert_no_spider_subprocess(runner)
    _assert_spider_options(spider_options, use_proxy=True, enable_redownload=True)


def test_pipeline_main_force_disables_proxy_for_all_steps(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(no_proxy=True))

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    uploader_call = _command_for(runner, 'apps.cli.qb.uploader')
    pikpak_call = _command_for(runner, 'apps.cli.pikpak.bridge')
    spider_options = _spider_options()

    assert '--no-proxy' in uploader_call[1]
    assert '--no-proxy' in pikpak_call[1]
    _assert_no_spider_subprocess(runner)
    _assert_spider_options(spider_options, no_proxy=True, enable_redownload=True)


def test_pipeline_main_can_disable_redownload(monkeypatch):
    runner = FakeStepRunner()
    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(no_redownload=True))

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0
    _assert_no_spider_subprocess(runner)
    _assert_spider_options(_spider_options(), enable_redownload=False)


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
    spider_options = _spider_options()
    _assert_spider_options(
        spider_options,
        output_file='Javdb_Test.csv',
        enable_redownload=True,
    )
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
    _assert_spider_options(
        _spider_options(),
        output_file='Javdb_Test.csv',
        enable_redownload=True,
    )


def test_pipeline_main_records_failure_email_exception_without_changing_exit(monkeypatch, tmp_path):
    runner = FailureEmailExceptionStepRunner(
        outcomes={"qb_uploader": ("failed", 2, "exit code 2")},
    )
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
    assert [step.name for step in result.steps] == [
        "spider",
        "qb_uploader",
        "email_notification_failure",
    ]
    failure_email = result.steps[-1]
    assert failure_email.status == "failed"
    assert failure_email.required is False
    assert failure_email.run_on_failure is True
    assert "apps.cli.notify.email" in failure_email.command
    assert "--from-pipeline" in failure_email.command
    assert failure_email.exit_code is None
    assert failure_email.failure_reason == "failed to launch failure email"
    assert failure_email.result_path is None
    assert failure_email.started_at.endswith("Z")
    assert failure_email.finished_at.endswith("Z")


def test_pipeline_main_records_failure_email_policy_exception(monkeypatch, tmp_path):
    runner = FakeStepRunner(outcomes={"qb_uploader": ("failed", 2, "exit code 2")})
    result_path = tmp_path / "pipeline-result.json"
    original_step_policy = pipeline_service.StepPolicy

    def step_policy_with_failure(*args, **kwargs):
        name = kwargs.get("name")
        if name is None and args:
            name = args[0]
        if name == "email_notification_failure":
            raise RuntimeError("failed to build failure email policy")
        return original_step_policy(*args, **kwargs)

    _patch_runner(monkeypatch, runner)
    monkeypatch.setattr(pipeline_service, "StepPolicy", step_policy_with_failure)
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
    assert [step.name for step in result.steps] == [
        "spider",
        "qb_uploader",
        "email_notification_failure",
    ]
    failure_email = result.steps[-1]
    assert failure_email.status == "failed"
    assert failure_email.required is False
    assert failure_email.run_on_failure is True
    assert "apps.cli.notify.email" in failure_email.command
    assert failure_email.exit_code is None
    assert failure_email.failure_reason == "failed to build failure email policy"
    _assert_spider_options(
        _spider_options(),
        output_file='Javdb_Test.csv',
        enable_redownload=True,
    )


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
    _assert_spider_options(
        _spider_options(),
        output_file='Javdb_Test.csv',
        enable_dedup=True,
        enable_redownload=True,
    )


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
    _assert_spider_options(
        _spider_options(),
        output_file='Javdb_Test.csv',
        enable_dedup=True,
        enable_redownload=True,
    )


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
    _assert_spider_options(
        _spider_options(),
        output_file='Javdb_Test.csv',
        dry_run=True,
        enable_redownload=True,
    )


def test_pipeline_main_preserves_partial_spider_result_on_spider_failure(monkeypatch, tmp_path):
    runner = FakeStepRunner()
    result_path = tmp_path / "pipeline-result.json"
    _patch_runner(monkeypatch, runner)
    FakeInProcessSpiderStepRunner.instances.clear()
    monkeypatch.setattr(
        pipeline_service,
        'InProcessSpiderStepRunner',
        SpiderFailureSidecarStepRunner,
        raising=False,
    )
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
    _assert_spider_options(
        _spider_options(),
        output_file='Javdb_Test.csv',
        enable_redownload=True,
    )
