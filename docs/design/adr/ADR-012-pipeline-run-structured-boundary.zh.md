# ADR-012：Pipeline Run 结构化边界

**状态**：已接受 - 实现待启动
**日期**：2026-05-20
**决策者**：Pipeline Run module brainstorming + grill 会话
**关联实现计划 (Related Implementation Plans)**：[IMP-ADR012-01](../impl/IMP-ADR012-01-pipeline-run-phase1-result-sidecar.md)（Phase 1 - result sidecar）、[IMP-ADR012-02](../impl/IMP-ADR012-02-pipeline-run-phase1-bake.md)（Phase 1 bake）、[IMP-ADR012-03](../impl/IMP-ADR012-03-pipeline-run-phase2-in-process-spider.md)（Phase 2 - in-process Spider）、[IMP-ADR012-04](../impl/IMP-ADR012-04-pipeline-run-final-result-consumption.md)（Final phase - result consumption cleanup）

## 待办 (Outstanding Work)

- Phase 1 - 结构化 Spider/Pipeline result sidecar 和 Pipeline step model。
- Phase 1 bake - 启动 in-process Spider 前必须完成 7 天生产 bake gate。
- Phase 2 - bake 通过后，Pipeline 通过 in-process runner 调 Spider。
- Final phase - GitHub workflows 和轻量 API task metadata 改用 result JSON，不再 grep stdout。

---

## 背景

`javdb.pipeline.service` 当前通过启动 CLI subprocess 来编排长运行 pipeline。
Spider step 会捕获 child stdout，并解析 `SPIDER_OUTPUT_CSV=`、
`SPIDER_SESSION_ID=` 等行，把数据传给后续 step。

这混合了两个不同 contract：

- log streaming：GitHub Actions 和 web UI 必须实时看到日志；
- structured run results：不应从 stdout 文本推断结构化结果。

当前 GitHub Actions workflows 也会解析 `SPIDER_*` stdout 行来写
`GITHUB_OUTPUT` 和 step summary。API task runner 会以 subprocess 启动
pipeline/spider CLI，并把 child stdout/stderr 直接写入 job log 文件；前端按
byte offset 轮询这些日志。

## 不可协商运行时不变量

本次重构不得降低实时日志能力。

GitHub Actions 必须继续在进程运行期间显示 pipeline 和 spider 日志。Frontend task
stream 必须继续在 job 运行期间增量增长。任何只能在进程退出后展示日志的
structured-result 迁移都违反本 ADR。

## 决策

### D1. 拆分 Log Stream 与 Structured Results

Pipeline runtime 使用两个显式通道：

| Channel | Audience | Contract |
|---|---|---|
| Log stream | Humans、GitHub Actions、frontend task stream | 实时行输出到 stdout、log files 和 job log files。 |
| Structured result | Pipeline/API/workflow adapters | Versioned JSON 或 in-process result objects。 |

Pipeline core 不得从 human log lines 解析业务数据。

### D2. Versioned Result Contracts

Spider 和 Pipeline run 暴露 versioned result JSON。每个 payload 都包含：

```json
{
  "schema_version": "1.0",
  "kind": "spider_run_result",
  "generated_at": "2026-05-20T00:00:00Z"
}
```

`kind` 为：

- `spider_run_result`
- `pipeline_run_result`

Reader 必须 tolerate unknown fields，遇到 required key 缺失时报明确错误，并通过
nullable fields 区分 unknown value 和真实 zero/empty value。

### D3. SpiderRunResult

`SpiderRunResult` 是 run-summary contract，不是完整 artifact registry。

字段：

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

`SpiderRunStats` 字段：

- `pages`
- `found`
- `parsed`
- `skipped`
- `failed`
- `no_new`

Spider runtime 是该 result 的权威来源，CLI glue 不是。

### D4. Best-Effort Failure Result

Spider 和 Pipeline 在失败时只要已知足够信息，就写 best-effort partial result JSON。

已知字段保留，未知字段为 `null`。`exit_code` 和 `failure_reason` 标记失败。
JSON 写入必须 atomic：在同目录写临时文件、flush，然后 replace 目标路径。

### D5. PipelineRunResult And StepResult

Pipeline 记录 run 和所有已执行 step。

`PipelineRunResult` 字段：

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

每个 `StepResult` 记录：

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

qB uploader、PikPak、email 和 rclone dedup 在本 ADR 中只产生 `StepResult`。
它们自己的完整 result JSON 属于后续 ADR。

### D6. Explicit StepPolicy

Pipeline step 行为显式化：

- Spider required。
- qB uploader required。
- PikPak bridge 继续 required。
- rclone dedup 继续 optional/non-fatal。
- success email notification 在主流程成功时运行。
- failure email notification 在 required-step 失败后运行。

Required step 失败会使 Pipeline 失败。Optional step 失败只记录，不使 Pipeline
失败。Failure notification 成功或失败都不改变最终 Pipeline exit code；只要主流程
失败，Pipeline exit code 就是 1。

### D7. Phase 1 使用 SubprocessStepRunner Adapter

Phase 1 保留 subprocess execution，以保留 process isolation 和当前 log streaming
行为，但用 `SubprocessStepRunner` Adapter 包起来。

Pipeline core 只看 `StepResult`，不接触 raw subprocess details。

Spider step 传 `--result-json <path>` 给 Spider CLI。Pipeline 读取该 sidecar，
不解析 stdout。

