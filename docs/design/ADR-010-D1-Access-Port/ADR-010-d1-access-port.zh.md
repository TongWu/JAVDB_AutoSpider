# ADR-010：统一 Python D1 读写端口

**状态**：已接受 —— 实现待启动（截至 2026-05-19，四个阶段均未执行）
**日期**：2026-05-19
**决策者**：D1 统一读写端口 brainstorming + grill 会话
**前置**：[ADR-006](../_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md) 已将 Pending Mode 作为默认写入路径；[ADR-009](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md) 记录了近期 D1 瞬时失败和 drift 响应。
**关联实现计划 (Related Implementation Plans)**：[IMP-ADR010-01](IMP-ADR010-01-d1-access-port-phase1-core.md)（Phase 1 — 端口核心）、[IMP-ADR010-02](IMP-ADR010-02-d1-access-port-phase2-recovery-outbox.md)（Phase 2 — 恢复 outbox）、[IMP-ADR010-03](IMP-ADR010-03-d1-access-port-phase3-safe-batching.md)（Phase 3 — 安全批处理）、[IMP-ADR010-04](IMP-ADR010-04-d1-access-port-phase4-startup-replay.md)（Phase 4 — 启动重放）

## 待办 (Outstanding Work)

- Phase 1 —— `D1AccessPort` 核心类 + `D1Connection`/`DualConnection` 代理。`javdb/storage/` 下尚未出现 `D1AccessPort` 符号。
- Phase 2 —— 恢复 outbox + replay 队列（对应 D5）。
- Phase 3 —— 安全 micro-batching + `flush()` 边界（对应 D4）。
- Phase 4 —— 启动期 outbox 重放。

四阶段独立闸门；截至该日期，本 ADR 中尚无任何决策上线。

---

## 背景

当前 D1 路径分散在几层：

- `javdb/storage/d1_client.py` 负责 Cloudflare D1 HTTP 请求格式、错误分类、retry/backoff、`executemany`、`batch_execute` 和 `requests.Session` 复用。
- `javdb/storage/dual_connection.py` 负责 SQLite + D1 双写、读走 D1、drift 记录，以及受保护主键表的校验。
- `javdb/storage/db/db_connection.py` 通过 `STORAGE_BACKEND` 选择 `sqlite`、`d1` 或 `dual`。
- 业务写入语义仍在 `db.py`、`db_history_write.py` 和 Repo wrapper 中。这些层知道 Session、PendingHistory、Stats、Rollback 和 Operations。

这套结构能工作，但有两个持续问题：

1. D1 仍会从热点路径收到大量短间隔 HTTP 请求。此前最明显的是 pending session commit；已存在且已有测试覆盖的 `COMMIT_SESSION_BULK` 路径可以把 per-href D1 请求压成批量请求，但目前仍是 opt-in。
2. 可恢复 D1 写失败主要靠单请求 retry + drift 检测。retry 耗尽后，dual 模式可以继续落 SQLite 并记录 drift，但没有统一的端口级 recovery queue 来重放已证明幂等的 D1 写。

目标边界是 Python 内部 D1 access port，不新增外部服务。这个端口也**不是**“D1 版 `db.py`”。`db.py` 和 Repo 层继续负责业务语义和 SQL 构造。D1 access port 只负责 Cloudflare D1 的 transport、可靠性、批处理、recovery、schema metadata cache 和观测。

---

## 决策

在 `javdb/storage/` 下引入进程内 `D1AccessPort`。它成为 Python 访问 Cloudflare D1 的唯一出口。`D1Connection` 继续保留调用方依赖的 sqlite3-compatible facade，但把 D1 HTTP 执行和 recovery 行为委托给端口。

### D1. 端口边界

分层变为：

```text
业务存储代码：db.py / repos / db_history_write / db_reports / db_stats
        |
get_db() / D1Connection / DualConnection
        |
D1AccessPort
        |
Cloudflare D1 HTTP API
```

端口不得接管 Session lifecycle、Pending Mode merge 规则、rollback 语义、stats payload 解释等业务不变量。这些都留在 `D1Connection` 之上。

### D2. 最小 API 契约

ADR 固定契约，不固定具体实现：

