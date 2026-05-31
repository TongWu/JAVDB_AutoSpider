# ADR-042：D1 逻辑 ACID 边界与权威写入

| 字段 | 值 |
| --- | --- |
| **状态 (Status)** | Accepted — 将当前边界固化为文档：D1 传输、会话级历史、增量 enrichment 分层处理 |
| **日期 (Date)** | 2026-05-31 |
| **作者 (Authors)** | Ted |
| **关联 (Related)** | [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md), [ADR-009](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.zh.md), [ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.zh.md), [ADR-032](../ADR-032-Mandatory-Session-Binding/ADR-032-mandatory-session-binding.zh.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.zh.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.zh.md) |
| **关联实现计划 (Related Implementation Plans)** | [IMP-ADR042-01](IMP-ADR042-01-d1-logical-acid-boundaries.md) - Phase 1 docs follow-through |

> 这份 ADR 来自一次 grill 结论：需要把“D1 本身”与“权威写入边界”分开看。这个区分很重要，因为系统不需要一个分布式事务管理器，但它确实需要一个在会话层面表现得像事务一样的边界。

## 背景 (Context)

仓库里的写入职责本来就分了层：

- `javdb/storage/d1_client.py` 与 `javdb/storage/d1_port.py` 负责 HTTP 传输、重试、批处理、恢复钩子和摘要指标。
- `javdb/storage/dual_connection.py` 把写入同时镜像到 SQLite 和 D1，并在两边不一致时记录 drift。
- `javdb/storage/db/_db_history_write.py` 负责 `MovieHistory` / `TorrentHistory` 的 pending stage / commit / resume 流程。
- `javdb/storage/sessions/lifecycle.py` 统一管理 `ReportSessions.Status` 的合法迁移。
- `javdb/storage/db/_db_connection.py` 决定使用 `sqlite`、`d1` 还是 `dual`。
- `docs/handbook/en/ops/d1-rollback.md` 把 rollback / resume 定义为运维恢复，而不是分布式事务的证明。

所以原问题不能简单回答“要不要 ACID”，而要拆成三层：

1. D1 在单个 request / batch 级别能提供原子性。
2. 权威历史写入需要会话级别的 all-or-nothing 行为。
3. 增量 enrichment 和诊断不能被拉进权威边界。

如果把 ACID 用得太宽，就会暗示这里有一个实际上不存在的分布式事务；如果用得太窄，又会忽略用户真正关心的是“这次 session 的权威历史到底有没有一次性提交成功”。

## 决策 (Decision)

对权威写入使用 **会话级逻辑 ACID（session-level logical ACID）**，同时把 D1 传输、enrichment 和诊断都留在这个边界之外。

### 设计决策 (Design Decisions)

**D1. D1 传输层不是权威边界。**

D1 只需要在它真正能保证的最小边界上是原子的：单个 request 或单个 batch。`D1Connection` 和 `D1AccessPort` 保持同步、显式、请求导向，它们不是分布式事务协调器。

**D2. 权威历史写入在 session 级别是一笔逻辑事务。**

决定一个 pipeline session 成功或失败的写入，必须在用户视角上表现成一个整体。它包括：

- `ReportSessions.Status` 迁移；
- 写入 `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites`；
- 将 pending 行 drain 到 `MovieHistory` / `TorrentHistory`；
- 失败或中断 session 的 rollback / resume 行为。

这才是需要 ACID-like 语义的边界。

**D3. 增量 enrichment 不属于权威边界。**

那些可幂等、可重放、可重新计算的表和流程，可以是 D1-canonical，但不必参与 session 成败的判定。比如 enrichment 表、事件日志式写入，都应该能重试、重放或在后续重新派生，但不能决定权威 session 是否提交。

**D4. 诊断与恢复记录是运维数据，不是用户真相。**

drift log、port summary、recovery outbox 这些都对可观测性和恢复非常重要，但它们不能把一次失败的权威写变成成功。它们只是在描述发生了什么，而不是重新定义正确性。

**D5. Dual mode 是验证器，不是事务管理器。**

