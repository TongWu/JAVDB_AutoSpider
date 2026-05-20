# Pipeline Run Structured Boundary Design

Date: 2026-05-20

## Context

`javdb.pipeline.service` currently orchestrates the long-running pipeline by
spawning CLI subprocesses. For the Spider step, it captures child stdout and
parses lines such as `SPIDER_OUTPUT_CSV=` and `SPIDER_SESSION_ID=` to pass data
to downstream steps.

This mixes two separate contracts:

- log streaming, which must remain real-time for GitHub Actions and the web UI;
- structured run results, which should not be inferred from stdout text.

The current GitHub Actions workflows also parse `SPIDER_*` stdout lines to
populate `GITHUB_OUTPUT` and the step summary. The API task runner starts
pipeline/spider CLIs as subprocesses and streams logs by writing child
stdout/stderr directly to job log files that the frontend polls by byte offset.

## Goals

- Stop using stdout parsing as the Pipeline module's internal data protocol.
- Preserve real-time GitHub Actions logs.
- Preserve frontend task log streaming.
- Introduce versioned structured run result JSON for Spider and Pipeline runs.
- Keep current production failure semantics.
- Make the migration converge in phases rather than stopping at compatibility.

## Non-Goals

- Do not redesign qB uploader, PikPak, or email as in-process services in this
  ADR.
- Do not require every pipeline step to produce its own complete result JSON in
  this ADR.
- Do not redesign failure recovery as a DAG or compensation workflow in this
  ADR.
- Do not move the API task runner fully off subprocesses in this ADR.
- Do not remove `SPIDER_*` stdout compatibility in Phase 1.

## Core Principle

The implementation must keep log streaming and structured result transfer as
separate channels.

Log stream:

- human-facing;
- real-time;
- written to console, GitHub Actions logs, pipeline log files, and API job log
  files;
- not parsed by Pipeline core for business data.

Structured result:

- machine-facing;
- versioned JSON;
- atomically written;
- parsed by Pipeline/API/workflow adapters;
- tolerant of additive fields and strict about required keys.

## Selected Approach

Use staged convergence.

Phase 1 removes stdout parsing from the Pipeline module while preserving
subprocess execution and real-time logs. Phase 1 writes and reads structured
result sidecars.

Phase 1 bake is a separate gate. It must prove the result contract does not
break real GitHub Actions, frontend task streaming, or failure notification.

Phase 2 replaces the Spider subprocess inside the Pipeline with an in-process
runner only after the bake gate passes.

The final phase moves GitHub workflows and lightweight API task metadata to
structured result consumption. If workflow/API cleanup is too large, it is split
into a dedicated final phase.

Separate follow-up ADR work covers complete per-step result JSON, DAG-style
failure recovery, compensation workflows, and fully in-process API task running.

## Architecture

The Pipeline module gains a core orchestration layer that deals in explicit
objects:

- `StepPolicy`
- `StepResult`
- `PipelineRunResult`
- `PipelineRunStatus`
- `PipelineStepStatus`

Execution mechanisms are Adapters. Phase 1 uses `SubprocessStepRunner` for all
steps. Phase 2 adds an in-process Spider runner.

Spider runtime becomes the authority for `SpiderRunResult`. CLI code passes
`--result-json` into the runtime and continues to print compatibility stdout
markers. CLI code does not construct the result from stdout.

## Components

`javdb.pipeline.models`
: Defines `StepPolicy`, `StepResult`, `PipelineRunResult`,
  `PipelineRunStatus`, and `PipelineStepStatus`.

`javdb.spider.app.result`
: Defines `SpiderRunStats`, `SpiderRunResult`, schema metadata, JSON
  serialization, JSON parsing, and atomic write helpers.

`javdb.pipeline.result_io`
: Reads Spider/Pipeline result JSON, writes Pipeline result JSON, validates
  schema version, tolerates unknown fields, and rejects missing required keys.

`javdb.pipeline.step_runner`
: Defines `LogSink`, `ConsoleAndFileLogSink`, and `SubprocessStepRunner`.
  Phase 2 adds `InProcessSpiderStepRunner`.

`javdb.spider.app.run_service`
: Generates the authoritative `SpiderRunResult`, including best-effort partial
  results on failure.

`apps.cli.spider`
: Adds `--result-json <path>`, forwards that path to the runtime, preserves
  `SPIDER_OUTPUT_CSV`, `SPIDER_SESSION_ID`, and `SPIDER_STAT_*` stdout
  compatibility.

`apps.cli.pipeline`
: Adds `--result-json <path>` and writes `PipelineRunResult` when requested.

`apps.api.services.task_service`
: Keeps subprocess-based job execution and log streaming. When launching a
  pipeline task, it passes `--result-json <job-log-dir>/<job_id>.result.json`
  and records `result_path` in job metadata.

