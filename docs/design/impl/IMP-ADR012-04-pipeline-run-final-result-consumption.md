# IMP-ADR012-04: ADR-012 Final Phase - Result Consumption Cleanup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move scoped GitHub workflow and API task consumers from stdout grepping to structured result JSON while preserving real-time logs.

**Architecture:** Workflows keep streaming logs with `tee /dev/stderr` or equivalent. Machine-readable data comes from `--result-json` plus a small JSON-to-GitHub-output helper. API task runner remains subprocess-based in this ADR but records and can expose result metadata.

**Tech Stack:** GitHub Actions YAML, Python JSON helper CLI, pytest, Markdown docs.

**Prerequisite:** [IMP-ADR012-02](IMP-ADR012-02-pipeline-run-phase1-bake.md) passed and [IMP-ADR012-03](IMP-ADR012-03-pipeline-run-phase2-in-process-spider.md) is complete unless the final phase is explicitly limited to workflow result consumption.

**Source spec:** [ADR-012](../adr/ADR-012-pipeline-run-structured-boundary.md), D13.

---

## Files

| Path | Responsibility |
|---|---|
| `apps/cli/ops/run_result_outputs.py` | New helper that reads result JSON and writes selected values to `GITHUB_OUTPUT` and `GITHUB_STEP_SUMMARY`. |
| `.github/workflows/DailyIngestion.yml` | Stop grepping `SPIDER_*` stdout; read result JSON. |
| `.github/workflows/AdHocIngestion.yml` | Stop grepping `SPIDER_*` stdout; read result JSON. |
| `.github/workflows/TestIngestion.yml` | Stop grepping `SPIDER_OUTPUT_CSV`; read result JSON. |
| `apps/api/services/task_service.py` | Expose result metadata or summary without changing streaming behavior. |
| `apps/api/schemas/payloads.py` | Add optional result fields to task response schemas. |
| `tests/unit/test_run_result_outputs.py` | Helper CLI tests. |
| `tests/unit/test_task_service_metadata.py` | API result metadata tests. |
| `docs/handbook/en/ops/logging.md`, `docs/handbook/zh/ops/logging.md` | Document that workflows no longer grep `SPIDER_*`. |

---

## Task 1: Add Result-To-GitHub-Output Helper

**Files:**
- Create: `apps/cli/ops/run_result_outputs.py`
- Create: `tests/unit/test_run_result_outputs.py`

- [ ] **Step 1: Write helper tests**

Create `tests/unit/test_run_result_outputs.py`:

```python
from __future__ import annotations

import json

from apps.cli.ops import run_result_outputs


def test_extract_spider_outputs_from_result(tmp_path):
    result = tmp_path / "spider-result.json"
    result.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "spider_run_result",
                "generated_at": "2026-05-20T01:00:00Z",
                "csv_path": "reports/DailyReport/x.csv",
                "session_id": "s1",
                "dedup_csv_path": "reports/dedup.csv",
                "stats": {
                    "pages": "1-2",
                    "found": 10,
                    "parsed": 8,
                    "skipped": 1,
                    "failed": 0,
                    "no_new": 1,
                },
                "mode": "daily",
                "url": None,
                "phase": "all",
                "page_range": "1-2",
                "started_at": "2026-05-20T01:00:00Z",
                "finished_at": "2026-05-20T01:02:00Z",
                "exit_code": 0,
                "failure_reason": None,
            }
        ),
        encoding="utf-8",
    )

    outputs = run_result_outputs.outputs_from_result(result)

    assert outputs["csv_filename"] == "reports/DailyReport/x.csv"
    assert outputs["session_id"] == "s1"
    assert outputs["dedup_csv_path"] == "reports/dedup.csv"
    assert outputs["stat_pages"] == "1-2"
    assert outputs["stat_parsed"] == "8"


def test_write_github_output_file(tmp_path):
    output = tmp_path / "github-output.txt"

    run_result_outputs.write_github_output(
        output,
        {"csv_filename": "reports/x.csv", "session_id": "s1"},
    )

    assert output.read_text(encoding="utf-8") == "csv_filename=reports/x.csv\nsession_id=s1\n"
```

