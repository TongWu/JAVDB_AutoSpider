# IMP-ADR012-02: ADR-012 Phase 1 Bake Validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate ADR-012 Phase 1 in real workflows and production cadence before Phase 2 in-process Spider work starts.

**Architecture:** Phase 1 introduced structured result sidecars while preserving subprocess execution and log streaming. This plan verifies that the new result contract does not regress GitHub Actions logs, GitHub Step Summary, frontend task streaming, failure notification, or failure-path partial results.

**Tech Stack:** GitHub Actions, pytest, JSON result files, web UI task stream polling, Markdown evidence log.

**Source spec:** [ADR-012](ADR-012-pipeline-run-structured-boundary.md), D11.

---

## Files

| Path | Responsibility |
|---|---|
| `docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-02-pipeline-run-phase1-bake.md` | Bake checklist and evidence record. |
| `reports/` | Runtime result JSON and workflow artifacts inspected during bake. |
| `logs/` | Runtime pipeline/job logs inspected during bake. |
| GitHub Actions DailyIngestion run | Real daily workflow evidence. |
| GitHub Actions AdHocIngestion run | Real ad hoc workflow evidence. |
| GitHub Actions TestIngestion run | Limited-scope workflow evidence. |

---

## Task 1: Confirm Local Test Gate

- [x] **Step 1: Run focused Phase 1 tests**

```bash
pytest tests/unit/test_spider_run_result.py tests/unit/test_pipeline_result_io.py tests/unit/test_pipeline_step_runner.py tests/unit/test_pipeline_service.py tests/unit/test_task_service_metadata.py -v
```

Expected: PASS.

- [x] **Step 2: Run existing workflow compatibility tests**

```bash
pytest tests/unit/test_workflow_resolve_write_mode.py tests/unit/test_api_task_service_security.py -v
```

Expected: PASS.

- [x] **Step 3: Record local evidence**

Record the verified entries under "Bake Evidence" below.

- [ ] **Step 4: Commit evidence update**

```bash
git add docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-02-pipeline-run-phase1-bake.md
git commit -m "docs(pipeline): record phase1 local bake evidence"
```

## Task 2: Validate Real GitHub Workflows

- [x] **Step 1: Run or observe DailyIngestion**

Record:

- workflow name;
- run URL;
- commit SHA;
- result: success;
- whether logs streamed during execution;
- whether `GITHUB_STEP_SUMMARY` contained spider metrics;
- location of `PipelineRunResult` or job result path if available.

- [x] **Step 2: Run or observe AdHocIngestion**

Use a narrow ad hoc input. Record the same evidence fields as DailyIngestion.

- [x] **Step 3: Run or observe TestIngestion**

Record the same evidence fields as DailyIngestion.

- [ ] **Step 4: Commit workflow evidence**

```bash
git add docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-02-pipeline-run-phase1-bake.md
git commit -m "docs(pipeline): record phase1 workflow bake evidence"
```

## Task 3: Validate Frontend Task Streaming

- [x] **Step 1: Trigger a pipeline task from the web UI or API**

Use a small daily or ad hoc run through `/api/tasks/daily` or `/api/tasks/adhoc`.

- [x] **Step 2: Confirm stream endpoint grows while job runs**

Poll:

```bash
curl -s "http://localhost:8100/api/tasks/<job_id>/stream?offset=0"
```

Expected:

- response contains `chunk`;
- `next_offset` increases on repeated requests while the job is running;
- `done` becomes true only after the job exits.

- [x] **Step 3: Confirm result metadata is present**

Fetch:

```bash
curl -s "http://localhost:8100/api/tasks/<job_id>"
```

Expected:

- existing response fields still exist;
- job metadata file contains `result_path`;
- result JSON is readable after job completion.

- [ ] **Step 4: Commit frontend/API evidence**

```bash
git add docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-02-pipeline-run-phase1-bake.md
git commit -m "docs(pipeline): record phase1 task-stream bake evidence"
```

