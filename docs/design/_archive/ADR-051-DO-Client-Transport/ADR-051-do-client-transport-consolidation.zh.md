# ADR-051：让 `ProxyCoordinatorClient.lease/report` 走 DO-client seam，并给异步上报队列定类型

| 字段       | 值                                                                 |
| ---------- | ----------------------------------------------------------------- |
| **状态**   | Completed（2026-06-14）——已由 [IMP-ADR051-01](IMP-ADR051-01-do-client-transport.md) 实现 |
| **日期**   | 2026-06-13                                                        |
| **作者**   | Ted                                                              |
| **关联**   | [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md)（拥有 `/recommend_proxy` **打分**；D19 要求 `/lease` 保持简单确定——本 ADR 守住它）、[ADR-013](../ADR-013-Runner-Runtime-State/ADR-013-runner-runtime-state-consolidation.md)（调用 `report_async` 的运行时状态）、[ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)（`ProxyPool` 是 Rust-Required，但 DO-client HTTP 层是纯 Python，不受影响） |

> 源自 2026-06-13 架构评审（候选 4 ——"让 `ProxyCoordinatorClient` 走 `_do_request`"）：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)。

## 背景（Context）

`BaseDOClient._do_request(method, path, body)`（`javdb/proxy/coordinator/do_client_base.py`）是拥有全部 Cloudflare Durable Object HTTP 传输的深 seam：`Timeout`/`ConnectionError` → `CoordinatorUnavailable`、非 2xx → `CoordinatorUnavailable`、非法 JSON → `CoordinatorUnavailable`，**外加**一个 `isinstance(parsed, dict)` 守卫。每个兄弟 client 都走它—— `MovieClaimClient`（9 方法）、`RunnerRegistryClient`（4）、`LoginStateClient`（6）、`WorkDistributorClient`（5）。

两个例外是 `ProxyCoordinatorClient.lease()` 和 `report()`（`proxy_coordinator_client.py`，745 行）——**整个 DO-client 子系统最高频的两个调用点**——它们**内联**了同一套传输块，在最热的调用方绕过 seam，且缺少 `_do_request` 提供的 `isinstance(parsed, dict)` 守卫。seam 恰恰在最要紧处近乎零 leverage。

另外，`_async_report_loop` 带着**死的**变长 tuple 解包守卫（`len(item) > 2 / > 3 / > 4`），其向后兼容目标——2- 与 4-tuple 推送点——在生产与测试中均已不存在。异步上报队列携带无类型、变长的 tuple 加一个裸 tuple 关闭 sentinel。

删除测试：删 `lease`/`report` 不是办法——它们有真实后处理。但它们的*传输块*是 `_do_request` 的副本；删掉这个副本能把 HTTP 错误契约收于一处。死 tuple-compat 是通往虚无的纯 pass-through——它干净地删除。

## 决策（Decision）

把两个例外接入 seam，并把无类型异步队列换成类型化事件。

### 设计决策（Design Decisions）

**D1. 让 `lease()` 与 `report()` 走 `self._do_request('POST', path, body)`。** 调用后只在返回的 dict 上做方法专属后处理—— `lease`：health-cache 写入、`ProxyHealthSnapshot` 构造、`banned_until`/`cf_bypass_until` 解析、`LeaseResult`；`report`：`penalty_factor`/`recent_event_count` 提取、`ReportResult`。两者继承 `_do_request` 的统一错误处理，**包括目前缺失的 `isinstance(parsed, dict)` 守卫**——这是正确性收益，不止是去重。

**D2. `_do_request` 之后内联解析——不抽 `_parse_lease_response`/`_parse_report_response` helper。** `MovieClaimClient` 与 `RunnerRegistryClient` 都在 `_do_request` 后内联解析、无私有 parse helper；与之一致使子系统统一，且现有 `patch(c._session.post)` 测试已端到端走过解析路径。

**D3. 用 frozen `AsyncReportEvent` dataclass 取代变长队列 tuple。** 模块级置于 `proxy_coordinator_client.py`（字段：`proxy_id`、`kind`、`ttl_ms`、`reason`、`latency_ms`）。`report_async()` 公开签名不变；只有内部队列项类型变，故四个调用点（`sleep.py`、`state.py`、`context.py`、`fetch_engine.py`）不动。**不**提升到 `BaseDOClient`——只有 `ProxyCoordinatorClient` 有异步分发，现在泛化会加无用泛化（一个 adapter ≠ 一个 seam）。

**D4. 关闭 sentinel 变成类型化 `ASYNC_QUEUE_SENTINEL` 常量；坍缩死解包。** 模块级 `ASYNC_QUEUE_SENTINEL = AsyncReportEvent(...)` 使队列同质（无 `Union`）；`_async_report_loop` 检查 `item is ASYNC_QUEUE_SENTINEL` 后访问命名字段。删除 `len(item) > 2/3/4` 兼容分支——其推送点已消失。