```python
class D1AccessPort:
    def execute(self, sql, params=(), *, policy=None): ...
    def executemany(self, sql, seq_of_params, *, policy=None): ...
    def batch_execute(self, statements, *, policy=None): ...
    def flush(self, *, ordering_key=None): ...
    def drain_recovery(self, *, ordering_key=None, max_batches=None): ...
    def close(self): ...
```

`D1Connection` 继续返回现有 cursor-compatible surface（`lastrowid`、`rowcount`、`fetchone`、`fetchall`），调用方不需要直接学习端口。

`RecoveryPolicy` metadata 只用于 safe batching/replay 判断。至少包含：

| 字段 | 用途 |
|---|---|
| `logical_db` | `history`、`reports` 或 `operations` |
| `operation_type` | 人可读类别，如 `pending_stage`、`stats_upsert`、`commit_apply_mark` |
| `idempotency_key` | 用于 retry/replay 去重和推理的稳定 key |
| `ordering_key` | FIFO key，默认 `<logical_db>:<session_id or global>` |
| `recovery_allowed` | retry 耗尽后是否允许进入 outbox |
| `max_attempts` | dead-letter 前的 replay 上限 |

### D3. 同步语义优先

普通 `execute()` 仍保持同步。普通 SQL 不会被静默延迟。只有显式 safe 的操作可以使用 micro-batching 或 recovery outbox。

这保留了当前对以下行为的假设：

- 写后读可见性；
- `rowcount`；
- `lastrowid`；
- `DualConnection` drift accounting；
- 受保护主键校验；
- `STRICT_DUAL_WRITE`。

### D4. Safe batching

端口支持 micro-batching，但第一轮 rollout 不延迟任意 SQL。safe batch 在以下边界 flush：

- `flush()`；
- `commit()` / finalization 边界；
- `close()`；
- batch size 达到 `D1_BATCH_LIMIT`（当前默认 50）；
- 等待时间达到 `D1_FLUSH_INTERVAL_MS`（候选默认 250 ms）。

Phase 1 默认启用现有 `COMMIT_SESSION_BULK` 路径，因为它已经有测试覆盖，并且能直接降低 pending commit 的 D1 round-trip。pending staging batch API 留到后续阶段。

### D5. Recovery outbox

可恢复 D1 写失败时：

1. 端口先沿用现有 transient/permanent 分类器进行同步 retry/backoff。
2. retry 耗尽后，只有已证明 safe 的操作可以进入 `reports/D1/d1_recovery_outbox.jsonl`。
3. outbox 保存完整 SQL 和 params。本仓库是 private，完整 payload 会被有意提交，以支持跨 run / 跨 runner recovery。
4. replay 成功后，记录从活跃 outbox 迁移到 `reports/D1/d1_recovery_outbox.processed.jsonl`，与现有 `d1_drift.jsonl` / `d1_drift.processed.jsonl` 运维心智一致。

outbox 事件是 append-only，使用以下状态：

| 状态 | 含义 |
|---|---|
| `queued` | retry 耗尽，safe operation 已接受进入 recovery |
| `attempting` | 该事件正在 replay |
| `replayed` | replay 成功 |
| `dead_lettered` | replay 遇到永久错误或超过 retry policy |
| `abandoned` | 操作员明确放弃该事件的 recovery |

replay 按 `ordering_key` 做 FIFO。不同 ordering key 可以独立 drain。

### D6. 后端语义

后端模式决定 outbox queue 是否可以算作成功：

| 模式 | 语义 |
|---|---|
| `STORAGE_BACKEND=d1` | 强一致。D1 写必须真实落入 D1，调用方才能看到成功。outbox 可以记录诊断，但不能把失败变成成功。 |
| `STORAGE_BACKEND=dual` | safe operation 在 outbox 可靠落盘后可以视为 recoverable-success，因为 SQLite 已经有本地写。相关 ordering key 必须在 session finalization/commit 前 drain。 |
| `STRICT_DUAL_WRITE=1` | strict mode 优先。即使 queued outbox，任何 D1 写失败仍会让 transaction 失败。 |

如果 replay 进入 `dead_lettered`，相关 ordering key 被阻断。只要该 key 还有 queued、attempting 或 dead-lettered recovery work，Session 不得进入 `finalizing` / `committed`。

### D7. Safe operation 准入

端口使用混合准入模型：

