# ADR-036：事件溯源管道脊柱

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;执行下放给各期 IMP                                    |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md), [ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md), [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md), [ADR-010](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) |

> 源自 2026-05-29 一次关于全新方向(方向三——可重放的管道内核)的头脑风暴。

## 背景 (Context)

管道是一条**编排式、命令式**过程:`javdb/pipeline/` 把 spider → uploader → pikpak 作为子进程/进程内步骤运行,带结构化 result sidecar（[ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)）。加一个横切能力就意味着**管道手术**——最近两份设计就是证据:

- [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)（媒体闭环）不得不**给 uploader 插桩**（加种时写）并**从 cleanup 步骤 push**（完成）才能得知种子的命运。
- [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)（漂移哨兵）不得不**hook index 解析边界**并**门控 commit 路径**。

每个新功能都从不同的点伸进管道。没有一条共享的流供消费者订阅。

两个事实决定了正确的野心:

1. **系统其实已经半事件溯源。** `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites` 是一个 **append-then-project** 日志（行以 `ApplyState='pending'` 累积,在 commit 时 materialize 到 `MovieHistory` / `TorrentHistory`）;`ReportSessions` 生命周期是一个受治理的状态机（[ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)）。
2. **对重日志的胃口低——有证据。** 逐行变更日志（`MovieHistoryAudit` / `TorrentHistoryAudit`）已于 2026-05-22 被**删除**（[ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) PR-4）。一个完整事件溯源重写会**重新引进他们刚删掉的那种啰嗦日志**。

因此本 ADR 走**附加式**路线:一条管道发往的 append-only 事件脊柱,新功能**消费**它而非 hook——且**不碰**权威的 `pending→commit` 路径。

## 决策 (Decision)

在 D1 引入一条**附加式、append-only 的 `PipelineEvent` 日志**。管道在其自然点发出**实体生命周期事件**;消费者按 cursor 读日志、构建幂等投影。现有 `pending→commit` 仍是 history 的权威;脊柱纯附加,日后可经 strangler 迁移逐步吸收投影。

### 设计决策 (Design Decisions)

**D1. 附加式脊柱,非破坏性。** 在现有管道**旁**加一条 append-only 日志。`pending→commit` 仍是 `MovieHistory` / `TorrentHistory` 的真相源。不重写任何现有物;脊柱日后可渐进变权威（strangler），也可永不变。

**D2. 载体是 D1 append-only 表;消费者按 cursor 轮询。** 不用 Cloudflare Queue / Durable Object。系统是批处理（cron 管道、批消费者），所以一张带单调序的可查询 D1 表是正确载体——D1-canonical、可重放、无实时推送基建。

```sql
CREATE TABLE PipelineEvent (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,  -- global monotonic order (D1 serializes writes)
  session_id   TEXT NOT NULL,
  run_id       TEXT,
  run_attempt  INTEGER,
  event_type   TEXT NOT NULL,
  entity_type  TEXT NOT NULL,   -- session | movie | torrent
  entity_id    TEXT,            -- href (movie) | qb_hash (torrent) | session_id (session)
  payload      TEXT,            -- JSON
  created_at   TEXT NOT NULL
);

CREATE TABLE EventConsumerCursor (
  consumer   TEXT PRIMARY KEY,
  last_seq   INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
```

**D3. 实体生命周期粒度——绝不逐字段。** 事件在 movie/torrent/session 有意义的生命周期转换点触发,而非每次字段变更（那是已退役的审计日志错误）。分类:

| 实体 | 事件 |
| --- | --- |
| session | `RunStarted`, `SessionCommitted`, `SessionFailed` |
| movie | `MovieDiscovered`, `MovieSelected` |
| torrent | `TorrentSelected`, `TorrentQueued`, `TorrentCompleted` |

体量与 pending 表相当（每实体每转换一行），那是系统本就持久化的——而非它删掉的额外逐行日志。

**D4. 在管道自然点 emit;一致性分级。** `events.emit()` 在功能本就 hook 的地方调用（index 选择、uploader 加种、cleanup、commit）。in-run 事件（`Discovered`/`Selected`/`Queued`）**best-effort**（emit 失败不能阻断管道）;commit 类事件（`SessionCommitted`/`SessionFailed` 与逐实体的 `Committed` 视图）随 **commit 事务**落,使日志对"什么提交了"永不与现实相左。对所有事件的完整事务 outbox 是后续硬化项。

**D5. cursor 幂等消费者 + 免费重放。** 消费者读 `seq > last_seq`、幂等投影、推进 cursor。**重放** = 把某消费者 cursor 重置为 0 再跑 → 它的投影从日志重建。这是头号价值（可重放/可审计），且在 cursor 模型下几乎免费。