**D5. 公开 API 不变。** `report_async()`、`lease()`、`report()`、`LeaseResult`、`ReportResult` 签名不变；外部调用点与队列生产者接口不动。这守住 [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md) D19（"保持 `/lease` 与请求热路径简单确定"）——外部契约逐字节相同；只有内部传输路由与队列类型改变。

## 后果（Consequences）

### 正面（Positive）

- **leverage** —— `BaseDOClient` 的深度终于抵达其两个最热调用方；HTTP 错误契约为所有 DO client 住于一处。
- **locality** —— `lease`/`report` 各减约 40 行重复传输、只剩响应字段提取；异步分发契约是一个类型化 dataclass，而非隐式 tuple 形状。
- **一处潜在缺口闭合** —— `lease`/`report` 获得目前缺失的 `isinstance(parsed, dict)` 守卫。
- **死代码删除** —— `len(item) > 2/3/4` tuple-compat 分支消失（删除测试：无物重现；推送点已没）。
- **测试命中单一 seam** —— mock `_do_request` 而非 `_session.post`；`AsyncReportEvent` 构造是单一契约点。

### 负面（Negative）

- **新增一个模块级 dataclass + sentinel 常量。** 微不足道；它取代一个隐式、无文档的 tuple 形状。

### 风险（Risks）

- **路由后的传输行为漂移。** 缓解：`_do_request` 已编码与内联块完全相同的 `CoordinatorUnavailable` 映射，*外加* dict 守卫；现有 `patch(c._session.post)` 测试仍在同一点拦截并保持绿，新测试覆盖 dict-守卫路径。
- **漏掉某个异步队列推送点仍入 tuple。** 缓解：IMP 任务枚举每个 `report_async` 与队列 `put` 点；类型注解 `Queue[AsyncReportEvent]` 让游离 tuple 成为类型错误。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1（唯一） | [IMP-ADR051-01](IMP-ADR051-01-do-client-transport.md) | `lease`/`report` 走 `_do_request`；`AsyncReportEvent` + 类型化 sentinel；删死 tuple-compat；加 dict-守卫测试 | — |

### 明确的非目标（YAGNI）

- **不**把异步分发提升进 `BaseDOClient`（D3）——一个 adapter、无第二处使用。
- **不**碰 `/recommend_proxy` 打分（ADR-023）——只动 `/lease` 与 `/report` 传输。
- **不**改任何公开签名或外部调用点（D5）。

## 领域语言（CONTEXT.md 增补）

- **DO-client seam（`BaseDOClient._do_request`）** —— 为 coordinator client 拥有全部 Cloudflare Durable Object HTTP 传输的单一方法（timeout/connection/非-2xx/非法-JSON → `CoordinatorUnavailable`，外加 `isinstance(dict)` 守卫）。所有 DO client 走它；ADR-051 把 `ProxyCoordinatorClient.lease`/`report`——最后两个例外——接上来。
- **AsyncReportEvent** —— frozen dataclass（`proxy_id`、`kind`、`ttl_ms`、`reason`、`latency_ms`），取代 `report_async()` 入队的变长 tuple；关闭 sentinel 是该类型的类型化 `ASYNC_QUEUE_SENTINEL`。

## 备选方案（Alternatives Considered）

- **把 `AsyncReportEvent`/异步分发提升到 `BaseDOClient`。** 否决（D3）：只有 `ProxyCoordinatorClient` 异步分发；一个 adapter 不构成 seam。
- **抽 `_parse_lease_response`/`_parse_report_response` helper。** 否决（D2）：兄弟 client 都在 `_do_request` 后内联解析；一致性胜过边际可测性。
- **保留裸 tuple sentinel，解包前 `isinstance` 检查。** 否决（D4）：保留混合类型队列，违背 dataclass 替换目的。

## 参考（References）

- [ADR-023 — Proxy Recommendation Policy](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md)
- [ADR-013 — Runner Runtime State](../ADR-013-Runner-Runtime-State/ADR-013-runner-runtime-state-consolidation.md)
- [ADR-041 — Rust Core Fallback Policy](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)
- 2026-06-13 架构评审：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## 状态日志（Status Log）

- 2026-06-14：Completed 于 [IMP-ADR051-01](IMP-ADR051-01-do-client-transport.md)。计划中的单一阶段已交付 `lease`/`report` 走 `_do_request`、类型化 `AsyncReportEvent` 队列项与 sentinel、删除死 tuple-compat 代码，以及聚焦的 dict-guard/异步队列回归测试。本 ADR 不再有后续 IMP。
- 2026-06-13：Proposed（源自 2026-06-13 架构评审候选 4）。决定：`lease`/`report` 走 `_do_request`（获得 `isinstance(dict)` 守卫）；内联后处理（与兄弟一致）；`AsyncReportEvent` frozen dataclass + 类型化 `ASYNC_QUEUE_SENTINEL` 置于 `proxy_coordinator_client.py` 模块级（不提升到 base）；删除死的 `len(item) > 2/3/4` tuple-compat；公开 API 不变。核实：恰 745 行；四个兄弟 client 均已走 `_do_request`；tuple-compat 推送点已消失。IMP-ADR051-01 待办。