- 内置保守 allowlist，用于明显幂等或带稳定 key 的 SQL，例如显式 `Id`/`Seq` insert、`ON CONFLICT ... DO UPDATE`、由稳定 `Id`、`Seq` 或 `SessionId` 限定的 update/delete。
- 热点业务路径可以显式传入 `RecoveryPolicy`，避免只靠 SQL pattern guessing。

DDL、schema migration、无 key delete、裸 AUTOINCREMENT insert、没有 policy 的顺序敏感 SQL 都不进入 outbox。

### D8. 读取行为

第一版不缓存业务 SELECT 结果。读路径获得统一 retry/metrics 行为，端口可以缓存稳定 schema metadata，例如 `PRAGMA table_info` 和部分 `sqlite_master` lookup。

不对 `MovieHistory`、`ReportSessions`、stats、pending rows 或 operations data 引入 TTL cache。

### D9. 观测

端口输出结构化日志，并在每次 run 结束生成 summary：

```text
reports/D1/d1_port_summary.json
```

summary 至少包含：

- D1 HTTP POST count；
- SQL statement count；
- batch count 和平均 batch size；
- retry count 和 retry-success count；
- transient / permanent failure count；
- outbox queued/replayed/dead-lettered count；
- recovery drain duration；
- schema-cache hit/miss count。

不要把普通 metrics 塞进 `d1_drift.jsonl`。drift 文件继续只记录异常和验证信号。

### D10. 分阶段启用

rollout 通过代码默认值和环境变量 override 分阶段推进。ADR 要求渐进模型；具体默认值翻转由后续 PR 完成。每个 phase 都有独立 implementation plan（IMP），这些 IMP 是本 ADR 的标准执行计划。

| 阶段 | Implementation plan | 默认行为 | Opt-in / 候选行为 |
|---|---|---|---|
| Phase 1 | [IMP-ADR010-01](IMP-ADR010-01-d1-access-port-phase1-core.md) | `D1Connection` 使用 `D1AccessPort`；retry/metrics/schema-cache 生效；`COMMIT_SESSION_BULK` 默认开启；生成 `d1_port_summary.json`；recovery inspect/replay CLI 可用于测试和 runbook | outbox 和通用 micro-batching 存在但禁用 |
| Phase 2 | [IMP-ADR010-02](IMP-ADR010-02-d1-access-port-phase2-recovery-outbox.md) | outbox 代码仍由 gate 控制 | `D1_RECOVERY_OUTBOX_ENABLED=1` 允许 safe operation queue 和 replay |
| Phase 3 | [IMP-ADR010-03](IMP-ADR010-03-d1-access-port-phase3-safe-batching.md) | 普通 SQL 仍同步 | `D1_BATCHING_ENABLED=1` 和 `D1_FLUSH_INTERVAL_MS=250` 允许 safe-path micro-batching |
| Phase 4 | [IMP-ADR010-04](IMP-ADR010-04-d1-access-port-phase4-startup-replay.md) | startup replay 关闭 | `D1_STARTUP_REPLAY_ENABLED=1` 在进程启动时 drain 非 dead-lettered work |

默认值晋级 gate 应使用 `d1_port_summary.json`、pending verify 记录以及 drift/dead-letter 缺失情况。只要某阶段造成新的 pending residual、新 dead letter 或无法解释的 drift，就不得把它改为默认。

### D11. Workflow 和 private payload 处理

因为 `d1_recovery_outbox.jsonl` 保存完整 SQL params，它即使被提交到 private repo，也必须被视为 private runtime state。

Phase 2 启用后，提交 D1 operational state 的 workflow 必须 stage：

```text
reports/D1/d1_recovery_outbox.jsonl
reports/D1/d1_recovery_outbox.processed.jsonl
reports/D1/d1_port_summary.json
```

任何 public publishing workflow 都必须显式排除 recovery outbox 文件，或在它们存在时 fail closed。这样可以避免完整 SQL params 被意外复制到 public mirror。

---

## 备选方案

### 备选 A：只做 Thin Port

只把 HTTP POST/retry 移到端口，不处理 batching/recovery。

否决原因：无法实质解决两个动机问题：短间隔 D1 请求爆发，以及网络失败后的写入恢复。

### 备选 B：外部 HTTP Service 或 Worker Proxy

新增一个服务，让所有 D1 流量经过它。

暂时否决。它会在 Python 存储路径尚未集中前增加新的网络依赖和部署面。