### D8. LogSink

`LogSink` 是 streaming boundary：

```python
class LogSink(Protocol):
    def write_line(self, step_name: str, line: str) -> None:
        pass
```

Phase 1 默认实现是 `ConsoleAndFileLogSink`。它把 child output 写入当前 process
stdout 和 active root file handler stream，不通过 `logger.info()` 重新格式化 child
output。

### D9. CLI Result JSON

两个 canonical CLI 都支持 caller-provided result path：

```text
python -m apps.cli.spider --result-json <path>
python -m apps.cli.pipeline --result-json <path>
```

路径由 caller 管理，避免并发 run 覆盖。

`SPIDER_*` stdout 行保留为 compatibility output，直到 final phase。

### D10. API Task Runner Scope

本 ADR 对 API task runner 只做轻量改动：

- pipeline job 获得 job-specific `--result-json <job-log-dir>/<job_id>.result.json`；
- job metadata 记录 `result_path`；
- 现有 subprocess execution 和 frontend log streaming 不变。

API task runner 脱离 subprocess 属于后续 ADR。

### D11. Phase 1 Bake Gate

Phase 2 在 Phase 1 bake 通过前不得启动。

Bake gate 要求：

- unit 和 integration tests 通过；
- Daily workflow 至少成功一次；
- AdHoc workflow 至少成功一次；
- TestIngestion workflow 至少成功一次；
- GitHub Actions logs 仍实时 streaming；
- GitHub Step Summary 仍正常渲染；
- frontend task stream 仍实时增长；
- success result JSON 存在且可读；
- failure path 写出可读 partial result JSON；
- failure email 行为无 regression；
- production Daily/AdHoc 按现有 cadence bake 7 天；
- bake 期间无 result JSON missing、log streaming regression 或 failure email
  regression。

### D12. Phase 2 In-Process Spider Runner

Bake gate 通过后，Pipeline 用返回 `SpiderRunResult` 的 in-process runner 替换
Spider subprocess。

Phase 2 必须保留实时 log streaming、CLI result JSON 支持和 `SPIDER_*`
compatibility output。qB uploader、PikPak、email 和 rclone dedup 在本 ADR 内继续
是 subprocess steps。

### D13. Final Phase Result Consumption Cleanup

Final phase 把本 ADR 范围内的 consumer 从 stdout parsing 迁到 result JSON。

GitHub workflows：

- DailyIngestion 不再 grep `SPIDER_*` stdout。
- AdHocIngestion 不再 grep `SPIDER_*` stdout。
- TestIngestion 不再 grep `SPIDER_OUTPUT_CSV`。
- Workflows 使用 `--result-json <path>` 加小型 JSON reader/GitHub output helper。
- 保留 `tee /dev/stderr` 或等价实时 logging。

API task runner：

- 本 ADR 内继续 subprocess execution；
- metadata 记录 `result_path`；
- 可在 task payload 中暴露 result metadata 或 summary。

如果 final phase 过大，就拆成 workflow phase 和 API task result metadata phase。

## Follow-Up ADR Scope

以下内容有意排除在本 ADR 之外：

- 每个 step 都输出自己的完整 result JSON；
- qB uploader、PikPak、email 和 rclone dedup 暴露 in-process service API；
- failure recovery 变成 DAG 或 compensation workflow；
- API task runner 停止使用 subprocess；
- API task runner 使用 service-level runner 和专用 job `LogSink`。

这些内容应进入后续 ADR，而不是扩进 ADR-012。

## 后果

### 正向

1. Pipeline 不再把 human log text 当内部数据协议。
2. 保留 GitHub Actions 和 frontend 的实时日志能力。
3. Failure path 可以通过 partial structured result 暴露数据。
4. Phase 1 bake 后再启动风险更高的 in-process Spider work。
5. 后续架构工作有明确边界。

### 负向

1. Phase 1 仍保留 Spider subprocess，因此不会立刻达到最终目标。
2. Result JSON 引入新的 compatibility contract，必须测试和版本化。
3. Bake 至少会按生产 cadence 延迟 Phase 2 7 天。

### 风险

1. **Result JSON 已存在，但 caller 永远继续解析 stdout。**
   - **缓解**：final phase 负责 workflow/API result consumption cleanup。
2. **引入 structured results 时 log streaming 退化。**
   - **缓解**：实时 log 行为是 invariant 和 bake gate。
3. **Partial failure results 把 unknown data 伪装成 zero。**
   - **缓解**：nullable fields 区分 unknown 和真实 zero。
4. **Phase 2 in-process runner 改变 Spider global-state cleanup。**
   - **缓解**：Phase 2 必须先通过 Phase 1 bake，并为 session cleanup、DB
     connection cleanup、logging、`SystemExit` mapping 写专门测试。

## References

- [IMP-ADR012-01](../impl/IMP-ADR012-01-pipeline-run-phase1-result-sidecar.md)
- [IMP-ADR012-02](../impl/IMP-ADR012-02-pipeline-run-phase1-bake.md)
- [IMP-ADR012-03](../impl/IMP-ADR012-03-pipeline-run-phase2-in-process-spider.md)
- [IMP-ADR012-04](../impl/IMP-ADR012-04-pipeline-run-final-result-consumption.md)
- [Logging handbook](../../handbook/zh/ops/logging.md)
