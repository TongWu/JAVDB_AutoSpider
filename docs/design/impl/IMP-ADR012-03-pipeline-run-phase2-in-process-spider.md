# IMP-ADR012-03: ADR-012 Phase 2 - In-Process Spider Runner

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Pipeline Spider subprocess with an in-process Spider runner after ADR-012 Phase 1 bake passes, while preserving real-time logs and all CLI compatibility output.

**Architecture:** Phase 1 already introduced `SpiderRunResult`, `PipelineRunResult`, `StepResult`, and `LogSink`. Phase 2 adds an in-process runner for the Spider step only. qB uploader, PikPak, email, and rclone dedup remain subprocess steps.

**Tech Stack:** Python 3.11, dataclasses, argparse-to-options mapping, logging, pytest.

**Prerequisite:** [IMP-ADR012-02](IMP-ADR012-02-pipeline-run-phase1-bake.md) Phase 2 Unlock Gate is complete.

**Source spec:** [ADR-012](../adr/ADR-012-pipeline-run-structured-boundary.md), D12.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/spider/app/options.py` | New `SpiderRunOptions` dataclass and argparse conversion helpers. |
| `javdb/spider/app/run_service.py` | Expose `run_spider(options, result_json=None) -> SpiderRunResult`; keep CLI `main()` wrapper. |
| `javdb/pipeline/step_runner.py` | Add `InProcessSpiderStepRunner`. |
| `javdb/pipeline/service.py` | Use `InProcessSpiderStepRunner` for the Spider step after bake. |
| `tests/unit/test_spider_run_options.py` | Options conversion and validation tests. |
| `tests/unit/test_in_process_spider_step_runner.py` | Runner result, exception mapping, and log sink tests. |
| `tests/unit/test_pipeline_service.py` | Prove Pipeline uses in-process Spider and still subprocesses other steps. |

---

## Task 1: Add SpiderRunOptions

**Files:**
- Create: `javdb/spider/app/options.py`
- Create: `tests/unit/test_spider_run_options.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_spider_run_options.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from javdb.spider.app.options import SpiderRunOptions, spider_options_from_args


def test_spider_options_from_args_daily_defaults():
    args = SimpleNamespace(
        url=None,
        start_page=1,
        end_page=10,
        all=False,
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
        result_json=None,
    )

    options = spider_options_from_args(args)

    assert isinstance(options, SpiderRunOptions)
    assert options.mode == "daily"
    assert options.start_page == 1
    assert options.end_page == 10
    assert options.phase == "all"


def test_spider_options_from_args_adhoc_url():
    args = SimpleNamespace(
        url="https://javdb.com/actors/EvkJ",
        start_page=1,
        end_page=1,
        all=False,
        ignore_history=False,
        phase="1",
        output_file=None,
        dry_run=True,
        ignore_release_date=True,
        use_proxy=True,
        no_proxy=False,
        always_bypass_time=0,
        enable_dedup=True,
        enable_redownload=False,
        redownload_threshold=None,
        result_json="/tmp/result.json",
    )

    options = spider_options_from_args(args)

    assert options.mode == "adhoc"
    assert options.url == "https://javdb.com/actors/EvkJ"
    assert options.result_json == "/tmp/result.json"
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/unit/test_spider_run_options.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.spider.app.options'`.

- [ ] **Step 3: Implement `javdb/spider/app/options.py`**

Create:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SpiderRunOptions:
    mode: Literal["daily", "adhoc"]
    url: str | None
    start_page: int | None
    end_page: int | None
    parse_all: bool
    ignore_history: bool
    phase: Literal["1", "2", "all"]
    output_file: str | None
    dry_run: bool
    ignore_release_date: bool
    use_proxy: bool
    no_proxy: bool
    always_bypass_time: int | None
    enable_dedup: bool
    enable_redownload: bool
    redownload_threshold: float | None
    result_json: str | None


def spider_options_from_args(args) -> SpiderRunOptions:
    return SpiderRunOptions(
        mode="adhoc" if getattr(args, "url", None) else "daily",
        url=getattr(args, "url", None),
        start_page=getattr(args, "start_page", None),
        end_page=getattr(args, "end_page", None),
        parse_all=bool(getattr(args, "all", False)),
        ignore_history=bool(getattr(args, "ignore_history", False)),
        phase=str(getattr(args, "phase", None) or "all"),
        output_file=getattr(args, "output_file", None),
        dry_run=bool(getattr(args, "dry_run", False)),
        ignore_release_date=bool(getattr(args, "ignore_release_date", False)),
        use_proxy=bool(getattr(args, "use_proxy", False)),
        no_proxy=bool(getattr(args, "no_proxy", False)),
        always_bypass_time=getattr(args, "always_bypass_time", None),
        enable_dedup=bool(getattr(args, "enable_dedup", False)),
        enable_redownload=bool(getattr(args, "enable_redownload", False)),
        redownload_threshold=getattr(args, "redownload_threshold", None),
        result_json=getattr(args, "result_json", None),
    )