- [ ] **Step 2: Run tests and verify expected failure**

```bash
pytest tests/unit/test_run_result_outputs.py -v
```

Expected: FAIL until helper exists.

- [ ] **Step 3: Implement helper**

Create `apps/cli/ops/run_result_outputs.py`:

```python
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping


def outputs_from_result(path: str | Path) -> dict[str, str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    stats = raw.get("stats") or {}
    outputs = {
        "csv_filename": raw.get("csv_path") or "",
        "session_id": raw.get("session_id") or "",
        "dedup_csv_path": raw.get("dedup_csv_path") or "",
        "stat_pages": str(stats.get("pages") or ""),
        "stat_found": str(stats.get("found") if stats.get("found") is not None else ""),
        "stat_parsed": str(stats.get("parsed") if stats.get("parsed") is not None else ""),
        "stat_skipped": str(stats.get("skipped") if stats.get("skipped") is not None else ""),
        "stat_failed": str(stats.get("failed") if stats.get("failed") is not None else ""),
        "stat_no_new": str(stats.get("no_new") if stats.get("no_new") is not None else ""),
    }
    return {key: value for key, value in outputs.items() if value != ""}


def write_github_output(path: str | Path, outputs: Mapping[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as fp:
        for key, value in outputs.items():
            fp.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write GitHub outputs from a run result JSON file.")
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    outputs = outputs_from_result(args.result_json)
    if args.github_output:
        write_github_output(args.github_output, outputs)
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run helper tests**

```bash
pytest tests/unit/test_run_result_outputs.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/cli/ops/run_result_outputs.py tests/unit/test_run_result_outputs.py
git commit -m "feat(ops): emit github outputs from run result"
```

## Task 2: Update DailyIngestion Workflow

**Files:**
- Modify: `.github/workflows/DailyIngestion.yml`
- Modify: `tests/unit/test_workflow_resolve_write_mode.py`

- [ ] **Step 1: Add result JSON path**

In the Spider step, create:

```bash
SPIDER_RESULT_JSON="$RUNNER_TEMP/spider-result.json"
SPIDER_CMD+=(--result-json "$SPIDER_RESULT_JSON")
```

- [ ] **Step 2: Preserve real-time log streaming**

Keep:

```bash
SPIDER_OUTPUT=$("${SPIDER_CMD[@]}" | tee /dev/stderr) && SPIDER_EXIT=0 || SPIDER_EXIT=$?
```

or replace it with an equivalent that still streams logs while the process runs.
Do not wait until process exit to print captured logs.

- [ ] **Step 3: Replace grep extraction with JSON helper**

After the Spider command exits, run:

```bash
python3 -m apps.cli.ops.run_result_outputs \
  --result-json "$SPIDER_RESULT_JSON" \
  --github-output "$GITHUB_OUTPUT"
```

Use helper outputs instead of `grep "^SPIDER_OUTPUT_CSV="`.

- [ ] **Step 4: Keep Step Summary**

Build the existing Markdown summary from helper output variables. Preserve
columns for pages, found, parsed, skipped, failed, csv, and session_id.

- [ ] **Step 5: Run workflow shell tests**

```bash
pytest tests/unit/test_workflow_resolve_write_mode.py tests/unit/test_run_result_outputs.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/DailyIngestion.yml tests/unit/test_workflow_resolve_write_mode.py
git commit -m "ci(pipeline): read daily spider result json"
```

## Task 3: Update AdHocIngestion Workflow

**Files:**
- Modify: `.github/workflows/AdHocIngestion.yml`

- [ ] **Step 1: Add `SPIDER_RESULT_JSON` under `$RUNNER_TEMP`**

Use the same pattern as DailyIngestion:

```bash
SPIDER_RESULT_JSON="$RUNNER_TEMP/spider-result.json"
SPIDER_CMD+=(--result-json "$SPIDER_RESULT_JSON")
```

- [ ] **Step 2: Replace stdout grep extraction**

Run `python3 -m apps.cli.ops.run_result_outputs` and use its GitHub outputs.

- [ ] **Step 3: Preserve real-time logs and summary table**

Keep `tee /dev/stderr` or equivalent streaming. Keep the Ad-Hoc summary title
and table shape.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_workflow_resolve_write_mode.py tests/unit/test_run_result_outputs.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/AdHocIngestion.yml
git commit -m "ci(pipeline): read adhoc spider result json"
```

