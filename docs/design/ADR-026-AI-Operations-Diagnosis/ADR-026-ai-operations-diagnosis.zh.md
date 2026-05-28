# ADR-026：AI 运维诊断助手

| 字段        | 值                                                                    |
| ----------- | --------------------------------------------------------------------- |
| **状态**    | Accepted - Phase 1 已交付；Phase 2-3 待执行                          |
| **日期**    | 2026-05-27                                                            |
| **作者**    | Ted                                                                   |
| **关联**    | [ADR-009](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md), [ADR-010](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-015](../ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) |

## Related

- [IMP-ADR026-01](IMP-ADR026-01-ai-ops-diagnosis-readonly.md)
- [IMP-ADR026-02](IMP-ADR026-02-ai-ops-diagnosis-history-analytics.md)
- [IMP-ADR026-03](IMP-ADR026-03-ai-ops-diagnosis-gated-remediation.md)
- [ADR-009](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-010](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-015](../ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)

## 背景

仓库里已经有几类运维信号和修复工具：

- ADR-009 提供了只读的 drift 诊断和受控 apply，用来处理 D1/SQLite
  pending-write drift。
- ADR-010 提供了 D1 access port、recovery outbox 和 startup replay。
- ADR-015 把 notify/email 与 CLI 边界拆得更明确。
- 现有 API 已经暴露了一个很小的 JavDB session diagnostics 面。
- email 路径已经会输出短的运维告警摘要。

这些能力都很有用，但仍然更像单点工具。一次失败发生时，操作者往往还是要自己拼接多处证据：workflow 结果、session 生命周期、D1 drift、rollback 是否安全、email 摘要、以及相关 runbook 页面。同一个 incident 还可能因为场景不同而需要不同处理：比如 ingestion 失败、session 过期、pending orphan、recovery outbox dead-letter，或者一个不安全的 rollback 候选。

这个 ADR 定义一个 AI 辅助的诊断层，但边界必须站稳：先确定性地收集证据，再让模型做归纳、排序可能原因、列出未知项和下一步动作。第一版不能自动修复任何东西。

## 决策

创建一个 D1 为准、可解释、只读的 AI 运维诊断助手。Phase 1 先把 incident 证据组装成结构化 bundle，再走 detector + model 的两段式诊断流程，持久化 incident 记录，并通过 CLI、API 和短 email 摘要暴露结果。

ADR-026 在 Phase 1 只做 advisory，不自动 rollback，不自动重跑 workflow，不自动改 D1，不自动删除 qBittorrent 任务，也不自动改任何运行时状态。

### 设计决策

D1. **默认只读** - 助手可以解释和建议，但 Phase 1 不得直接修改 D1、重跑 workflow、回滚 session，或删除 qBittorrent 任务。

D2. **先证据，后模型** - 原始日志不会直接喂给模型。先由确定性收集器构建紧凑的 incident bundle，模型只看到被整理过的事实与引用。

D3. **D1 是真源** - incident 记录、诊断元数据和状态更新优先写入 D1。SQLite 只做本地调试镜像，不是权威。

D4. **必须结构化输出** - 每次诊断都要输出 `confirmed_findings`、`likely_causes`、`unknowns`、
`recommended_next_actions`、`unsafe_actions` 和 `evidence_refs`，不能只返回一段黑盒答案。

D5. **detector 负责事实，模型负责综合** - 确定性规则和轻量分类器负责提取候选事实；模型负责归纳和解释，但不能凭空创造 bundle 里没有的证据。

D6. **证据不全就 fail closed** - 如果 bundle 缺少关键数据，助手必须把缺口标成 `unknown`，不能硬猜。安全回退就是人工复核。

D7. **存结构化摘要，不存原始日志** - incident 里只保存结构化摘要和证据指针，不保存整份原始日志。bundle 只保留重建诊断所需的最小内容。

D8. **email 走短摘要，UI/API 走详细内容** - email 只放简短摘要和诊断记录链接或指针，详细推理留给 CLI 和 API。

D9. **Phase 1 有边界** - 第一版覆盖 DailyIngestion、AdHocIngestion、TestIngestion、session 生命周期异常、D1/SQLite drift、pending orphan、recovery outbox/dead-letter、rollback 安全性，以及 qBittorrent 外部副作用检查。它不覆盖 qB file filter 深度诊断、PikPak/Rclone 深度诊断、parser 自动修复、前端性能、或 proxy bandit 调参。

D10. **安全优先于自动化** - 后续阶段可以加审批流或门控式修复建议，但 Phase 1 明确不做自动修复。

### Incident Bundle

收集器会从确定性输入里组装一个紧凑的 `incident_bundle`：

- 触发来源：手动 CLI、workflow failure，或 operator 的定向操作；
- `run_id`、`run_attempt`、`session_id` 和时间上下文；
- workflow 状态和关键 job 结果；
- D1 drift / pending orphan 信号；
- rollback 安全信号和 session 生命周期状态；
- email 摘要片段；
- 相关日志片段和已知 verdict；
- runbook 引用和相关 ADR 指针；
- 如 incident 涉及上传，也可包含 qBittorrent 外部副作用元数据。

bundle 要足够小，既能持久化又能审阅，但也要足够丰富，能支撑一个有用的诊断。它是经过筛选的表示，不是完整日志档案。

### Detector Layer

在任何模型调用之前，assistant 先运行确定性 detectors。这些 detectors 可以：

- 识别 incident 更像是 ingestion 失败、session 过期、D1/SQLite drift、
  pending orphan，还是 recovery outbox 问题；