```

- [ ] **Step 4: Run options tests**

```bash
pytest tests/unit/test_spider_run_options.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/app/options.py tests/unit/test_spider_run_options.py
git commit -m "feat(spider): add run options model"
```

## Task 2: Expose In-Process Spider Service

**Files:**
- Modify: `javdb/spider/app/run_service.py`
- Modify: `javdb/spider/app/main.py`
- Modify: `tests/unit/test_spider_run_result.py`

- [ ] **Step 1: Add tests for service-level result return**

Append to `tests/unit/test_spider_run_result.py`:

```python
from javdb.spider.app.options import SpiderRunOptions
from javdb.spider.app import run_service


def test_run_spider_returns_result_from_service_impl(monkeypatch, tmp_path):
    expected = SpiderRunResult(
        csv_path="reports/DailyReport/x.csv",
        session_id="s1",
        dedup_csv_path=None,
        stats=SpiderRunStats(
            pages="1-1",
            found=1,
            parsed=1,
            skipped=0,
            failed=0,
            no_new=0,
        ),
        mode="daily",
        url=None,
        phase="all",
        page_range="1-1",
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:01:00Z",
        exit_code=0,
        failure_reason=None,
    )
    options = SpiderRunOptions(
        mode="daily",
        url=None,
        start_page=1,
        end_page=1,
        parse_all=False,
        ignore_history=False,
        phase="all",
        output_file="Javdb_Test.csv",
        dry_run=True,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        enable_dedup=False,
        enable_redownload=False,
        redownload_threshold=None,
        result_json=str(tmp_path / "spider-result.json"),
    )

    monkeypatch.setattr(run_service, "_run_spider_impl", lambda received: expected)

    result = run_service.run_spider(options)

    assert result is expected
    assert read_spider_result(options.result_json).session_id == "s1"
```

- [ ] **Step 2: Split CLI wrapper from service**

In `javdb/spider/app/run_service.py`, preserve `main()` but route it through:

```python
def run_spider(options: SpiderRunOptions) -> SpiderRunResult:
    result = _run_spider_impl(options)
    if options.result_json:
        write_spider_result_atomic(options.result_json, result)
    return result


def _run_spider_impl(options: SpiderRunOptions) -> SpiderRunResult:
    """Execute the Spider run using the body moved from the current main()."""
    return _run_spider_main_body(options)
```

Create `_run_spider_main_body(options)` in the same file by moving the current
post-argument-parse body of `main()` into that helper and replacing every direct
`args.<field>` read with `options.<field>`. Keep existing `try/finally` cleanup
inside the helper.

`main()` remains responsible for:

- parsing CLI args;
- converting args to `SpiderRunOptions`;
- printing compatibility `SPIDER_*` lines through existing report code;
- mapping `SpiderRunResult.exit_code` to `SystemExit`.

`run_spider()` is responsible for:

- executing Spider business logic;
- returning `SpiderRunResult`;
- writing `options.result_json` when present;
- cleaning active session/run identity in `finally`;
- preserving DB connection cleanup behavior.

- [ ] **Step 3: Preserve compatibility stdout**

Run smoke tests that assert the CLI still emits compatibility output:

```bash
pytest tests/smoke/test_spider.py tests/smoke/test_spider_app_main.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add javdb/spider/app/run_service.py javdb/spider/app/main.py tests/unit/test_spider_run_result.py tests/smoke/test_spider.py tests/smoke/test_spider_app_main.py
git commit -m "refactor(spider): expose in-process run service"
```

## Task 3: Add InProcessSpiderStepRunner

**Files:**
- Modify: `javdb/pipeline/step_runner.py`
- Create: `tests/unit/test_in_process_spider_step_runner.py`

- [ ] **Step 1: Write runner tests**

Create `tests/unit/test_in_process_spider_step_runner.py`:

```python
from __future__ import annotations

from javdb.pipeline.models import StepPolicy
from javdb.pipeline.step_runner import InProcessSpiderStepRunner
from javdb.spider.app.result import SpiderRunResult, SpiderRunStats


def _result():
    return SpiderRunResult(
        csv_path="reports/DailyReport/x.csv",
        session_id="s1",
        dedup_csv_path=None,
        stats=SpiderRunStats(pages="1-1", found=1, parsed=1, skipped=0, failed=0, no_new=0),
        mode="daily",
        url=None,
        phase="all",
        page_range="1-1",
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:01:00Z",
        exit_code=0,
        failure_reason=None,
    )