### 备选 C：所有写入进入激进统一队列

把每个 D1 写都交给端口排队、flush、replay。

否决原因：会静默改变 SQL 时序和失败语义。当前代码依赖同步写、`lastrowid`、`rowcount`、写后读行为和 strict dual-write 校验。

### 备选 D：SQLite Outbox

把 recovery work 存进本地 SQLite 表。

不作为初始设计。`STORAGE_BACKEND=d1` 下本地 `.db` 不是 source of truth，workflow 也已经跳过提交它们。`reports/D1` 下的 JSONL 更贴近现有 drift/recovery audit 模式。

### 备选 E：只做内存 Recovery

只在进程内 retry，不做 durable outbox。

否决原因：无法跨进程退出、GitHub runner 中断或长时间网络抖动。

---

## 后果

### 正面影响

- 给 D1 建立单一 Python access boundary。
- 通过默认开启现有 bulk commit path，立即减少 D1 请求量。
- 给 safe dual-mode D1 写失败增加 durable recovery 路径。
- 保留 `D1Connection` 和 `DualConnection` 的调用方契约。
- 避免把业务存储语义塞进 transport 层。
- 为后续阶段 gate 提供 metrics。

### 负面影响

- 增加一个必须保持克制的 storage-layer abstraction。
- outbox 保存完整 SQL params，提高 private repo 数据敏感度。
- 一些热点路径需要补充显式 recovery metadata。
- 分阶段 rollout 增加多个 operator 需要理解的配置开关。

### 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 被认为 safe 的操作 replay 错误 | 高 | 保守 allowlist、显式 `RecoveryPolicy`、按 operation type 写单测、不确定就 dead-letter |
| 完整 outbox payload 泄漏到 public publish | 高 | public publish workflow 必须排除 outbox 或 fail closed |
| `STRICT_DUAL_WRITE` 语义变模糊 | 中 | strict mode 永远优先，禁止 outbox soft-success |
| 一个 poison event 阻断无关 recovery | 中 | FIFO 按 ordering key，而不是全局 |
| metrics 变吵 | 低 | 使用 `d1_port_summary.json`；`d1_drift.jsonl` 继续聚焦异常 |

---

## 测试影响

实现时至少需要更新或新增以下测试：

- `D1Connection` 通过 `D1AccessPort` 执行 HTTP；
- retry/transient/permanent 分类与当前 `d1_client.py` 行为保持一致；
- schema metadata cache 不缓存业务 SELECT；
- `COMMIT_SESSION_BULK` 默认开启和 env override；
- outbox 状态流转：`queued`、`attempting`、`replayed`、`dead_lettered`、`abandoned`；
- ordering-key FIFO replay；
- `STORAGE_BACKEND=d1` 强一致；
- `STORAGE_BACKEND=dual` safe-operation soft-success；
- `STRICT_DUAL_WRITE=1` 覆盖 outbox soft-success；
- session finalization 会被相关 recovery key 阻断，直到 drain 完成；
- processed 文件迁移不丢 replay audit history。

`tests/unit/test_d1_dual.py` 和 `tests/unit/test_commit_session_bulk.py` 是现有主要测试锚点。

---

## 文档与 Workflow 影响

当实现改变行为时，需要更新：

- root `README.md` 和 `README_CN.md` 的 D1 配置表；
- `docs/handbook/en/self-hoster/configuration.md` 和 `docs/handbook/zh/self-hoster/configuration.md`；
- `docs/handbook/en/ops/d1-rollback.md` 和 `docs/handbook/zh/ops/d1-rollback.md`；
- companion wiki `JAVDB_AutoSpider.wiki` 中对应页面；
- `.github/workflows/` 中 D1 recovery outbox、processed outbox 和 port summary 文件的 staging 规则。

在测试、staging 规则和 public-publish 排除规则到位前，任何 workflow 默认值都不得启用 outbox soft-success。

---

## 相关决策

- [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) —— Repo 迁移与最终 `db.py` 退役。
- [ADR-006](../_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md) —— Pending Mode 默认和 bake-gate 模型。
- [ADR-009](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md) —— D1 瞬时错误分类器与 drift 诊断。
- [`docs/handbook/zh/ops/d1-rollback.md`](../../handbook/zh/ops/d1-rollback.md) —— 当前 pending rollback 和 drift 响应 runbook。
