# ADR-038：智能体操作台 MCP 面

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;执行下放给各期 IMP                                    |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) |

> 源自 2026-05-29 一次关于全新方向(方向四——智能体操作台)的头脑风暴。

## 背景 (Context)

系统通过 Vue 控制台 + REST API 和 CLI 操作。**没有对话式/agent 接口**:要回答"为什么昨晚那次 run 只找到三部?",运维者得手工把 workflow 结果、会话生命周期、D1 漂移、邮件摘要、runbook 页拼起来——[ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md) 加了只读 AI 诊断,但只是单个端点,而非 agent 能探索的面。

两个事实让 MCP 面既便宜又当时:

1. **service 层已分层良好。** `apps/api/services/`（`task_service`、`spider_jobs`、`explore_service`、sessions、`system_service`、`config_service`…）被 FastAPI router 和 CLI 同时 adapt。一个新的 **MCP adapter** 是对*同一套* service 的第三个 adapter——正是 [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) 的"一套 service、多个 adapter"形态。目前没有任何 MCP server（干净起点）。
2. **本会话刚建好 agent 想读的数据。** 事件脊柱（[ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)）、incidents（[ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)）、获取结果（[ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)）、漂移（[ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)）正是让 agent 能回答"这次 run 发生了什么"的那个面。

本 ADR 把系统**经 MCP 暴露成对话式 agent 面**,从**只读**起步（镜像 ADR-026 刻意的只读优先），gated 动作推迟到后期。

## 决策 (Decision)

建 `apps/mcp/`:一个 **Python FastMCP** server,作为对现有 `apps/api/services/` 层的薄**第三 adapter**。Phase 1 经 stdio 暴露**只读**工具（观测 + 诊断）;有副作用的动作推迟到 Phase 2,藏在显式的 dry-run + confirm + 审计门后。

### 设计决策 (Design Decisions)

**D1. MCP server 是 service 层之上的第三 adapter——非重新实现。** 每个 tool 是对既有 service/repo 函数的薄调用;API router 与 MCP tool 调**同一套** service。若某读取无 service 函数,就加到 **service 层**（API + MCP 共享），绝不内联进 MCP adapter。这守住 [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) 边界:一套 service,三个 adapter（CLI / API / MCP）。

```
apps/mcp/
  server.py     # FastMCP 实例 + tool 注册
  tools/        # 每组 tool 一个薄模块;各调一个 service/repo
  context.py    # 复用 apps/api/services 的 runtime/context
```

**D2. Python FastMCP,Phase 1 用 stdio 传输。** FastMCP 直接复用 Python service 层,给出完整本地能力。Phase 1 交付 **stdio**,operator 接到本地 agent（如 Claude）;远程 HTTP/SSE 传输留后期。

**D3. Phase 1 只读:观测 + 诊断。** 工具分类:

| 工具 | 背后 | 答什么 |
| --- | --- | --- |
| `list_runs` / `get_run` | `task_service` / jobs | 最近 run、状态 |
| `list_sessions` / `get_session` | sessions service | 会话状态 / 生命周期 |
| `search_history` | `explore_service` / history | "我有没有 X?" |
| `get_run_timeline` / `query_events` | `PipelineEvent`（ADR-036） | "这次 run 发生了什么?" |
| `list_incidents` / `get_incident` | `OpsIncidents`（ADR-026） | 运维事件 |
| `diagnose_run` | ADR-026 诊断（只读） | "为什么失败?" |
| `get_acquisition_outcomes` | `AcquisitionOutcome`（ADR-033） | 种子落地 / 卡住 |
| `get_drift` | `site_drift` / `ParseFieldHealth`（ADR-035） | 解析漂移 |
| `get_capabilities` | capabilities | 部署能力 |

组合 `query_events` + `list_incidents` + `get_acquisition_outcomes`,agent 一轮即可回答跨源运维问题。