## Result Contracts

All result JSON payloads include:

```json
{
  "schema_version": "1.0",
  "kind": "spider_run_result",
  "generated_at": "2026-05-20T00:00:00Z"
}
```

`kind` is one of:

- `spider_run_result`
- `pipeline_run_result`

Readers must:

- tolerate unknown fields;
- reject missing required keys with clear errors;
- distinguish unknown values from real zero/empty values with nullable fields;
- treat additive fields within the same major schema version as compatible.

JSON writes must be atomic: write to a temporary file in the same directory,
flush, then replace the target path.

## SpiderRunResult

`SpiderRunResult` is a run-summary-level contract, not a full artifact registry.

Fields:

- `schema_version`
- `kind`
- `generated_at`
- `csv_path`
- `session_id`
- `dedup_csv_path`
- `stats`
- `mode`
- `url`
- `phase`
- `page_range`
- `started_at`
- `finished_at`
- `exit_code`
- `failure_reason`

`SpiderRunStats` fields:

- `pages`
- `found`
- `parsed`
- `skipped`
- `failed`
- `no_new`

On success, the result is complete and `exit_code=0`.

On failure, the runtime writes a best-effort partial result when possible.
Known fields are preserved, unknown fields remain `null`, and
`failure_reason` records the failure.

## PipelineRunResult

`PipelineRunResult` records the pipeline run and all executed steps.

Fields:

- `schema_version`
- `kind`
- `generated_at`
- `status`
- `mode`
- `url`
- `started_at`
- `finished_at`
- `exit_code`
- `failure_reason`
- `spider_result`
- `steps`

Each `StepResult` records:

- `name`
- `status`
- `required`
- `run_on_failure`
- `command`
- `started_at`
- `finished_at`
- `exit_code`
- `failure_reason`
- `result_path`

qB uploader, PikPak, email, and rclone dedup produce `StepResult` only in this
ADR. Full per-step result JSON belongs to a follow-up ADR.

## Step Policy

`StepPolicy` makes current behavior explicit:

- Spider is required.
- qB uploader is required.
- PikPak bridge remains required.
- rclone dedup remains optional/non-fatal.
- success email notification runs on successful main flow.
- failure email notification runs after required-step failure.

Required step failure makes the Pipeline fail.

Optional step failure is recorded but does not fail the Pipeline.

Failure notification success or failure never changes the final Pipeline exit
code. If the main flow failed, the Pipeline exits with code 1.

## LogSink

`LogSink` is the streaming boundary:

```python
class LogSink(Protocol):
    def write_line(self, step_name: str, line: str) -> None: ...
```

Phase 1 default implementation:

`ConsoleAndFileLogSink`
: Writes each child line to the current process stdout and to the active root
  file handler stream, preserving current Pipeline behavior and GitHub Actions
  real-time visibility.

Phase 1 must not reformat child output through `logger.info()`, because that
would alter existing log parsing and human-facing log style.

API task runner does not switch to `JobFileLogSink` in this ADR. It keeps
capturing Pipeline CLI stdout into the job log file.

## Phase 1: Result Sidecars And Pipeline Model

Phase 1 delivers:

- `SpiderRunResult` and `PipelineRunResult`;
- `--result-json` for Spider CLI;
- `--result-json` for Pipeline CLI;
- `SubprocessStepRunner`;
- `LogSink`;
- Pipeline stops parsing stdout for `csv_path` and `session_id`;
- API task runner records a pipeline result path in job metadata;
- current workflow stdout compatibility remains unchanged.

Phase 1 does not modify Daily/AdHoc/Test workflows.

## Phase 1 Bake

Phase 1 bake is a separate implementation plan and gate.

Required gate:

- Unit and integration tests pass.
- Daily workflow succeeds at least once.
- AdHoc workflow succeeds at least once.
- TestIngestion workflow succeeds at least once.
- GitHub Actions logs still stream in real time.
- GitHub Step Summary still renders correctly.
- Frontend task stream still grows in real time.
- Success result JSON exists and is readable.
- Failure path writes readable partial result JSON.
- Failure email behavior does not regress.
- Production Daily/AdHoc runs bake for 7 days on the existing cadence.
- No result JSON missing events, log streaming regressions, or failure email
  regressions occur during bake.

Phase 2 must not start before this bake gate passes.

## Phase 2: In-Process Spider Runner

Phase 2 changes the Pipeline Spider step from subprocess execution to an
in-process runner returning `SpiderRunResult`.

Requirements:

- Phase 1 bake gate has passed.
- Log streaming remains real-time.
- CLI `--result-json` remains supported.
- `SPIDER_*` stdout compatibility remains available until the final phase.
- Pipeline still writes `PipelineRunResult`.
- qB uploader, PikPak, email, and rclone dedup remain subprocess steps.