- 从日志或 workflow 输出中提取已知症状；
- 标记 rollback 看起来是安全、不安全，还是未知；
- 标记 qBittorrent 外部副作用是否已经发生。

detector 的范围要刻意窄。如果某个事实无法证明，它应该输出更弱的信号或 unknown，而不是硬给 verdict。

### AI Diagnosis Layer

模型读取 bundle 和 detector 输出，然后生成结构化诊断。输出必须把下面几类内容分开：

- `confirmed_findings` - bundle 能支持的事实；
- `likely_causes` - 对 incident 发生原因的最佳解释；
- `unknowns` - 阻止更高置信度判断的缺失事实；
- `recommended_next_actions` - operator 动作或 runbook 步骤；
- `unsafe_actions` - 现在不该做的动作；
- `confidence` - 对诊断的粗粒度置信度。

模型不能凭空创造新事实，不能在没有安全提示的情况下建议破坏性动作，也不能覆盖 detector 已经证明为 unknown 的状态。

### Incident Store

Phase 1 把 incident 存到 D1 的 canonical 记录集中。一个合理的记录形状是：

- `incident_id`
- `trigger_source`
- `run_id`
- `run_attempt`
- `session_id`
- `incident_type`
- `status`
- `confirmed_findings_json`
- `likely_causes_json`
- `unknowns_json`
- `recommended_next_actions_json`
- `unsafe_actions_json`
- `evidence_refs_json`
- `created_at`
- `updated_at`
- `resolved_at`

实现时表名可以演进，但核心 contract 要稳定：incident 必须是持久化、可查询、可版本化的，而且不能依赖把完整原始日志一直塞在数据库里。

如果 D1 持久化失败，assistant 仍应返回诊断结果，并记录一个降级的 persistence status，例如 `d1_failed_jsonl_written`。JSONL fallback 可以作为耐久性后备，但 D1 仍然是 canonical store。

### Entry Points

assistant 应该能从三个面进入：

1. CLI - 未来的命令，比如 `python3 -m apps.cli.ops.diagnose_run`。
2. API/Web - 结构化 incident 查询和诊断展示。
3. Email - 只发短告警，不重复完整推理。

CLI 是主要的 operator 入口。API/Web 是只读展示面。Email 只负责通知。

### Safety Boundary

Phase 1 里，assistant 永远不能做这些事：

- 自动 rollback session；
- 自动重跑 DailyIngestion、AdHocIngestion 或 TestIngestion；
- 基于诊断结果修改 D1；
- 自动删除 qBittorrent 任务；
- 没有 operator 意图就把 recovery event 标成 resolved。

如果模型不确定，就应该明确说不确定。如果 detector 说动作不安全，模型不能覆盖它。

## 后果

### 正面

- 操作者可以拿到一个统一的诊断 artifact，而不是自己手工拼接 run log、
  session 状态、drift 数据和 runbook 页面。
- 设计和仓库的 D1-first 方向一致。
- 结构化 incident 记录以后可以支持搜索、相似性检索和更安全的修复流。
- 第一版风险较低，因为只是 advisory。

### 负面

- 新的 incident 存储和诊断 plumbing 会增加运维面。
- 这个助手仍然需要仔细调 prompt 和 detectors。
- 只读设计可以给建议，但第一阶段不能去掉所有人工介入。

### 风险

- **诊断过于自信** - 缓解方式：强制结构化证据，明确 unknown，并在证据薄弱时 fail closed。
- **原始数据太多** - 缓解方式：只存摘要和引用，不存完整日志档案。
- **自动修复范围外溢** - 缓解方式：Phase 1 保持纯 advisory，任何后续修复都必须经过单独 ADR 或单独阶段门控。
- **运维工具重复** - 缓解方式：对齐 ADR-009 和 ADR-010，不再另造一套 rollback 或 drift 系统。

## 实施路线

| 阶段 | IMP | 交付 | 延后 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR026-01](IMP-ADR026-01-ai-ops-diagnosis-readonly.md)（Completed 2026-05-27） | D1 incident schema、deterministic incident bundle collector、detector layer、AI synthesis、CLI/API 只读查询、短 email 摘要、JSONL fallback | 不做自动修复 |
| Phase 2 | [IMP-ADR026-02](IMP-ADR026-02-ai-ops-diagnosis-history-analytics.md) | UI 历史浏览、确定性相似性检索、以及更丰富的 incident 分析 | 修复审批流 |
| Phase 3 | [IMP-ADR026-03](IMP-ADR026-03-ai-ops-diagnosis-gated-remediation.md) | 带人工确认和显式安全边界的门控式修复建议 | 任何全自动修复 |

## 参考

- `apps/cli/db/drift_diagnose.py` - 现有的只读 diagnose CLI 边界。
- `javdb/storage/drift_diagnose.py` - 现有的 canonical drift diagnosis service。
- `apps/api/routers/diagnostics.py` - 现有 diagnostics API 面。
- `apps/api/schemas/diagnostics.py` - 现有 diagnostics schema 形状。
- `javdb/integrations/notify/email.py` - 现有 email 告警路径。
- `docs/handbook/en/ops/d1-rollback.md` - rollback 和 pending-mode 运维 SOP。
- `docs/handbook/en/ops/troubleshooting.md` - 现有 troubleshooting 参考。

## 状态日志

- 2026-05-27：以 ADR-026 提出。
- 2026-05-27：Phase 1 已交付并验证；Phase 2-3 仍保持 proposed。