## Task 4: Update TestIngestion Workflow

**Files:**
- Modify: `.github/workflows/TestIngestion.yml`

- [ ] **Step 1: Add result JSON paths for daily and ad hoc Spider test steps**

Use distinct paths:

```bash
SPIDER_RESULT_JSON="$RUNNER_TEMP/daily-spider-result.json"
```

and:

```bash
SPIDER_RESULT_JSON="$RUNNER_TEMP/adhoc-spider-result.json"
```

- [ ] **Step 2: Replace `SPIDER_OUTPUT_CSV` grep**

Use:

```bash
python3 -m apps.cli.ops.run_result_outputs \
  --result-json "$SPIDER_RESULT_JSON" \
  --github-output "$GITHUB_OUTPUT"
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_workflow_resolve_write_mode.py tests/unit/test_run_result_outputs.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/TestIngestion.yml
git commit -m "ci(pipeline): read testing spider result json"
```

## Task 5: Expose API Task Result Metadata

**Files:**
- Modify: `apps/api/services/task_service.py`
- Modify: `apps/api/schemas/payloads.py`
- Modify: `tests/unit/test_task_service_metadata.py`

- [ ] **Step 1: Add metadata tests**

Add a test proving completed task payloads include `result_path` and, when the
result JSON exists, a small `result_summary`:

```python
import json
from datetime import datetime, timezone

from apps.api.services import context, task_service


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
```

- [ ] **Step 2: Add optional schema fields**

In the task response schema, add optional fields:

```python
result_path: Optional[str] = None
result_summary: Optional[Dict[str, Any]] = None
```

- [ ] **Step 3: Load result summary safely**

In `_get_job()`, if metadata contains `result_path` and the file exists, load a
small summary:

```python
{
    "kind": raw.get("kind"),
    "schema_version": raw.get("schema_version"),
    "status": raw.get("status"),
    "exit_code": raw.get("exit_code"),
    "failure_reason": raw.get("failure_reason"),
}
```

Ignore invalid JSON with a warning in server logs and do not fail task payload
retrieval.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_task_service_metadata.py tests/unit/test_api_task_service_security.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/services/task_service.py apps/api/schemas/payloads.py tests/unit/test_task_service_metadata.py
git commit -m "feat(api): expose task result metadata"
```

## Task 6: Documentation And Final Gate

**Files:**
- Modify: `docs/handbook/en/ops/logging.md`
- Modify: `docs/handbook/zh/ops/logging.md`

- [ ] **Step 1: Update logging docs**

Document that workflows now use result JSON for machine-readable outputs while
keeping log streaming for human-readable output.

- [ ] **Step 2: Run final checks**

```bash
rg -n "grep \\\"\\^SPIDER_|grep '\\^SPIDER_|SPIDER_OUTPUT=\\$\\(" .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml .github/workflows/TestIngestion.yml
pytest tests/unit/test_run_result_outputs.py tests/unit/test_workflow_resolve_write_mode.py tests/unit/test_task_service_metadata.py -v
git diff --check
```

Expected:

- grep finds no stdout parsing of `SPIDER_*` in the three target workflows;
- tests pass;
- no whitespace errors.

- [ ] **Step 3: Commit**

```bash
git add docs/handbook/en/ops/logging.md docs/handbook/zh/ops/logging.md
git commit -m "docs(pipeline): document result json workflow outputs"
```

## Final Phase Completion Gate

- [ ] DailyIngestion no longer greps `SPIDER_*` stdout.
- [ ] AdHocIngestion no longer greps `SPIDER_*` stdout.
- [ ] TestIngestion no longer greps `SPIDER_OUTPUT_CSV`.
- [ ] Real-time workflow logs remain visible while Spider runs.
- [ ] GitHub outputs still include CSV path, session ID, and dedup CSV path when present.
- [ ] GitHub Step Summary still renders spider stats.
- [ ] API task payloads expose result metadata without changing stream behavior.
- [ ] API task runner remains subprocess-based in this ADR.