Phase 2 may introduce `InProcessSpiderStepRunner`, but it must not remove
`SubprocessStepRunner`, because other steps still use it.

## Final Phase: Workflow/API Result Consumption Cleanup

The final phase moves callers from stdout parsing to result JSON where scoped
for this ADR.

GitHub workflows:

- DailyIngestion stops grepping `SPIDER_*` stdout.
- AdHocIngestion stops grepping `SPIDER_*` stdout.
- TestIngestion stops grepping `SPIDER_OUTPUT_CSV`.
- Workflows use `--result-json <path>` plus a small JSON reader/GitHub output
  helper.
- `tee /dev/stderr` or equivalent real-time logging remains.

API task runner:

- keeps subprocess execution in this ADR;
- records `result_path` in metadata;
- may expose result metadata or summary in task payloads;
- does not move to service-level/in-process execution in this ADR.

If this final phase is too large, split it into:

- Phase 3: GitHub workflows stop grepping stdout.
- Phase 4: API task result metadata consumption.

## Follow-Up ADR Scope

The following are intentionally out of this ADR and should be captured in a
future ADR:

- every step emits its own complete result JSON;
- qB uploader, PikPak, email, and rclone dedup expose in-process service APIs;
- failure recovery becomes a DAG or compensation workflow;
- API task runner stops using subprocesses;
- API task runner uses a service-level runner and dedicated job `LogSink`.

## Testing Strategy

Unit tests:

- `SpiderRunResult` JSON read/write;
- `PipelineRunResult` JSON read/write;
- schema version validation;
- unknown field tolerance;
- missing required key errors;
- atomic writes;
- `SubprocessStepRunner` streaming line forwarding;
- `SubprocessStepRunner` timeout handling;
- exit code to `StepResult` mapping;
- `StepPolicy` required/optional behavior;
- `run_on_failure` behavior;
- API task metadata includes `result_path`;
- task stream response shape remains stable.

Integration tests:

- mocked Pipeline run writes `PipelineRunResult`;
- Spider success writes full `SpiderRunResult`;
- Spider failure writes partial `SpiderRunResult`;
- Pipeline reads Spider result JSON instead of stdout;
- failure notification step is recorded.

Compatibility tests:

- `SPIDER_OUTPUT_CSV` stdout still exists;
- `SPIDER_SESSION_ID` stdout still exists;
- `SPIDER_STAT_*` stdout still exists;
- existing workflow grep logic is not changed in Phase 1.

Regression checks:

- `extract_csv_path_from_output()` and `extract_session_id_from_output()` are
  removed or no longer used by Pipeline core after Phase 1.
- No Pipeline core test relies on `SPIDER_*` stdout parsing after Phase 1.

## Documentation Strategy

Create:

- `docs/design/adr/ADR-012-pipeline-run-structured-boundary.md`
- `docs/design/adr/ADR-012-pipeline-run-structured-boundary.zh.md`
- `docs/design/impl/IMP-019-pipeline-run-phase1-result-sidecar.md`
- `docs/design/impl/IMP-020-pipeline-run-phase1-bake.md`
- `docs/design/impl/IMP-021-pipeline-run-phase2-in-process-spider.md`
- `docs/design/impl/IMP-022-pipeline-run-final-result-consumption.md`

Update:

- `javdb/pipeline/README.md`;
- `docs/handbook/en/ops/logging.md`;
- `docs/handbook/zh/ops/logging.md`;
- root README and wiki only if user-facing CLI/result behavior changes.

## Open Questions Resolved

- Use staged convergence.
- Preserve real-time GitHub Actions logs.
- Preserve frontend task streaming.
- Put workflow stdout-grep removal in the final phase.
- Split the final phase if it is too large.
- Spider runtime/service writes `SpiderRunResult`.
- Use run-summary-level result fields, not a full artifact registry.
- Write best-effort partial result JSON on failure.
- Add `PipelineRunResult` with step-level results.
- Put complete per-step result JSON in a future ADR.
- Use explicit `StepPolicy` / `StepResult`.
- Put DAG/compensation failure redesign in the same future ADR as per-step
  result JSON.
- Use `SubprocessStepRunner` in Phase 1.
- Add `LogSink`.
- Add `--result-json` to both Spider and Pipeline CLI.
- Add lightweight schema version metadata.
- Do not change workflows in Phase 1.
- API task runner records result metadata but keeps subprocess execution.
- qB uploader, PikPak, and email stay subprocess steps in this ADR.
- Failure notification behavior stays the same but is recorded as a step.
- Phase 2 is in this ADR but gated by Phase 1 bake.
- Use one ADR plus four IMPs.
