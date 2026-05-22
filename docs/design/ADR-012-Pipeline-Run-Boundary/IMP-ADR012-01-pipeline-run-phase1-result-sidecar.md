# IMP-ADR012-01: ADR-012 Phase 1 - Pipeline Result Sidecar

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-012 Phase 1 by introducing versioned Spider/Pipeline result sidecars, explicit Pipeline step results, `SubprocessStepRunner`, and `LogSink` while preserving subprocess execution and real-time logs.

**Architecture:** Pipeline core stops parsing `SPIDER_*` stdout for internal data. Spider runtime writes `SpiderRunResult` to `--result-json`; Pipeline reads that result JSON and writes `PipelineRunResult`. Subprocess execution remains behind an Adapter so GitHub Actions and frontend task logs keep streaming.

**Tech Stack:** Python 3.11, dataclasses, JSON, pathlib, argparse, subprocess, pytest, GitHub Actions-compatible stdout.

**Source spec:** [ADR-012](ADR-012-pipeline-run-structured-boundary.md), D1-D10.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/spider/app/result.py` | New Spider result dataclasses plus atomic JSON read/write helpers. |
| `javdb/pipeline/models.py` | Add Pipeline result and step policy/result dataclasses without breaking existing ingestion models. |
| `javdb/pipeline/result_io.py` | New Pipeline result JSON helpers and Spider result readers. |
| `javdb/pipeline/step_runner.py` | New `LogSink`, `ConsoleAndFileLogSink`, and `SubprocessStepRunner`. |
| `javdb/spider/app/cli.py` | Add `--result-json` argument. |
| `javdb/spider/app/run_service.py` | Generate authoritative `SpiderRunResult`; write complete or partial JSON. |
| `apps/cli/spider.py` | Preserve canonical CLI behavior through existing `main()`. |
| `javdb/pipeline/service.py` | Build step plan, call `SubprocessStepRunner`, read Spider result JSON, write Pipeline result JSON. |
| `apps/cli/pipeline.py` | Preserve canonical CLI entry while exposing `--result-json` through service parsing. |
| `apps/api/services/task_service.py` | Pass job-specific `--result-json` for pipeline tasks and persist `result_path` metadata. |
| `tests/unit/test_spider_run_result.py` | New result serialization and partial-failure tests. |
| `tests/unit/test_pipeline_result_io.py` | New Pipeline result serialization/schema tests. |
| `tests/unit/test_pipeline_step_runner.py` | New subprocess streaming, exit, and timeout tests. |
| `tests/unit/test_pipeline_service.py` | Update Pipeline orchestration tests to use result JSON instead of stdout parsing. |
| `tests/unit/test_task_service_metadata.py` | Add API task result-path metadata coverage. |
| `javdb/pipeline/README.md` | Document log stream vs structured result split. |
| `docs/handbook/en/ops/logging.md`, `docs/handbook/zh/ops/logging.md` | Document that `SPIDER_*` remains compatibility output in Phase 1. |

---

## Task 1: Add Spider Result Model

**Files:**
- Create: `javdb/spider/app/result.py`
- Create: `tests/unit/test_spider_run_result.py`

- [ ] **Step 1: Write failing tests for Spider result JSON**

Create `tests/unit/test_spider_run_result.py`:

```python
from __future__ import annotations

import json

import pytest

from javdb.spider.app.result import (
    SPIDER_RESULT_KIND,
    SPIDER_RESULT_SCHEMA_VERSION,
    SpiderRunResult,
    SpiderRunStats,
    read_spider_result,
    write_spider_result_atomic,
)


def _result(**overrides):
    payload = dict(
        csv_path="reports/DailyReport/2026/05/Javdb_Today.csv",
        session_id="20260520T010203Z-0001",
        dedup_csv_path=None,
        stats=SpiderRunStats(
            pages="1-10",
            found=42,
            parsed=12,
            skipped=3,
            failed=1,
            no_new=2,
        ),
        mode="daily",
        url=None,
        phase="all",
        page_range="1-10",
        started_at="2026-05-20T01:02:03Z",
        finished_at="2026-05-20T01:05:03Z",
        exit_code=0,
        failure_reason=None,
    )
    payload.update(overrides)
    return SpiderRunResult(**payload)


