# ADR-012: Pipeline Run Structured Boundary

**Status**: Accepted - implementation pending
**Date**: 2026-05-20
**Deciders**: Pipeline Run module brainstorming and grill session
**Related Implementation Plans**: [IMP-ADR012-01](../impl/IMP-ADR012-01-pipeline-run-phase1-result-sidecar.md) (Phase 1 - result sidecar), [IMP-ADR012-02](../impl/IMP-ADR012-02-pipeline-run-phase1-bake.md) (Phase 1 bake), [IMP-ADR012-03](../impl/IMP-ADR012-03-pipeline-run-phase2-in-process-spider.md) (Phase 2 - in-process Spider), [IMP-ADR012-04](../impl/IMP-ADR012-04-pipeline-run-final-result-consumption.md) (Final phase - result consumption cleanup)

## Outstanding Work

- Phase 1 - structured Spider/Pipeline result sidecars and Pipeline step model.
- Phase 1 bake - 7-day production bake gate before in-process Spider work starts.
- Phase 2 - Pipeline calls Spider through an in-process runner after bake.
- Final phase - GitHub workflows and light API task metadata consume result JSON instead of grepping stdout.

---

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

## Non-Negotiable Runtime Invariant

The refactor must not degrade real-time logs.

GitHub Actions must continue to show pipeline and spider logs while the process
is running. The frontend task stream must continue to grow incrementally while a
job is running. Any structured-result migration that only exposes logs after the
process exits violates this ADR.

## Decision

### D1. Split Log Stream From Structured Results

Pipeline runtime uses two explicit channels:

| Channel | Audience | Contract |
|---|---|---|
| Log stream | Humans, GitHub Actions, frontend task stream | Real-time lines written to stdout, log files, and job log files. |
| Structured result | Pipeline/API/workflow adapters | Versioned JSON or in-process result objects. |

Pipeline core must not parse human log lines for business data.

### D2. Versioned Result Contracts

Spider and Pipeline runs expose versioned result JSON. Every payload includes:

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

Readers tolerate unknown fields, reject missing required keys, and preserve the
difference between unknown values and real zero/empty values through nullable
fields.

### D3. SpiderRunResult

`SpiderRunResult` is a run-summary contract, not a complete artifact registry.

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

Spider runtime, not CLI glue, is the authority for this result.

### D4. Best-Effort Failure Result

Spider and Pipeline write best-effort partial result JSON on failure whenever
they know enough to do so.

Known fields are preserved. Unknown fields stay `null`. `exit_code` and
`failure_reason` mark the failure. JSON writes are atomic: write a temporary
file in the same directory, flush it, then replace the target.

### D5. PipelineRunResult And StepResult

Pipeline records the run and every executed step.

`PipelineRunResult` fields:

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

qB uploader, PikPak, email, and rclone dedup only produce `StepResult` in this
ADR. Their own full result JSON belongs to a follow-up ADR.

### D6. Explicit StepPolicy

Pipeline step behavior becomes explicit:

- Spider is required.
- qB uploader is required.
- PikPak bridge remains required.
- rclone dedup remains optional/non-fatal.
- success email notification runs on successful main flow.
- failure email notification runs after required-step failure.

Required step failure fails the Pipeline. Optional step failure is recorded and
does not fail the Pipeline. Failure notification success or failure never changes
the final Pipeline exit code; if the main flow failed, the Pipeline exits with
code 1.

### D7. SubprocessStepRunner Adapter In Phase 1

Phase 1 keeps subprocess execution to preserve process isolation and current log
streaming behavior, but wraps it in a `SubprocessStepRunner` Adapter.

Pipeline core sees `StepResult`, not raw subprocess details.

The Spider step passes `--result-json <path>` to the Spider CLI. Pipeline reads
that sidecar instead of parsing stdout.

### D8. LogSink

`LogSink` is the streaming boundary:

```python
class LogSink(Protocol):
    def write_line(self, step_name: str, line: str) -> None:
        pass
```

Phase 1 default implementation is `ConsoleAndFileLogSink`. It writes child
output to the current process stdout and the active root file handler stream
without reformatting child output through `logger.info()`.

### D9. CLI Result JSON