def test_in_process_spider_runner_returns_success_step():
    calls = []

    def fake_run(options):
        calls.append(options)
        return _result()

    runner = InProcessSpiderStepRunner(run_spider=fake_run)
    policy = StepPolicy(name="spider", required=True, timeout_sec=3600)

    step, spider_result = runner.run(policy, options=object(), command_label=["in-process", "spider"])

    assert step.status == "success"
    assert step.exit_code == 0
    assert spider_result.session_id == "s1"
    assert len(calls) == 1


def test_in_process_spider_runner_maps_exception_to_failed_step():
    def fake_run(options):
        raise RuntimeError("boom")

    runner = InProcessSpiderStepRunner(run_spider=fake_run)
    policy = StepPolicy(name="spider", required=True, timeout_sec=3600)

    step, spider_result = runner.run(policy, options=object(), command_label=["in-process", "spider"])

    assert step.status == "failed"
    assert step.exit_code == 1
    assert "boom" in (step.failure_reason or "")
    assert spider_result is None
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/unit/test_in_process_spider_step_runner.py -v
```

Expected: FAIL until `InProcessSpiderStepRunner` exists.

- [ ] **Step 3: Implement runner**

Add to `javdb/pipeline/step_runner.py`:

```python
class InProcessSpiderStepRunner:
    def __init__(self, *, run_spider):
        self._run_spider = run_spider

    def run(self, policy: StepPolicy, *, options, command_label: Sequence[str]) -> tuple[StepResult, object | None]:
        started_at = _utc_now_iso()
        try:
            spider_result = self._run_spider(options)
            status = "success" if spider_result.exit_code == 0 else "failed"
            return (
                StepResult(
                    name=policy.name,
                    status=status,
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=list(command_label),
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=spider_result.exit_code,
                    failure_reason=spider_result.failure_reason,
                    result_path=getattr(options, "result_json", None),
                ),
                spider_result,
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return (
                StepResult(
                    name=policy.name,
                    status="success" if code == 0 else "failed",
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=list(command_label),
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=code,
                    failure_reason=None if code == 0 else f"exit code {code}",
                    result_path=getattr(options, "result_json", None),
                ),
                None,
            )
        except Exception as exc:
            return (
                StepResult(
                    name=policy.name,
                    status="failed",
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=list(command_label),
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=1,
                    failure_reason=str(exc),
                    result_path=getattr(options, "result_json", None),
                ),
                None,
            )
```

- [ ] **Step 4: Run runner tests**

```bash
pytest tests/unit/test_in_process_spider_step_runner.py tests/unit/test_pipeline_step_runner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/pipeline/step_runner.py tests/unit/test_in_process_spider_step_runner.py
git commit -m "feat(pipeline): add in-process spider step runner"
```

## Task 4: Use In-Process Spider In Pipeline

**Files:**
- Modify: `javdb/pipeline/service.py`
- Modify: `tests/unit/test_pipeline_service.py`

- [ ] **Step 1: Update Pipeline service tests**

Add assertions that the Spider command is no longer run through
`SubprocessStepRunner`, while qB uploader, PikPak, email, and rclone dedup still
are.

- [ ] **Step 2: Build `SpiderRunOptions` from Pipeline args**

In `javdb/pipeline/service.py`, create a `SpiderRunOptions` instead of building
only CLI args for the Spider step. Keep a `command_label` list for `StepResult`
auditability:

```python
command_label = ["in-process", "javdb.spider.app.run_service.run_spider"]
```

- [ ] **Step 3: Execute Spider through `InProcessSpiderStepRunner`**

Use:

```python
spider_step, spider_result = InProcessSpiderStepRunner(
    run_spider=run_spider,
).run(
    StepPolicy(name="spider", required=True, timeout_sec=3600),
    options=spider_options,
    command_label=command_label,
)
```

Use `spider_result.csv_path` and `spider_result.session_id` directly. Do not
read Spider stdout or require the Spider result JSON for in-process operation.
Keep result JSON writing enabled through `spider_options.result_json`.

- [ ] **Step 4: Run focused tests**

```bash
pytest tests/unit/test_pipeline_service.py tests/unit/test_in_process_spider_step_runner.py tests/unit/test_spider_run_options.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/pipeline/service.py tests/unit/test_pipeline_service.py
git commit -m "refactor(pipeline): run spider in process"
```

## Phase 2 Completion Gate

- [ ] [IMP-ADR012-02](IMP-ADR012-02-pipeline-run-phase1-bake.md) Phase 2 Unlock Gate is complete.
- [ ] Pipeline no longer subprocesses the Spider step.
- [ ] Pipeline still subprocesses qB uploader, PikPak, email, and rclone dedup.
- [ ] GitHub Actions logs still stream in real time.
- [ ] Frontend task stream still grows in real time.
- [ ] `SPIDER_*` CLI compatibility remains.
- [ ] `python -m apps.cli.pipeline --result-json <path>` writes `PipelineRunResult`.
- [ ] Focused Pipeline/Spider tests pass.