**D4. gated 动作推迟到 Phase 2,且现在就把门规定好。** 一个有副作用的 tool（`trigger_run`、`rollback_session`、`commit_session`）必须:(1) 返回"将要做什么"的 **dry-run 预览**;(2) 要求显式 `confirm=true` 二次调用才执行;(3) 复用现有 auth;(4) 每次执行写一条**审计事件**（`PipelineEvent` / `OpsIncident`）。这镜像 ADR-026 的只读 → 受控修复递进。

**D5. 安全:只读、脱敏、无 secrets。** Phase 1 工具从不 mutate;敏感值经现有 masking 脱敏;**`config.py`/secrets 绝不暴露成 tool**。本地 stdio 假定 operator 可信;远程传输（后期）在传输层加 auth。

**D6. Phase 1 无 TypeScript Worker MCP。** 一个平行的 Cloudflare Worker MCP（ADR-017 后端分工的"双-MCP"镜像）是后期可选阶段;Phase 1 只有 Python adapter。

## 后果 (Consequences)

### 正面 (Positive)

- **对话式运维面**——直接问系统,而非拼控制台页面。
- **便宜——薄 adapter**——按 ADR-015 复用现有 service 层,几乎无新逻辑。
- **暴露本会话产出**——事件脊柱、incidents、outcomes、漂移第一天即可被 agent 查询。
- **天生安全**——Phase 1 只读;动作是单独 gated 的阶段。

### 负面 (Negative)

- **多一个要同步的 adapter**——service 签名变时,MCP tool（与 API router 一样）须跟随。
- **service 缺口压力**——某些读取可能缺 service 函数,需补一个（对 API 也是好事,但有前期工作）。
- **信任模型依传输而定**——stdio 假定本地 operator 可信;远程用需后期 auth 传输。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — 只读面 | [IMP-ADR038-01](IMP-ADR038-01-readonly-mcp.md) | `apps/mcp/` FastMCP server（stdio）;上表只读工具;复用 ADR-026 的 `diagnose_run` | mutate 动作;远程传输;TS Worker MCP |
| Phase 2 — gated 动作 | IMP-ADR038-02（占位） | `trigger_run` / `rollback_session` / `commit_session`,藏在 dry-run + confirm + auth + 审计事件后 | — |
| Phase 3 — 远程 / 双 MCP（可选） | IMP-ADR038-03（占位） | HTTP/SSE 传输;平行 TS Worker MCP | — |

Phase 1 独立成立（只读、附加）。Phase 2 加 gated mutate 面。Phase 3 是可选的远程/serverless 触达。

### 明确的非目标 (YAGNI)

- **Phase 1 无 mutation**——只观测 + 诊断。
- **Phase 1 无远程传输**——仅 stdio。
- **无 TS Worker MCP**——双-MCP 镜像是 Phase 3。
- **无 secrets/config 工具**——绝不暴露。
- **不在 MCP adapter 里重写 service 逻辑**（D1）。

## 领域语言 (CONTEXT.md 待补充项)

- **MCP adapter**——`apps/mcp/` 把 service 层暴露成 MCP 工具的面,CLI、API 之外的第三 adapter。
- **Read-only tool（只读工具）**——只查询的 MCP 工具;Phase 1 的全部。
- **Gated action（受控动作）**——由 dry-run 预览 + 显式 confirm + auth + 审计事件守护的有副作用 MCP 工具（Phase 2）。

## 备选方案 (Alternatives Considered)

- **先做 TypeScript Worker MCP**——否决（D2/D6）:丰富的 Python service 层是最便宜的复用且给出完整本地能力;Worker MCP 是后期可选镜像。
- **Phase 1 就读 + 动作**——否决（D3/D4）:mutate 工具能触发或回滚生产;它们需要刻意的 dry-run/confirm/审计门与单独阶段,正如 ADR-026 把只读排在受控修复之前。
- **在 MCP adapter 里重写查询**——否决（D1）:违反 ADR-015 并把逻辑从 API 分叉出去。

## 参考 (References)

- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-026 — AI Operations Diagnosis](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed(伞型;三期已划定,IMP 待出)。