Both canonical CLIs support caller-provided result paths:

```text
python -m apps.cli.spider --result-json <path>
python -m apps.cli.pipeline --result-json <path>
```

The caller owns the path, avoiding concurrent-run overwrite hazards.

`SPIDER_*` stdout lines remain compatibility output until the final phase.

### D10. API Task Runner Scope

This ADR makes only a lightweight API task runner change:

- pipeline jobs receive a job-specific `--result-json <job-log-dir>/<job_id>.result.json`;
- job metadata records `result_path`;
- existing subprocess execution and frontend log streaming remain unchanged.

Moving the API task runner off subprocesses is follow-up ADR scope.

### D11. Phase 1 Bake Gate

Phase 2 must not start until Phase 1 bakes successfully.

The bake gate requires:

- unit and integration tests pass;
- Daily workflow succeeds at least once;
- AdHoc workflow succeeds at least once;
- TestIngestion workflow succeeds at least once;
- GitHub Actions logs still stream in real time;
- GitHub Step Summary still renders correctly;
- frontend task stream still grows in real time;
- success result JSON exists and is readable;
- failure path writes readable partial result JSON;
- failure email behavior does not regress;
- production Daily/AdHoc runs bake for 7 days on the existing cadence;
- no result JSON missing events, log streaming regressions, or failure email
  regressions occur during bake.

### D12. Phase 2 In-Process Spider Runner

After the bake gate passes, Pipeline replaces the Spider subprocess with an
in-process runner returning `SpiderRunResult`.

Phase 2 must preserve real-time log streaming, CLI result JSON support, and
`SPIDER_*` compatibility output. qB uploader, PikPak, email, and rclone dedup
remain subprocess steps in this ADR.

### D13. Final Phase Result Consumption Cleanup

The final phase moves scoped consumers from stdout parsing to result JSON.

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
- may expose result metadata or summary in task payloads.

If this final phase is too large, split it into a workflow phase and an API
task result metadata phase.

## Follow-Up ADR Scope

The following are intentionally out of this ADR:

- every step emits its own complete result JSON;
- qB uploader, PikPak, email, and rclone dedup expose in-process service APIs;
- failure recovery becomes a DAG or compensation workflow;
- API task runner stops using subprocesses;
- API task runner uses a service-level runner and dedicated job `LogSink`.

These items should be captured in a follow-up ADR rather than expanded inside
ADR-012.

## Consequences

### Positive

1. Pipeline stops using human log text as an internal data protocol.
2. Real-time GitHub Actions and frontend logs are preserved.
3. Failure-path data becomes available through partial structured results.
4. Phase 1 can bake before riskier in-process Spider work starts.
5. Follow-up architecture work has explicit boundaries.

### Negative

1. Phase 1 keeps subprocess execution for the Spider step, so the final target
   is not reached immediately.
2. Result JSON introduces another compatibility contract that must be tested and
   versioned.
3. Bake delays Phase 2 for at least 7 days of production cadence.

### Risks

1. **Result JSON exists but callers still parse stdout forever.**
   - **Mitigation**: final phase owns workflow/API result consumption cleanup.
2. **Log streaming regresses while introducing structured results.**
   - **Mitigation**: real-time log behavior is an invariant and a bake gate.
3. **Partial failure results hide unknown data as zero.**
   - **Mitigation**: nullable fields distinguish unknown from actual zero.
4. **Phase 2 in-process runner changes Spider global-state cleanup.**
   - **Mitigation**: Phase 2 requires Phase 1 bake and dedicated tests for
     session cleanup, DB connection cleanup, logging, and `SystemExit` mapping.

## References

- [IMP-ADR012-01](../impl/IMP-ADR012-01-pipeline-run-phase1-result-sidecar.md)
- [IMP-ADR012-02](../impl/IMP-ADR012-02-pipeline-run-phase1-bake.md)
- [IMP-ADR012-03](../impl/IMP-ADR012-03-pipeline-run-phase2-in-process-spider.md)
- [IMP-ADR012-04](../impl/IMP-ADR012-04-pipeline-run-final-result-consumption.md)
- [Logging handbook](../../handbook/en/ops/logging.md)