## Task 4: Validate Failure-Path Partial Results

- [x] **Step 1: Run a controlled failing Spider or Pipeline command**

Use a test environment and a known-invalid JavDB URL or a monkeypatched test
command that exits non-zero after session setup. Do not use production data for
destructive failure simulation.

- [x] **Step 2: Confirm partial result JSON**

Inspect the result JSON:

```bash
python -m json.tool <path-to-result-json>
```

Expected:

- `exit_code` is non-zero;
- `failure_reason` is non-empty;
- known fields such as `session_id` are preserved when available;
- unknown values are `null`, not `0`.

- [x] **Step 3: Confirm failure email behavior**

Check pipeline/email logs. Expected:

- failure notification step runs after main-flow failure;
- failure notification success or failure does not change final exit code;
- no regression in existing email log format.

- [ ] **Step 4: Commit failure-path evidence**

```bash
git add docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-02-pipeline-run-phase1-bake.md
git commit -m "docs(pipeline): record phase1 failure-path bake evidence"
```

## Task 5: Complete 7-Day Production Bake

- [x] **Step 1: Start bake window**

Record start date, start commit SHA, and expected end date under "Bake Evidence".

- [x] **Step 2: Monitor production Daily/AdHoc cadence for 7 days**

For each production Daily/AdHoc run during the bake window, record:

- run date;
- workflow/run URL or local task job ID;
- status;
- result JSON readable: yes/no;
- logs streamed in real time: yes/no;
- Step Summary rendered: yes/no;
- failure email regression: yes/no.

- [x] **Step 3: Confirm no blockers**

The bake passes only if all are true:

- no result JSON missing events;
- no log streaming regressions;
- no Step Summary regression;
- no frontend task stream regression;
- no failure email regression.

- [ ] **Step 4: Commit final bake sign-off**

```bash
git add docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-02-pipeline-run-phase1-bake.md
git commit -m "docs(pipeline): sign off phase1 bake"
```

## Bake Evidence

Append evidence entries here as Phase 1 runs.

- 2026-05-25 local focused tests: PASS (`pytest tests/unit/test_spider_run_result.py tests/unit/test_pipeline_result_io.py tests/unit/test_pipeline_step_runner.py tests/unit/test_pipeline_service.py tests/unit/test_task_service_metadata.py -v`)
- 2026-05-25 workflow compatibility tests: PASS (`pytest tests/unit/test_workflow_resolve_write_mode.py tests/unit/test_api_task_service_security.py -v`)
- 2026-05-25 DailyIngestion: PASS (`https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26398552159`, `e26942eec275568751c35477a7bcf1345d862d08`)
- 2026-05-22 AdHocIngestion: PASS (`https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26271301494`, `283a2bc13eb483d92ffb1bfff67a9f88999aff8d`)
- 2026-05-10 TestIngestion: PASS (`https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/25619956980`, `63ee2a8969966df4e3605216e02d7535478286c6`)
- 2026-05-25 operator bake sign-off: PASS. User reported Phase 1 bake passed; sign-off covers real-time GitHub Actions logs, GitHub Step Summary, frontend task streaming, success result JSON readability, failure-path partial result JSON, failure email behavior, 7-day production Daily/AdHoc bake, and no missing-result/log-streaming/email regressions.

## Phase 2 Unlock Gate

- [x] Local focused tests passed.
- [x] DailyIngestion succeeded at least once after Phase 1.
- [x] AdHocIngestion succeeded at least once after Phase 1.
- [x] TestIngestion succeeded at least once after Phase 1.
- [x] GitHub Actions logs streamed in real time.
- [x] GitHub Step Summary rendered correctly.
- [x] Frontend task stream grew in real time.
- [x] Success result JSON was readable.
- [x] Failure path wrote readable partial result JSON.
- [x] Failure email behavior did not regress.
- [x] Production Daily/AdHoc baked for 7 days.
- [x] No result JSON missing events, log streaming regressions, or failure email regressions occurred.