**D6. 对现有 hook 的 strangler 路径。** Phase 2 把 [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) 的 `AcquisitionOutcome` 与 [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) 的哨兵改成**消费事件**而非 hook 管道——回头去侵入化它们。让 `pending→commit`/history 成为日志的投影是 Phase 3+,推迟且高谨慎。

**D7. 模块形态遵循仓库惯例。** `javdb/pipeline/events/` 含 `models.py`（事件类型）、`store.py`（`emit` + read-since-cursor）、`consumer.py`（基类消费者 + cursor 推进）;`javdb/storage/repos/pipeline_event_repo.py` 是 D1 访问。emit 调用点位于现有管道点。

## 后果 (Consequences)

### 正面 (Positive)

- **新功能变消费者,而非手术**——订阅脊柱,而非每次从新点伸进管道。
- **可重放可审计**——重置 cursor 即可重建任意投影;日志是"发生了什么"的有序真相。
- **回头简化 ADR-033/035**——它们的侵入式 hook 在 Phase 2 变成事件消费者。
- **低风险**——附加式;`pending→commit` 权威不动。
- **D1-canonical**——多一张 append-only 表,无新基建。

### 负面 (Negative)

- **多一条要维持诚实的流**——emit 点须随管道演进保持正确;best-effort 的 in-run emit 在失败下可能漏事件（commit 类事件是一致性骨干）。
- **消费者最终一致**——cursor 轮询的投影滞后日志一个轮询间隔（对批处理系统可接受）。
- **strangler 前与 pending 表重叠**——附加阶段脊柱与 pending 日志都描述 history 写;仅当 Phase 3 让 history 成投影时才消解。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — 脊柱 + 示范 | [IMP-ADR036-01](IMP-ADR036-01-event-spine.md) | `PipelineEvent` + `EventConsumerCursor` 表;`events` 模块（`emit`、read-since-cursor、基类消费者）;管道各点 emit;一个示范消费者（`RunEventSummary` per-session 计数）证明 emit→consume→replay | 收编 ADR-033/035;history-as-projection |
| Phase 2 — 收编消费者 | IMP-ADR036-02（占位） | 把 ADR-033 `AcquisitionOutcome` 与 ADR-035 哨兵改成消费事件 | — |
| Phase 3 — Strangler（可选） | IMP-ADR036-03（占位） | 让 `pending→commit`/history 成为日志的投影 | — |

Phase 1 独立成立、不碰任何权威物。Phase 2 依赖 ADR-033/035 已落地。Phase 3 是可选、高谨慎的权威迁移。

### 明确的非目标 (YAGNI)

- **日志非权威**——`pending→commit` 仍是真相源（仅 Phase 3+）。
- **无实时推送**——D1 表 + cursor 轮询;无 Cloudflare Queue/DO。
- **无逐字段事件**——仅实体生命周期转换。
- **Phase 1 不从日志重建现有投影。**

## 领域语言 (CONTEXT.md 待补充项)

- **Pipeline event（管道事件）**——`PipelineEvent` 中一条不可变、append-only 的实体生命周期转换记录（movie/torrent/session），按 `seq` 排序。
- **Event spine（事件脊柱）**——管道发往、消费者订阅的那条单一 append-only 日志。
- **Consumer cursor（消费者游标）**——某消费者的 `last_seq`,标记它投影到哪;重置即重放。
- **Projection（投影）**——消费者从事件构建的幂等读模型。
- **Strangler migration（绞杀迁移）**——把权威从 `pending→commit` 渐进迁到事件日志（推迟）。

## 备选方案 (Alternatives Considered)

- **完整事件溯源（每个动作一事件,所有状态皆投影,replay 重建一切）**——否决:对在跑管道的爆炸半径最大,且重新引进 ADR-005 PR-4 删掉的逐行日志。
- **Cloudflare Queue / Durable Object 载体**——否决（D2）:管道是 Python（GH Actions/本地）、消费者是批处理;CF Queue 为系统并不需要的实时引入跨进程 HTTP 耦合。
- **仅阶段/run 级粒度**——否决（D3）:消费者（闭环、偏好、stats）需要逐实体事件来派生逐实体状态。
- **现在就让日志变权威**——否决（D1）:对在跑系统风险高;strangler 路径让它保持可选、渐进。

## 参考 (References)

- [ADR-012 — Pipeline Run Structured Boundary](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)
- [ADR-019 — Session Lifecycle Authority](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-005 — db.py Retirement & Repo Pattern](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
- [ADR-010 — D1 Access Port](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed(伞型;三期已划定,IMP 待出)。