def test_write_and_read_spider_result_round_trip(tmp_path):
    path = tmp_path / "spider-result.json"

    write_spider_result_atomic(path, _result())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == SPIDER_RESULT_SCHEMA_VERSION
    assert raw["kind"] == SPIDER_RESULT_KIND
    loaded = read_spider_result(path)
    assert loaded.csv_path == "reports/DailyReport/2026/05/Javdb_Today.csv"
    assert loaded.stats.parsed == 12


def test_spider_result_tolerates_unknown_fields(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["new_field"] = "ignored"
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = read_spider_result(path)

    assert loaded.session_id == "20260520T010203Z-0001"


def test_spider_result_rejects_wrong_kind(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["kind"] = "pipeline_run_result"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="spider_run_result"):
        read_spider_result(path)


def test_spider_partial_failure_result_preserves_unknowns_as_none(tmp_path):
    path = tmp_path / "spider-result.json"
    result = _result(
        csv_path=None,
        session_id="20260520T010203Z-0001",
        stats=None,
        exit_code=2,
        failure_reason="proxy ban detected",
    )

    write_spider_result_atomic(path, result)
    loaded = read_spider_result(path)

    assert loaded.csv_path is None
    assert loaded.stats is None
    assert loaded.exit_code == 2
    assert loaded.failure_reason == "proxy ban detected"
```

- [ ] **Step 2: Run the tests and verify the expected failure**

```bash
pytest tests/unit/test_spider_run_result.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.spider.app.result'`.

- [ ] **Step 3: Implement `javdb/spider/app/result.py`**

Create `javdb/spider/app/result.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

SPIDER_RESULT_SCHEMA_VERSION = "1.0"
SPIDER_RESULT_KIND = "spider_run_result"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SpiderRunStats:
    pages: str
    found: int
    parsed: int
    skipped: int
    failed: int
    no_new: int


@dataclass(frozen=True)
class SpiderRunResult:
    csv_path: str | None
    session_id: str | None
    dedup_csv_path: str | None
    stats: SpiderRunStats | None
    mode: Literal["daily", "adhoc"]
    url: str | None
    phase: Literal["1", "2", "all"]
    page_range: str | None
    started_at: str | None
    finished_at: str | None
    exit_code: int
    failure_reason: str | None
    schema_version: str = SPIDER_RESULT_SCHEMA_VERSION
    kind: str = SPIDER_RESULT_KIND
    generated_at: str = field(default_factory=utc_now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_spider_result_atomic(path: str | Path, result: SpiderRunResult) -> None:
    _atomic_write_json(Path(path), result.to_json_dict())


def read_spider_result(path: str | Path) -> SpiderRunResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema_version") != SPIDER_RESULT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported spider result schema_version: {raw.get('schema_version')!r}")
    if raw.get("kind") != SPIDER_RESULT_KIND:
        raise ValueError(f"Expected spider_run_result, got {raw.get('kind')!r}")
    required = {
        "csv_path", "session_id", "dedup_csv_path", "stats", "mode", "url",
        "phase", "page_range", "started_at", "finished_at", "exit_code",
        "failure_reason",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Missing spider result field(s): {', '.join(missing)}")
    stats_raw = raw.get("stats")
    stats = SpiderRunStats(**stats_raw) if isinstance(stats_raw, dict) else None
    return SpiderRunResult(
        csv_path=raw["csv_path"],
        session_id=raw["session_id"],
        dedup_csv_path=raw["dedup_csv_path"],
        stats=stats,
        mode=raw["mode"],
        url=raw["url"],
        phase=raw["phase"],
        page_range=raw["page_range"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        exit_code=int(raw["exit_code"]),
        failure_reason=raw["failure_reason"],
        schema_version=raw["schema_version"],
        kind=raw["kind"],
        generated_at=raw.get("generated_at") or utc_now_iso(),
    )
```

- [ ] **Step 4: Run the result tests**

```bash
pytest tests/unit/test_spider_run_result.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/app/result.py tests/unit/test_spider_run_result.py
git commit -m "feat(spider): add structured run result"
```

## Task 2: Add Pipeline Result Models And IO

**Files:**
- Modify: `javdb/pipeline/models.py`
- Create: `javdb/pipeline/result_io.py`
- Create: `tests/unit/test_pipeline_result_io.py`

- [ ] **Step 1: Write failing tests for Pipeline result JSON**

Create `tests/unit/test_pipeline_result_io.py`:

```python
from __future__ import annotations

import json

import pytest

from javdb.pipeline.models import PipelineRunResult, StepResult
from javdb.pipeline.result_io import (
    PIPELINE_RESULT_KIND,
    PIPELINE_RESULT_SCHEMA_VERSION,
    read_pipeline_result,
    write_pipeline_result_atomic,
)


def _step(name="spider", status="success"):
    return StepResult(
        name=name,
        status=status,
        required=True,
        run_on_failure=False,
        command=["python3", "-m", "apps.cli.spider"],
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:01:00Z",
        exit_code=0,
        failure_reason=None,
        result_path="reports/tmp/spider-result.json",
    )


def _pipeline_result():
    return PipelineRunResult(
        status="success",
        mode="daily",
        url=None,
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:05:00Z",
        exit_code=0,
        failure_reason=None,
        spider_result={"csv_path": "reports/DailyReport/x.csv"},
        steps=[_step()],
    )


def test_write_and_read_pipeline_result_round_trip(tmp_path):
    path = tmp_path / "pipeline-result.json"

    write_pipeline_result_atomic(path, _pipeline_result())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == PIPELINE_RESULT_SCHEMA_VERSION
    assert raw["kind"] == PIPELINE_RESULT_KIND
    loaded = read_pipeline_result(path)
    assert loaded.status == "success"
    assert loaded.steps[0].name == "spider"


def test_pipeline_result_rejects_missing_required_field(tmp_path):
    path = tmp_path / "pipeline-result.json"
    write_pipeline_result_atomic(path, _pipeline_result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["steps"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="steps"):
        read_pipeline_result(path)


def test_pipeline_result_tolerates_unknown_fields(tmp_path):
    path = tmp_path / "pipeline-result.json"
    write_pipeline_result_atomic(path, _pipeline_result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["extra"] = {"ignored": True}
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = read_pipeline_result(path)

    assert loaded.status == "success"
```

- [ ] **Step 2: Run the tests and verify the expected failure**

```bash
pytest tests/unit/test_pipeline_result_io.py -v
```

Expected: FAIL with missing `StepResult` or missing `javdb.pipeline.result_io`.

- [ ] **Step 3: Add result dataclasses to `javdb/pipeline/models.py`**

Append the following dataclasses below the existing ingestion models:

```python
from typing import Literal

PipelineRunStatus = Literal["success", "failed", "running"]
PipelineStepStatus = Literal["success", "failed", "timed_out", "skipped"]


@dataclass(frozen=True)
class StepPolicy:
    name: str
    required: bool = True
    run_on_failure: bool = False
    timeout_sec: int = 3600


@dataclass(frozen=True)
class StepResult:
    name: str
    status: PipelineStepStatus
    required: bool
    run_on_failure: bool
    command: List[str]
    started_at: str
    finished_at: str
    exit_code: Optional[int]
    failure_reason: Optional[str] = None
    result_path: Optional[str] = None


@dataclass(frozen=True)
class PipelineRunResult:
    status: PipelineRunStatus
    mode: str
    url: Optional[str]
    started_at: str
    finished_at: str
    exit_code: int
    failure_reason: Optional[str]
    spider_result: Optional[dict]
    steps: List[StepResult]
```

Also update `__all__` in `javdb/pipeline/engine.py` if callers need these
types through the compatibility export.

- [ ] **Step 4: Implement `javdb/pipeline/result_io.py`**

Create `javdb/pipeline/result_io.py`:

```python
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from javdb.pipeline.models import PipelineRunResult, StepResult
from javdb.spider.app.result import read_spider_result, write_spider_result_atomic
from javdb.spider.app.result import _atomic_write_json as _write_json_atomic

PIPELINE_RESULT_SCHEMA_VERSION = "1.0"
PIPELINE_RESULT_KIND = "pipeline_run_result"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pipeline_payload(result: PipelineRunResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["schema_version"] = PIPELINE_RESULT_SCHEMA_VERSION
    payload["kind"] = PIPELINE_RESULT_KIND
    payload["generated_at"] = utc_now_iso()
    return payload


def write_pipeline_result_atomic(path: str | Path, result: PipelineRunResult) -> None:
    _write_json_atomic(Path(path), _pipeline_payload(result))


def read_pipeline_result(path: str | Path) -> PipelineRunResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema_version") != PIPELINE_RESULT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported pipeline result schema_version: {raw.get('schema_version')!r}")
    if raw.get("kind") != PIPELINE_RESULT_KIND:
        raise ValueError(f"Expected pipeline_run_result, got {raw.get('kind')!r}")
    required = {
        "status", "mode", "url", "started_at", "finished_at", "exit_code",
        "failure_reason", "spider_result", "steps",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Missing pipeline result field(s): {', '.join(missing)}")
    steps = [StepResult(**item) for item in raw["steps"]]
    return PipelineRunResult(
        status=raw["status"],
        mode=raw["mode"],
        url=raw["url"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        exit_code=int(raw["exit_code"]),
        failure_reason=raw["failure_reason"],
        spider_result=raw["spider_result"],
        steps=steps,
    )


__all__ = [
    "PIPELINE_RESULT_KIND",
    "PIPELINE_RESULT_SCHEMA_VERSION",
    "read_pipeline_result",
    "read_spider_result",
    "write_pipeline_result_atomic",
    "write_spider_result_atomic",
]
```

- [ ] **Step 5: Run Pipeline result tests**

```bash
pytest tests/unit/test_pipeline_result_io.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add javdb/pipeline/models.py javdb/pipeline/result_io.py javdb/pipeline/engine.py tests/unit/test_pipeline_result_io.py
git commit -m "feat(pipeline): add structured run result model"
```

## Task 3: Add SubprocessStepRunner And LogSink

**Files:**
- Create: `javdb/pipeline/step_runner.py`
- Create: `tests/unit/test_pipeline_step_runner.py`

- [ ] **Step 1: Write failing tests for streaming, exit code, and timeout**

Create `tests/unit/test_pipeline_step_runner.py`:

```python
from __future__ import annotations

import sys

from javdb.pipeline.models import StepPolicy
from javdb.pipeline.step_runner import LogSink, SubprocessStepRunner


class RecordingSink:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def write_line(self, step_name: str, line: str) -> None:
        self.lines.append((step_name, line))


def test_subprocess_step_runner_streams_lines():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=10)

    result = runner.run(
        policy,
        [sys.executable, "-c", "print('one'); print('two')"],
    )

    assert result.status == "success"
    assert result.exit_code == 0
    assert sink.lines == [("demo", "one\n"), ("demo", "two\n")]


def test_subprocess_step_runner_maps_nonzero_exit_to_failed():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=10)

    result = runner.run(
        policy,
        [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"],
    )

    assert result.status == "failed"
    assert result.exit_code == 3
    assert result.failure_reason == "exit code 3"


def test_subprocess_step_runner_times_out():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=1)

    result = runner.run(
        policy,
        [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(5)"],
    )

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert "timed out" in (result.failure_reason or "")
    assert ("demo", "start\n") in sink.lines
```

- [ ] **Step 2: Run tests and verify expected failure**

```bash
pytest tests/unit/test_pipeline_step_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.pipeline.step_runner'`.

- [ ] **Step 3: Implement `javdb/pipeline/step_runner.py`**

Create `javdb/pipeline/step_runner.py`:

```python
from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Protocol, Sequence

from javdb.pipeline.models import StepPolicy, StepResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LogSink(Protocol):
    def write_line(self, step_name: str, line: str) -> None:
        pass


class ConsoleAndFileLogSink:
    def __init__(self):
        self._file_handler = None
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.FileHandler):
                self._file_handler = handler
                break

    def write_line(self, step_name: str, line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()
        if self._file_handler is not None:
            self._file_handler.stream.write(line)
            self._file_handler.stream.flush()


class SubprocessStepRunner:
    def __init__(self, *, log_sink: LogSink | None = None):
        self._log_sink = log_sink or ConsoleAndFileLogSink()

    def run(
        self,
        policy: StepPolicy,
        command: Sequence[str],
        *,
        result_path: str | None = None,
    ) -> StepResult:
        started_at = _utc_now_iso()
        deadline = time.monotonic() + policy.timeout_sec
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        timed_out = False
        try:
            if process.stdout:
                for line in iter(process.stdout.readline, ""):
                    if line:
                        self._log_sink.write_line(policy.name, line)
                    if time.monotonic() > deadline:
                        timed_out = True
                        break
                process.stdout.close()
            if timed_out:
                process.kill()
                process.wait()
                return StepResult(
                    name=policy.name,
                    status="timed_out",
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=list(command),
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=None,
                    failure_reason=f"timed out after {policy.timeout_sec}s",
                    result_path=result_path,
                )
            return_code = process.wait()
            status = "success" if return_code == 0 else "failed"
            return StepResult(
                name=policy.name,
                status=status,
                required=policy.required,
                run_on_failure=policy.run_on_failure,
                command=list(command),
                started_at=started_at,
                finished_at=_utc_now_iso(),
                exit_code=return_code,
                failure_reason=None if return_code == 0 else f"exit code {return_code}",
                result_path=result_path,
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
```

- [ ] **Step 4: Run step runner tests**

```bash
pytest tests/unit/test_pipeline_step_runner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/pipeline/step_runner.py tests/unit/test_pipeline_step_runner.py
git commit -m "feat(pipeline): add subprocess step runner"
```

## Task 4: Add `--result-json` To Spider CLI And Runtime

**Files:**
- Modify: `javdb/spider/app/cli.py`
- Modify: `javdb/spider/app/run_service.py`
- Modify: `tests/unit/test_spider_run_result.py`
- Modify: `tests/smoke/test_spider.py`
- Modify: `tests/smoke/test_spider_app_main.py`

- [ ] **Step 1: Add tests for result JSON CLI/runtime behavior**

Append to `tests/unit/test_spider_run_result.py`:

```python
def test_spider_result_can_represent_failure_without_stats(tmp_path):
    path = tmp_path / "failed-spider-result.json"
    result = SpiderRunResult(
        csv_path=None,
        session_id=None,
        dedup_csv_path=None,
        stats=None,
        mode="daily",
        url=None,
        phase="all",
        page_range=None,
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:00:10Z",
        exit_code=1,
        failure_reason="boom",
    )

    write_spider_result_atomic(path, result)
    loaded = read_spider_result(path)

    assert loaded.exit_code == 1
    assert loaded.failure_reason == "boom"
```

- [ ] **Step 2: Add `--result-json` argument**

In `javdb/spider/app/cli.py`, add:

```python
parser.add_argument(
    "--result-json",
    type=str,
    default=None,
    help="Write a versioned SpiderRunResult JSON sidecar to this path.",
)
```

- [ ] **Step 3: Write success result from `run_service`**

In `javdb/spider/app/run_service.py`, capture `started_at` near the start of
`main()`. After stats are known and before returning success, build
`SpiderRunResult` and call `write_spider_result_atomic(args.result_json, result)`
when `args.result_json` is set.

Use fields already computed by the run:

```python
result = SpiderRunResult(
    csv_path=str(csv_path) if csv_path else None,
    session_id=str(_session_id) if _session_id else None,
    dedup_csv_path=str(dedup_csv_path) if dedup_csv_path else None,
    stats=SpiderRunStats(
        pages=f"{start_page}-*" if parse_all else f"{start_page}-{end_page}",
        found=int(total_discovered),
        parsed=int(len(rows)),
        skipped=int(skipped_history_count),
        failed=int(failed_count),
        no_new=int(no_new_torrents_count),
    ),
    mode="adhoc" if args.url else "daily",
    url=args.url,
    phase=str(phase_mode),
    page_range=f"{start_page}-*" if parse_all else f"{start_page}-{end_page}",
    started_at=started_at,
    finished_at=utc_now_iso(),
    exit_code=0,
    failure_reason=None,
)
write_spider_result_atomic(args.result_json, result)
```

- [ ] **Step 4: Write best-effort failure result**

In the existing `except Exception as exc:` block in `javdb/spider/app/run_service.py`,
write a partial result when `args.result_json` exists. Preserve any known
`_session_id`, `csv_path`, `dedup_csv_path`, `phase_mode`, and page range values.
Set `exit_code=1` unless the exception is `SystemExit` with a concrete non-zero
integer code.

- [ ] **Step 5: Run Spider result and compatibility tests**

```bash
pytest tests/unit/test_spider_run_result.py tests/smoke/test_spider.py tests/smoke/test_spider_app_main.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/app/cli.py javdb/spider/app/run_service.py tests/unit/test_spider_run_result.py tests/smoke/test_spider.py tests/smoke/test_spider_app_main.py
git commit -m "feat(spider): write structured run result sidecar"
```

## Task 5: Refactor Pipeline Service Around Step Results

**Files:**
- Modify: `javdb/pipeline/service.py`
- Modify: `tests/unit/test_pipeline_service.py`

- [ ] **Step 1: Update Pipeline tests to provide Spider result JSON**

In `tests/unit/test_pipeline_service.py`, replace fake `run_command()` return
strings with a fake `SubprocessStepRunner` that writes a Spider result JSON when
the spider command includes `--result-json`.

Use this helper:

```python
from javdb.pipeline.models import StepResult
from javdb.spider.app.result import SpiderRunResult, SpiderRunStats, write_spider_result_atomic


class FakeStepRunner:
    def __init__(self):
        self.calls = []

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
        return StepResult(
            name=policy.name,
            status="success",
            required=policy.required,
            run_on_failure=policy.run_on_failure,
            command=list(command),
            started_at="2026-05-20T01:00:00Z",
            finished_at="2026-05-20T01:01:00Z",
            exit_code=0,
            failure_reason=None,
            result_path=result_path,
        )
```

- [ ] **Step 2: Add Pipeline CLI `--result-json` argument**

In `javdb/pipeline/service.py`, extend `parse_arguments()`:

```python
parser.add_argument(
    "--result-json",
    type=str,
    default=None,
    help="Write a versioned PipelineRunResult JSON sidecar to this path.",
)
```

- [ ] **Step 3: Replace `run_command()` usage with `SubprocessStepRunner`**

In `javdb/pipeline/service.py`, create a runner near the start of `main()`:

```python
runner = SubprocessStepRunner()
```

For Spider:

```python
spider_result_path = Path(tempfile.mkdtemp(prefix="pipeline-result-")) / "spider-result.json"
spider_args.extend(["--result-json", str(spider_result_path)])
spider_step = runner.run(
    StepPolicy(name="spider", required=True, timeout_sec=3600),
    spider_cmd + spider_args,
    result_path=str(spider_result_path),
)
```

If the Spider step succeeds, read `read_spider_result(spider_result_path)` and
use `csv_path` / `session_id` from the result. Do not parse stdout.

- [ ] **Step 4: Keep qB/PikPak/email/dedup subprocess steps**

Execute qB, PikPak, rclone dedup, and email through `runner.run()` with explicit
`StepPolicy`. Keep rclone dedup optional/non-fatal:

```python
dedup_policy = StepPolicy(name="rclone_dedup", required=False, timeout_sec=3600)
```

For required steps, failed or timed-out status raises the same Pipeline failure
path as the previous `RuntimeError`.

- [ ] **Step 5: Record success and failure notification steps**

Record success email as `email_notification`. In the `except` path, run failure
email with:

```python
StepPolicy(name="email_notification_failure", required=False, run_on_failure=True, timeout_sec=3600)
```

Append its `StepResult` regardless of success or failure. Keep final exit code
as 1 when the main flow failed.

- [ ] **Step 6: Write PipelineRunResult**

Before `sys.exit()`, build `PipelineRunResult` and write it when
`args.result_json` is set. Also write best-effort partial results in failure
paths.

- [ ] **Step 7: Remove internal stdout extraction dependency**

Delete or stop calling `extract_csv_path_from_output()` and
`extract_session_id_from_output()` from Pipeline core. Keep them only if tests or
compatibility code still need them outside Pipeline core.

- [ ] **Step 8: Run Pipeline service tests**

```bash
pytest tests/unit/test_pipeline_service.py tests/unit/test_pipeline_result_io.py tests/unit/test_pipeline_step_runner.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add javdb/pipeline/service.py tests/unit/test_pipeline_service.py
git commit -m "refactor(pipeline): read spider result sidecar"
```

## Task 6: Add API Task Result Metadata

**Files:**
- Modify: `apps/api/services/task_service.py`
- Modify: `tests/unit/test_task_service_metadata.py`
- Modify: `tests/unit/test_api_task_service_security.py`

- [ ] **Step 1: Add metadata tests**

Append to `tests/unit/test_task_service_metadata.py`:

```python
from apps.api.services import task_service


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
```

- [ ] **Step 2: Allow `--result-json` for task commands**

In `apps/api/services/task_service.py`, add to `_TASK_ALLOWED_FLAGS`:

```python
"--result-json": "job_result_file",
```

Add validation for `job_result_file`: it must resolve under
`context.RESOLVED_JOB_LOG_DIR` and end with `.result.json`.

- [ ] **Step 3: Pass result JSON path for pipeline jobs**

In `_spawn_job()`, when `command[3] == "apps.cli.pipeline"` and the command does
not already contain `--result-json`, compute:

```python
result_path = _resolved_path_under_job_log_dir(job_id, ".result.json")
command.extend(["--result-json", str(result_path)])
```

Record `result_path` in the in-memory job dict and `.meta.json`.

- [ ] **Step 4: Preserve stream response shape**

Run the existing task metadata tests and add no required frontend field changes
in this phase.

```bash
pytest tests/unit/test_task_service_metadata.py tests/unit/test_api_task_service_security.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/services/task_service.py tests/unit/test_task_service_metadata.py tests/unit/test_api_task_service_security.py
git commit -m "feat(api): record pipeline result sidecar path"
```

## Task 7: Documentation And Phase 1 Gate

**Files:**
- Modify: `javdb/pipeline/README.md`
- Modify: `docs/handbook/en/ops/logging.md`
- Modify: `docs/handbook/zh/ops/logging.md`

- [ ] **Step 1: Update `javdb/pipeline/README.md`**

Document:

- Pipeline logs remain a real-time human stream.
- Pipeline structured data uses result JSON.
- `SPIDER_*` stdout is compatibility output, not the internal Pipeline protocol.

- [ ] **Step 2: Update logging handbook in English and Chinese**

Add a section after "GitHub Actions Step Summary":

```markdown
## Structured Run Results

Pipeline and Spider CLIs can write versioned result JSON through
`--result-json <path>`. These files are the machine-readable run contract.
The `SPIDER_*` stdout lines remain compatibility output for existing workflows
during ADR-012 Phase 1. Do not add new Pipeline internals that parse those
stdout markers for business data.
```

- [ ] **Step 3: Run docs and focused tests**

```bash
pytest tests/unit/test_spider_run_result.py tests/unit/test_pipeline_result_io.py tests/unit/test_pipeline_step_runner.py tests/unit/test_pipeline_service.py tests/unit/test_task_service_metadata.py -v
git diff --check
```

Expected: PASS and no whitespace errors.

- [ ] **Step 4: Commit**

```bash
git add javdb/pipeline/README.md docs/handbook/en/ops/logging.md docs/handbook/zh/ops/logging.md
git commit -m "docs(pipeline): document structured run results"
```

## Phase 1 Completion Gate

- [ ] `pytest tests/unit/test_spider_run_result.py tests/unit/test_pipeline_result_io.py tests/unit/test_pipeline_step_runner.py tests/unit/test_pipeline_service.py tests/unit/test_task_service_metadata.py -v` passes.
- [ ] Existing `SPIDER_OUTPUT_CSV`, `SPIDER_SESSION_ID`, and `SPIDER_STAT_*` stdout compatibility tests pass.
- [ ] Pipeline no longer reads `csv_path` or `session_id` by parsing stdout.
- [ ] Pipeline CLI writes `PipelineRunResult` when `--result-json` is supplied.
- [ ] Spider CLI writes `SpiderRunResult` when `--result-json` is supplied.
- [ ] API task runner stores `result_path` metadata for pipeline jobs.
- [ ] Daily/AdHoc/Test workflows are unchanged in Phase 1.
