# ADR-002: 可观测性数据的存储拓扑

**状态**: 已完成 2026-05-17 —— 五张历史表全部上线（MetricsState DO 的 `metrics_snapshots`、RunnerRegistry DO 的 `signals_event_log` + `runners_event_log` + `proxies_seen`、GlobalLoginState DO 的 `login_event_log`、ConfigState DO 的 `config_audit_log`）。
**日期**: 2026-05-16
**决策者**: Proxy Coordinator Dashboard 改造
**关联实现计划 (Related Implementation Plans)**: [IMP-ADR003-01](../ADR-003-Metrics-Pipeline/IMP-ADR003-01-dashboard-phase2-worker-backend.md)（worker backend 基础设施——schema 已落地）、[IMP-ADR002-01](IMP-ADR002-01-dashboard-phase4-history-drilldowns.md)（下游 drill-down UI；消费方，计划中）

---

## 背景 (Context)

为了让运维 dashboard 支持"看过去发生了什么"（history drill-down），需要持久化 5 类回放数据：

| 数据类型 | 性质 | 用途 |
|---|---|---|
| Metrics Snapshots | 周期采样时序 | latency / health score / queue depth 图表 |
| Signals Event Log | 事件流 | signal 创建/到期/撤销审计 |
| Runners Event Log | 事件流 | runner register/unregister/crashed 履历 |
| Login Event Log | 事件流 | attempt/publish/invalidate/lease 流转 |
| Config Audit Log | 变更审计 | PATCH /config 的 before/after |

这些数据**目前都不持久化**——`RunnerRegistry` DO 只保留活跃 runner / 活跃 signal，过期即丢；`ConfigState` DO 不记录变更历史；`GlobalLoginState` DO 只有 bounded ring buffer。

### 问题

需要回答："这些 history 表**放在哪个 DO 里**？"

---

## 决策 (Decision)

**每类回放数据扩展到对应业务 DO 的 SQLite schema**，而非集中到一个新的 `HistoryState` DO。

具体落位：

| 数据 | 落位 DO | 表 |
|---|---|---|
| Metrics Snapshots | **MetricsState DO**（新建，见 ADR-003） | `metrics_snapshots` |
| Signals Event Log | `RunnerRegistry` DO（已有，扩 schema） | `signals_event_log` |
| Runners Event Log | `RunnerRegistry` DO（已有，扩 schema） | `runners_event_log` |
| Login Event Log | `GlobalLoginState` DO（已有，扩 schema） | `login_event_log` |
| Config Audit Log | `ConfigState` DO（已有，扩 schema） | `config_audit_log` |

每个 DO 自己跑 retention sweep（差异化保留期：metrics 30d / signals 90d / runners 90d / login 30d / config 365d）。

---

## 备选方案 (Alternatives Considered)

### 备选 A：集中式 HistoryState DO

新建一个 singleton `HistoryState` DO，所有 5 类 history 集中存放，其他 DO 在状态变更时跨 DO 写入。

**优点**：
- 单一来源、单一清理任务
- 跨 history 类型的查询更容易（例如"昨晚 23:00 同时发生了什么"）
- 增加新 history 类型只需改一处

**缺点（不选的原因）**：
- **原子性破坏**：状态变更（如 PATCH /config）和审计写入分别在两个 DO 上，不在同一事务。若 audit 写入失败，配置已经改了但没人记账——审计目的失效。
- **跨 DO 写入是额外 IO + 时延 + 失败处理负担**：每次状态变更必须 fan-out 一个 fetch 请求到 HistoryState DO，要么阻塞主路径（拖慢响应），要么 fire-and-forget（丢失风险）。
- **集中 DO 变成热点**：所有运维事件都打到一个 DO 上，单点写入瓶颈。
- **跨 history 类型查询的价值有限**：dashboard drill-down 是按面板分类的（signals tab / config tab 各自独立），并不真正需要联合查询。

---

## 实现策略 (Implementation)

### Phase 2（rollout 阶段，见 grill-me Q7）

向三个现有 DO 各加一张 history 表：