`DualConnection` 可以 drift、可以记录 drift、也可以 fail loud。它的任务是验证迁移和恢复期间的 parity，不是模拟 SQLite 和 D1 之间的分布式 ACID。

**D6. 新的 D1 写入必须先分类。**

所有新的 D1 写入，在设计评审时都必须被归类为以下之一：

| 类别 | 含义 | 规则 |
| --- | --- | --- |
| authoritative | 必须参与会话级逻辑 ACID | 出错时 fail closed；它决定 session 是否正确提交 |
| additive | 可以重放或重建 | 优先使用幂等 UPSERT / append-only 行为 |
| diagnostic | 只负责观察或解释状态 | 绝不能决定用户可见的正确性 |

如果一个写入无法被分类，就说明它还不够清楚，不能合并。

## 术语 (Domain Language)

- **权威写入 (Authoritative write)** — 决定一个 session 是否正确提交的写入。
- **增量写入 (Additive write)** — 记录额外状态，但不改变权威 session 的语义。
- **诊断写入 (Diagnostic write)** — 记录证据、drift 或恢复状态的写入。
- **会话级逻辑 ACID (Session-level logical ACID)** — 在底层由多层和恢复机制组成，但对权威写入来说表现得像一个原子整体的保证。

## 后果 (Consequences)

### 正面 (Positive)

- **不再假装有分布式事务** — 架构说的是真话。
- **给未来的新表提供清晰评审规则** — 新的 D1 写入必须先分类。
- **和现有代码形状一致** — 当前栈已经在用 pending staging、session lifecycle authority、drift logging 和 recovery。
- **保护权威历史路径** — 用户最关心的是 session 的历史有没有正确一次性提交。
- **保留 enrichment 的灵活性** — 增量表可以保持可重放、可幂等，而不必被拖进关键路径。

### 负面 (Negative)

- **评审时要多一个判断面** — 作者必须说明为什么某个写入是 authoritative / additive / diagnostic。
- **不是所有 D1 写入都使用同一种失败语义** — 需要开发者和运维始终分清类别。
- **Dual / recovery 机制仍然必要** — 因为系统本来就不是分布式事务栈。

## 备选方案 (Alternatives Considered)

### 把 SQLite 和 D1 做成完整分布式 ACID

拒绝。当前系统依赖 HTTP 驱动的 D1 请求、本地 SQLite、dual-write parity checks 和 recovery 流程，这不是一个分布式事务系统；假装它是只会制造错误的安全感。

### 所有东西都走 best-effort

拒绝。权威历史路径会变得太弱，失败 session 也无法再被清晰推理。

### 把每一个 D1 表都当成权威表

拒绝。仓库里已经存在增量 enrichment 和诊断面，它们应该留在 session commit 边界之外。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR042-01](IMP-ADR042-01-d1-logical-acid-boundaries.md) | 将边界传播到 CONTEXT.md 以及 storage / handbook 文档 | 任何试图把 SQLite 和 D1 伪装成分布式事务的设计 |

## 参考 (References)

- [ADR-005 — Db Py Retirement and Repo Pattern](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)
- [ADR-009 — D1 Drift Classifier](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md)
- [ADR-010 — D1 Access Port](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.zh.md)
- [ADR-019 — Session Lifecycle Authority](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.zh.md)
- [ADR-032 — Mandatory Session Binding](../ADR-032-Mandatory-Session-Binding/ADR-032-mandatory-session-binding.zh.md)
- [ADR-033 — Media Closed Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.zh.md)
- [ADR-036 — Event Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.zh.md)
- [D1 rollback handbook](../../handbook/zh/ops/d1-rollback.md)
- [javdb/storage/d1_client.py](../../../javdb/storage/d1_client.py)
- [javdb/storage/dual_connection.py](../../../javdb/storage/dual_connection.py)
- [javdb/storage/db/_db_history_write.py](../../../javdb/storage/db/_db_history_write.py)

## 状态日志 (Status Log)

- 2026-05-31: Accepted — 将 D1 的会话级逻辑 ACID 边界与权威写入分类固化下来。