#### `RunnerRegistry` DO
```sql
CREATE TABLE signals_event_log (
  ts INTEGER NOT NULL,            -- ms wall-clock
  event_kind TEXT NOT NULL,       -- 'create' | 'auto_expire' | 'explicit_revoke'
  signal_id TEXT NOT NULL,
  signal_kind TEXT NOT NULL,      -- throttle_global | ban_proxy | pause_all | resume
  payload_json TEXT,              -- factor / proxy_id / reason ...
  PRIMARY KEY (ts, signal_id)
);
CREATE INDEX idx_signals_event_log_kind ON signals_event_log(signal_kind, ts);

CREATE TABLE runners_event_log (
  ts INTEGER NOT NULL,
  event_kind TEXT NOT NULL,       -- 'register' | 'unregister' | 'crashed'
  holder_id TEXT NOT NULL,
  workflow_run_id TEXT,
  workflow_name TEXT,
  proxy_pool_hash TEXT,
  final_status TEXT,              -- on unregister/crashed only
  PRIMARY KEY (ts, holder_id, event_kind)
);
CREATE INDEX idx_runners_event_log_holder ON runners_event_log(holder_id, ts);
```

#### `ConfigState` DO
```sql
CREATE TABLE config_audit_log (
  ts INTEGER NOT NULL,
  key TEXT NOT NULL,
  old_value TEXT,                 -- JSON
  new_value TEXT,                 -- JSON
  actor TEXT,                     -- principal id (bearer token name / dashboard cookie)
  actor_kind TEXT NOT NULL,       -- 'operator' | 'system'
  reason TEXT,
  PRIMARY KEY (ts, key)
);
```

#### `GlobalLoginState` DO
```sql
CREATE TABLE login_event_log (
  ts INTEGER NOT NULL,
  event_kind TEXT NOT NULL,       -- 'attempt' | 'publish' | 'invalidate' | 'lease_acquire' | 'lease_release'
  holder_id TEXT,                 -- nullable: invalidate may come from anyone
  outcome TEXT,                   -- 'success' | 'failure' (attempt only)
  cookie_version INTEGER,         -- publish/invalidate only
  detail TEXT,                    -- free-form reason
  PRIMARY KEY (ts, event_kind, COALESCE(holder_id, ''))
);
```

### Retention sweep

每个 DO 的 GC alarm（已存在）里加一条 `DELETE WHERE ts < now() - retention_ms`：
- Signals / Runners event log：90 天
- Login event log：30 天
- Config audit log：365 天

清理频率：每次 GC alarm 顺手扫；额外硬上限按表 100k 行（防御性）。

### Phase 4（drill-down UI）

逐个 panel 暴露 GET 端点（cookie-authed only）：
- `GET /signals/history?range=...`
- `GET /runners/history?range=...&holder_id=...`
- `GET /login/history?range=...&holder_id=...`
- `GET /config/history?range=...&key=...`

Dashboard 抽屉打开时 fetch 对应端点。

---

## 后果 (Consequences)

### 正面影响

1. **原子写入**：状态变更 + history 写入在同一 DO 事务，永不漂移
2. **写入零额外时延**：history 写就在主路径里，不需要 fan-out
3. **DO 写入负载分散**：每个 DO 自己承担自己的 history 流量
4. **DO schema 演化局部化**：未来某个 DO 加字段不影响其他 DO

### 负面影响

1. **5 套独立的 retention 逻辑**（每个 DO 一份 sweep 代码）—— 通过抽出一个 `pruneLogTable(db, table, retentionMs, maxRows)` 共享 helper 缓解
2. **跨 history 类型联合查询不便** —— 实际 dashboard 不需要
3. **新增 history 类型需要修改对应 DO** —— 但增加频率低，可接受

### 风险

1. **某个 DO 的 SQLite 存储增长不可预期** —— Retention sweep + 硬上限兜底
2. **schema migration**：现有 DO 已有数据，需要 `CREATE TABLE IF NOT EXISTS` + 不破坏既有列
   - **缓解**：用 SQLite `ALTER TABLE ADD COLUMN` 的向后兼容能力；不动既有列

---

## 相关决策 (Related Decisions)

- **ADR-003**：Metrics Pipeline 的具体设计（MetricsState DO 独立的原因）
- **ADR-004**：Runner 上报 PROXY_POOL（runners_event_log 的部分数据来源）

---

## 参考资料 (References)

- [CONTEXT.md](../../../../CONTEXT.md) — 可观测性数据章节、术语定义
- 现有 DO 实现：`JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`, `config_state.ts`, `global_login_state.ts`
