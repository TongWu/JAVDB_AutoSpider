# CONTEXT.md

项目领域语言和架构概念词汇表。用于保持代码、文档、讨论中的术语一致性。

---

## 存储层（Storage Layer）

### Session（会话）

一次 pipeline 运行的**业务**逻辑单元，由唯一的 `SessionId` 标识。每个 Session 关联：
- **SessionId** — 应用层生成的 TEXT 字符串，格式 `YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS`（不是遗留的 51-bit snowflake 整数）
- **RunId** — GitHub Actions 的 `github.run_id`（可选）
- **RunAttempt** — GitHub Actions 的 `github.run_attempt`（可选）
- **Status** — `in_progress`, `finalizing`, `committed`, `failed`

所有数据库写入都必须携带 `SessionId`，以支持精确回滚。

> Session 是 Python 端业务概念，存于 SQLite/D1 的 `ReportSessions`，**与 Worker 端的 `Runner`（见"代理协调"章节）不同**：Session 是 pipeline 视角，Runner 是进程视角，通常 1:1 但生命周期不完全重合。

**相关接口**：
- `db_session.set_active_session_id()`
- `db_session.get_active_session_id()`
- `db_reports.db_create_report_session()`

---

### Write Mode（写入模式）

历史记录（MovieHistory/TorrentHistory）目前有两种写入路径并存。**ADR-005 计划只保留 Pending Mode**，但当前仍是过渡期——**真实默认是 audit**（[ADR-006](adr/ADR-006-pending-mode-default-rollout.md) 正在把默认推到 pending，bake 期 30 天后才进入 ADR-005）。

#### **Pending Mode**（目标默认）

写入分两阶段：
1. **Stage** — 写入先进入 `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites`
2. **Commit** — 成功后批量 upsert 到 `MovieHistory` / `TorrentHistory`

失败时直接删除 `Pending*` 表中的行，无需审计回放。

**相关接口（ADR-005 之后）**：
- `HistoryRepo(conn, session_id).stage_movie(...)` / `.stage_torrent(...)`
- `HistoryRepo(conn, session_id).commit()`
- `HistoryRepo(conn, session_id).rollback()`

**当前接口（过渡期）**：
- `db_history_write.db_stage_history_write()` / `db_commit_session_history()`

#### **Audit Mode**（当前实际默认，将于 ADR-005 退役）

每次 upsert 前，将旧行的 JSON 保存到 `MovieHistoryAudit` / `TorrentHistoryAudit`。失败时通过审计表恢复。

**当前状态**：
- `db_session.py:188` 在无 explicit/env/config 时 fallback 返回 `'audit'`
- SQLite schema `WriteMode TEXT DEFAULT 'audit'`
- 近 30 天约 80% session 走此路径（实测 2026-05-16）
- `DailyIngestion.yml` 有 auto-fallback 到 audit 24h 的运维安全网（critical pending alert 触发）

**退役计划**：
1. [ADR-006](adr/ADR-006-pending-mode-default-rollout.md) — 改 code/schema/workflow 默认到 pending，重设计 auto-fallback 为告警+暂停，bake 30 天
2. [ADR-005](adr/ADR-005-db-py-retirement-and-repo-pattern.md) — 完全退役（删 `_audit_*` 代码 + drop 审计表）

**相关接口**：
- `db_history_write.db_upsert_history()`（含 `_audit_*` 辅助）

---

### Storage Backend（存储后端）

系统支持三种存储后端，通过环境变量 `STORAGE_BACKEND` 控制：

#### **SQLite Mode**（默认）

所有读写操作本地 SQLite 文件：
- `reports/history.db` — MovieHistory, TorrentHistory
- `reports/reports.db` — ReportSessions, ReportMovies, ReportTorrents, Stats
- `reports/operations.db` — RcloneInventory, DedupRecords, PikpakHistory

#### **D1 Mode**

所有读写操作 Cloudflare D1（GitHub Actions 环境）。

#### **Dual Mode**

写入同时镜像到 SQLite 和 D1，读取从 D1。用于迁移验证。

**相关接口**：
- `db_connection.current_backend()`
- `db_connection.get_db()` — 根据 backend 返回不同连接类型

---

### Drift（漂移）

在 **Dual Mode** 下，如果 D1 写入失败但 SQLite 写入成功，会产生"漂移"（两个后端数据不一致）。

**处理策略**：
- 默认：记录到 `reports/D1/d1_drift.jsonl`，继续执行
- `STRICT_DUAL_WRITE=1`：D1 失败时立即中止 Session

**相关模块**：
- `dual_connection.DualConnection` — 检测并记录 drift
- `d1_client.D1Connection` — D1 HTTP 客户端

---

## 历史记录（History）

### MovieHistory / TorrentHistory

存储已下载电影和种子的历史记录，用于增量抓取（避免重复下载）。

**关键字段**：
- `Href` — 电影的唯一标识符（JavDB URL 路径，如 `/movies/abc123`）
- `Phase` — 抓取阶段（1-4）
- `LastVisited` — 最后访问时间
- `TorrentTypes` — 已下载的种子类型（JSON 数组，如 `["字幕", "无码"]`）

**Upsert 语义**：
- 如果 `Href` 已存在，更新 `LastVisited` 和 `TorrentTypes`（合并新类型）
- 如果 `Href` 不存在，插入新行

**相关接口**：
- `HistoryRepo.stage_movie()` / `.stage_torrent()` — Pending 写入
- `HistoryRepo.load_history()` — 读取所有历史
- `HistoryRepo.load_history_snapshot()` — 读取包含 pending 的快照

---

### Pending Tables（待定表）

Pending Mode 下的暂存表：
- `PendingMovieHistoryWrites` — 待提交的 MovieHistory 行
- `PendingTorrentHistoryWrites` — 待提交的 TorrentHistory 行

**生命周期**：
1. Spider 调用 `db_stage_history_write()` → 插入 Pending 表
2. Spider 成功完成 → 调用 `db_commit_session_history()` → 批量 upsert 到 History 表
3. Spider 失败 → 调用 `db_rollback_session()` → 删除 Pending 表中的行

**相关接口**：
- `HistoryRepo.stage_movie()` / `.stage_torrent()`
- `HistoryRepo.commit()`
- `RollbackCoordinator.rollback_session()`（跨 Repo 协调器）

---

### Audit Tables（当前存在，ADR-005 后退役）

Audit Mode 下的审计表：
- `MovieHistoryAudit` — 每次 upsert 前的旧行 JSON
- `TorrentHistoryAudit` — 每次 upsert 前的旧行 JSON

**字段**：
- `SessionId` — 关联的 Session
- `Href` — 被修改的电影
- `OldRowJson` — 修改前的行（JSON 字符串）
- `AuditedAt` — 审计时间

**回滚时**：解析 `OldRowJson`，恢复到 History 表。

**目标态**：[ADR-005](adr/ADR-005-db-py-retirement-and-repo-pattern.md) 计划 drop 这两张表（migration v14）。前置条件 [ADR-006](adr/ADR-006-pending-mode-default-rollout.md) bake 完成后才能执行。

---

## 报告（Reports）

### ReportSessions

记录每次 pipeline 运行的元数据：
- `Id` — SessionId（主键）
- `ReportType` — `daily`, `adhoc`, `manual`
- `Status` — `in_progress`, `finalizing`, `committed`, `failed`
- `RunId` / `RunAttempt` — GitHub Actions 标识符
- `WriteMode` — 当前 `'pending'` 或 `'audit'`；ADR-005 后只有 `'pending'`
- `CreatedAt` / `CommittedAt` / `FailedAt` — 时间戳

**状态转换**：
```
in_progress → finalizing → committed
            ↘ failed
```

**相关接口**：
- `ReportsRepo(conn, session_id).create_report_session(...)`
- `ReportsRepo(conn, session_id).mark_committed()`
- `ReportsRepo(conn, session_id).mark_failed(reason=...)`

---

### ReportMovies / ReportTorrents

每次运行抓取到的电影和种子的快照（用于生成 CSV 报告）。

**与 History 的区别**：
- **History** — 累积的历史记录（增量更新）
- **Reports** — 单次运行的快照（不可变）

**相关接口**：
- `ReportsRepo(conn, session_id).insert_report_rows(...)`
- `ReportsRepo(conn).get_report_rows(session_id=...)`

---

## 统计数据（Stats）

### SpiderStats / UploaderStats / PikpakStats

每次运行的统计指标（JSON 格式）：
- **SpiderStats** — 抓取统计（电影数、种子数、失败数）
- **UploaderStats** — qBittorrent 上传统计
- **PikpakStats** — PikPak 同步统计

**字段**：
- `SessionId` — 关联的 Session（主键）
- `StatsJson` — 统计数据（JSON 字符串）
- `CreatedAt` — 创建时间

**相关接口**：
- `StatsRepo(conn, session_id).save_spider_stats(...)`
- `StatsRepo(conn).get_spider_stats(session_id=...)`

---

## 操作表（Operations）

### RcloneInventory

Rclone 远程存储的文件清单（用于去重）。

**字段**：
- `RemotePath` — 远程文件路径
- `Size` — 文件大小
- `ModTime` — 修改时间

**更新策略**：
1. 写入 `RcloneInventoryStaging` 暂存表
2. 调用 `db_swap_rclone_inventory()` 原子替换主表

**相关接口**：
- `OperationsRepo(conn).load_rclone_inventory()`
- `OperationsRepo(conn, session_id).swap_rclone_inventory()`

---

### DedupRecords

去重记录（标记已处理的电影，避免重复上传）。

**字段**：
- `Href` — 电影标识符
- `Reason` — 去重原因（`rclone_exists`, `qb_exists`, 等）
- `SessionId` — 关联的 Session
- `CreatedAt` — 创建时间

**相关接口**：
- `OperationsRepo(conn).load_dedup_records()`
- `OperationsRepo(conn, session_id).save_dedup_records(...)`

---

### PikpakHistory

PikPak 同步历史（记录已同步的文件）。

**字段**：
- `TorrentHash` — 种子哈希
- `FileName` — 文件名
- `SessionId` — 关联的 Session
- `SyncedAt` — 同步时间

**相关接口**：
- `OperationsRepo(conn, session_id).append_pikpak_history(...)`

---

## 回滚（Rollback）

### Rollback Strategy（回滚策略）

当前根据 **Write Mode** 选择回滚策略；ADR-005 后只保留 Pending 一条。

#### **Pending Mode Rollback**

1. 删除 `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites` 中 `SessionId` 匹配的行
2. 删除 `ReportSessions` / `ReportMovies` / `ReportTorrents` 中 `SessionId` 匹配的行
3. 删除 `RcloneInventory` / `DedupRecords` / `PikpakHistory` 中 `SessionId` 匹配的行

#### **Audit Mode Rollback**（过渡期保留）

1. 从 `MovieHistoryAudit` / `TorrentHistoryAudit` 读取 `OldRowJson`
2. 解析 JSON，恢复到 `MovieHistory` / `TorrentHistory`
3. 删除审计行
4. 删除 Reports 和 Operations 表中的行

**当前接口**：
- `db_rollback.db_rollback_session()` — 协调所有表的回滚
- `db_history_write.rollback_history_for_session()`
- `db_reports.rollback_reports_for_session()`
- `db_operations.rollback_operations_for_session()`

**ADR-005 之后**：
- `RollbackCoordinator(conn).rollback_session(session_id)` — 跨 Repo 协调
- `HistoryRepo(conn, session_id).rollback()` — 只保留 Pending 分支

---

### Orphan Audit（孤儿审计）

在 Audit Mode 下，如果 Session 被标记为 `committed`，但审计表中仍有该 Session 的行，这些行被称为"孤儿审计"。

**原因**：
- Session 提交后，审计行应该被清理
- 如果清理失败（进程崩溃、超时），审计行会残留

**处理**：
- `db_rollback_session()` 会检测并删除孤儿审计
- 定期清理任务（`StaleSessionCleanup.yml`）会清理 >30 天的孤儿审计

**ADR-005 之后**：随审计表 drop 一并退役。

---

## 代理协调（Proxy Coordinator）

### Runner（运行者）

Worker 端 `RunnerRegistry` Durable Object 跟踪的**进程注册单位**。每个 Runner 由 `holder_id` 唯一标识，对应一个执行 pipeline 的进程（通常是一个 GitHub Actions runner job）。

**字段**：
- `holder_id` — 进程唯一标识（不是 `SessionId`）
- `workflow_run_id` — GH Actions `github.run_id`（与 Python 端 `Session.RunId` 同源）
- `workflow_name` — 工作流名（如 `DailyIngestion`）
- `started_at` — 首次 register 时刻
- `last_heartbeat` — 最近心跳
- `proxy_pool_hash` — 当前进程的 PROXY_POOL JSON 的 SHA1 前缀
- `page_range` — 该进程负责的页区段（可选）

**与 Session 的区别**：
- Session 是 **业务概念**（pipeline 视角），存于 Python 端 SQLite/D1
- Runner 是 **进程概念**（运行时视角），存于 Worker 端 DO
- 通常 1:1 对应，但**生命周期不完全重合**：Session 可能在 Runner unregister 后还要做 commit/rollback；Runner 也可能在 Session 创建前就 register

**Runner 生命周期事件**（写入 `runners_event_log`，见"可观测性数据"）：
- `register` — 首次注册
- `unregister` — 主动注销
- `crashed` — 心跳超时（StaleSessionCleanup 触发，>48h 无心跳）

**相关接口**：
- `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`
- `javdb/proxy/coordinator/runner_registry_client.py`

---

### RunnerRegistry DO（运行者注册表）

Singleton Durable Object（`idFromName("runners")`），跨 Worker isolate 唯一权威实例。职责：
- 维护活跃 `Runner` 集合（live registry，键 `holder_id`）
- 维护活跃 `Signal` 集合（operator-pushed，TTL 自动过期）
- GC alarm 周期清理 stale runner 和 expired signal

**dashboard 改造扩展**（新增表）：
- `proxies_seen(id TEXT PK, name TEXT, last_seen_ms INTEGER)` — Worker 端 proxy 名册（来源：runner register 时上报的 PROXY_POOL）
- `signals_event_log` — signal 生命周期事件（90 天）
- `runners_event_log` — runner 生命周期事件（90 天）

**相关接口**：
- `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`

---

### ConfigState DO（动态配置）

Singleton Durable Object（`idFromName("global-config")`），存储运行时可调的全局配置 override。runner 在 register / heartbeat 时通过 `embedConfigSnapshot()` 顺带拉取。

**核心 API**：
- `GET /config` — 当前快照（base values + overrides 合并视图）
- `PATCH /config` — 操作员或程序化修改某个 key

**dashboard 改造扩展**：
- `config_audit_log(ts, key, old_value, new_value, actor, actor_kind, reason)` — 每次 PATCH 记录（365 天）

**相关接口**：
- `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts`

---

### GlobalLoginState DO（全局登录状态）

Singleton Durable Object（`idFromName("global")`），存储所有 runner 共享的 JavDB session cookie。AES-GCM 加密静态存储；key 派生自 `PROXY_COORDINATOR_TOKEN`。

**核心 API**：
- `GET /login_state` — 当前 cookie 持有者 + 解密后的 cookie
- `POST /login_state/acquire_lease` — re-login 互斥锁
- `POST /login_state/publish` — 持有 lease 的进程发布新 cookie
- `POST /login_state/invalidate` — 标记当前 cookie 失效
- `POST /login_state/record_attempt` — 追加一次 success/failure 记录到滚动 buffer

**dashboard 改造扩展**：
- `login_event_log(ts, kind, holder_id, ...)` — attempt / publish / invalidate / lease 流转（30 天）

**相关接口**：
- `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts`

---

### Signal（运维信号）

操作员推送给所有 runner 的**时间有界、idempotent**的运行时干预。存在于 `RunnerRegistry` DO 的 `active_signals` 字段，runner 通过 heartbeat 拉取。

**Kind（封闭集）**：
- `throttle_global` — 所有 runner 的本地 sleep 乘以 `factor`（≥1.0）
- `ban_proxy` — runner 本地从池中移除指定 proxy `ttl_ms`
- `pause_all` — 所有 runner 暂停分发新任务 `ttl_ms`
- `resume` — 覆盖性清除所有其他 active signal

**生命周期**：`create → auto-expire (TTL)` 或 `create → explicit-revoke (resume)`。所有事件写入 `signals_event_log`（见"可观测性数据"）。

**相关接口**：
- `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts` — `handleSignal()`
- `javdb/proxy/coordinator/runner_registry_client.py` — `record_signal_application()`

---

### Circuit Breaker（熔断器）

Coordinator 降级保护模式。当 coordinator（Cloudflare Worker）连续不可达时，Python 客户端自动切换到本地 throttle，避免每次请求都等待超时。

**状态转换**：
```
closed（正常）→ open（降级）→ half-open（探测）→ closed
```

- **closed** — 正常调用 coordinator `/lease`
- **open** — 连续失败超过 `_DEGRADE_THRESHOLD`（3 次）后断路，跳过 coordinator 调用，使用本地 throttle
- **half-open** — 经过 `_recovery_probe_sec`（300s）后发送探测请求
- 探测成功 → closed；探测失败 → 重新 open

**日志标识**：
- `WARNING: Circuit breaker open` — 进入降级
- `INFO: Circuit breaker half-open: probing coordinator` — 恢复探测
- `INFO: Circuit breaker closed: coordinator recovered` — 恢复正常

**相关模块**：
- `javdb/spider/runtime/sleep.py` — `MovieSleepManager.plan_sleep()`

---

### Degraded Mode（降级模式）

Coordinator 不可达时的运行状态。降级模式下：
1. Sleep 时间由本地 `TripleWindowThrottle` 决定（不调用 coordinator）
2. Throttle 窗口按 **Runner Scale** 缩放，防止多 runner 并行时全局请求量超限
3. 定期发送恢复探测（见 **Circuit Breaker**）

**相关接口**：
- `MovieSleepManager._degraded` — 当前是否处于降级
- `MovieSleepManager._degraded_since` — 进入降级的时间戳

---

### Runner Scale（Runner 缩放）

多 runner 并行时，动态调整本地 throttle 窗口限制。每个 runner 的 `TripleWindowThrottle` 的 `long_max` 和 `extra_max` 除以活跃 runner 数量，使全局聚合请求量不超过单 runner 的设计上限。

**传播路径**：
```
Worker RunnerRegistry → heartbeat response (active_runners_count)
  → Python _runner_heartbeat_loop → MovieSleepManager.set_active_runners()
    → TripleWindowThrottle.set_runner_scale()
```

**示例**：默认 `extra_max=200`，3 个 runner → 每个 runner `extra_max=66`

**相关接口**：
- `TripleWindowThrottle.set_runner_scale(active_runners)`
- `MovieSleepManager.set_active_runners(count)`
- `HeartbeatResult.active_runners_count`

---

### Sub-Shard（子分片）

MovieClaim DO 在每日分片内按 `href` 的 djb2 hash 再分为 N 个子分片（默认 4），将多 runner 的 claim 负载分散到多个 DO 实例，避免单个 DO 成为热点。

**分片命名**：
- 旧格式（遗留）：`claims-YYYY-MM-DD`
- 新格式：`claims-YYYY-MM-DD-{hash(href) % N}`

**配置**：`NUM_CLAIM_SHARDS` 环境变量（Worker 侧，默认 4；设为 1 禁用子分片）

**相关模块**：
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` — `resolveClaimShardForHref()`、`hrefShardIndex()`

---

### Fan-Out（扇出）

Session 级操作（commit / rollback / sweep）需要广播到所有子分片 + 遗留分片，确保不遗漏任何数据。Fan-out 结果合并策略：数值字段求和、`server_time` 取最大值、字符串字段取首个。

**相关接口**：
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` — `fanOutToAllClaimShards()`

---

### Maintenance Page（维护页）

JavDB 站点维护或不可用时的响应页面。维护页不应触发 CF 惩罚事件——这是站点级问题而非代理级问题。

**检测标记**（大小写不敏感）：`系統維護中`、`系统维护中`、`system maintenance`、`service unavailable`、`temporarily unavailable`、`暫時無法使用`

**启发式检测**：短页面（<2000 字符）含 502/503 状态码但不含正常内容标记（`movie-list`、`video-detail`）时也判定为维护页。

**相关接口**：
- `javdb/storage/bridges/rust_adapters/parser_adapter.py` — `is_maintenance_page()`

---

## 可观测性数据（Observability Data）

Worker 端（Cloudflare Worker / Durable Objects）的回放与审计数据，**与 Python 侧的 `MovieHistory` / `TorrentHistory` 在概念上无关**——后者是抓取去重所需的累积状态，前者是运维 dashboard 回放生产事件用。术语严格区分以避免混淆：

### Snapshots（快照）

**周期采样的时间序列数据**。每个采样点是一个完整的运行时状态快照（JSON），用 5 秒桶为主键，`INSERT OR REPLACE` 去重。

**用途**：dashboard 时序图表（latency、health score、active runners、queue depth 等）。

**写入路径（混合）**：
- Cron 1 分钟触发 + Dashboard `/ops/snapshot` 5 秒轮询（`ctx.waitUntil` 异步）
- 应用 **Idle Suppression**（见下）跳过空状态写入

**存储位置**：`MetricsState` DO（新建），表名 `metrics_snapshots`。

**保留策略**：30 天滚动 TTL + 100k 行硬上限。

### Event Log（事件日志）

**追加式的生命周期事件流**。每个事件是一个状态转换（如 signal 创建、runner 注册）。

**实例**：
- `signals_event_log`（在 `RunnerRegistry` DO）— signal create / auto-expire / explicit-revoke
- `runs_event_log`（在 `RunnerRegistry` DO）— runner register / unregister / crashed
- `login_event_log`（在 `GlobalLoginState` DO）— attempt / publish / invalidate / lease 流转

**保留策略**：signals 与 runs 90 天；login 30 天。

### Audit Log（审计日志）

**变更前后值对比的审计记录**，专用于配置变更（PATCH 操作）。

**实例**：
- `config_audit_log`（在 `ConfigState` DO）— 每次 `PATCH /config` 记录 `key, old_value, new_value, actor, actor_kind, reason, ts`

**保留策略**：365 天（变更稀疏，审计价值高）。

> **不要与已弃用的 Audit Mode（`MovieHistoryAudit` / `TorrentHistoryAudit`）混淆**。前者是 Worker 端配置变更审计；后者是 Python 端存储模式的遗留回滚机制，计划 2026-08-13 下线。两者在不同的存储后端（CF DO vs SQLite/D1）、不同的数据语义、不同的生命周期。

### Idle Suppression（空闲抑制）

Snapshots 写入的优化策略：当系统处于空闲状态时跳过周期写入，以节约 DO IO。

**Idle 定义**（全部同时满足）：
- `active_runners == 0`
- `queue_depth == 0` 且 `in_flight == 0`
- `active_signals == 0`
- 过去 5 分钟无任何 proxy 的 lease / report 活动

**边界写入规则**：
- active → idle 过渡：写一次 **Transition Marker** 让折线有清晰收尾
- idle → active 恢复：写恢复点
- 整点（:00）：写**心跳锚点**，用于 dashboard 区分"系统空闲"和"Cron 失效"

**相关模块**：
- `JAVDB_AutoSpider_Proxycoordinator/src/metrics_state.ts`（待建）

### UI 别名

**对终端用户（dashboard 文案、按钮文字、面板标题）一律使用 "History" 表达"看过去发生了什么"**——避免要求用户区分 Snapshots / Event Log / Audit Log。代码、表名、API path 必须使用精确术语。

---

## 迁移（Migrations）

### Schema Version（模式版本）

当前模式版本：`SCHEMA_VERSION = 13`

**版本历史**：
- v5 — 单数据库（`javdb_autospider.db`）
- v6 — 拆分为三个数据库（`history.db`, `reports.db`, `operations.db`）
- v7 — 添加 Actor 列到 MovieHistory
- v8 — 添加 Rollback 列（SessionId, WriteMode）
- v13 — 当前版本（包含 Pending 表）
- v14 — ADR-005：drop `MovieHistoryAudit` / `TorrentHistoryAudit`

### 版本文件布局（ADR-005 之后）

每个 schema 版本一个文件，过程式接口：

```
javdb/migrations/versions/
├── v6_split_dbs.py          → def migrate(conn) -> None
├── v7_actor_columns.py      → def migrate(conn) -> None
├── v8_rollback_columns.py   → def migrate(conn) -> None
├── ...
└── v14_drop_audit_tables.py → def migrate(conn) -> None
```

`javdb/migrations/runner.py` 作为 dispatcher，根据 `detect_schema_version()` 顺次调用版本函数。`tools/` 子目录保留 ad-hoc 维护脚本不变。

**相关接口**：
- `migrations.runner.init_db()` — 初始化全部数据库到最新版本
- `migrations.runner.detect_schema_version()` — 检测当前版本
- `migrations.versions.v6_split_dbs.migrate(conn)` — 单步迁移

---

## 架构模式（Architectural Patterns）

### Seam（接缝）

模块之间的接口边界。每个模块通过公开接口与其他模块交互，隐藏实现细节。

**示例**：
- `db_connection` 提供 `get_db()` 接口，隐藏 SQLite/D1/Dual 的路由逻辑
- `db_session` 提供 `get_active_session_id()` 接口，隐藏线程本地状态

### Adapter（适配器）

满足接口的具体实现。

**示例**：
- `sqlite3.Connection` — SQLite 适配器
- `D1Connection` — D1 适配器
- `DualConnection` — Dual 适配器

### Repo（仓储类）

ADR-005 后，数据库写入与读取统一通过 4 个 Repo 类对外暴露：

| 类 | 物理位置 | 构造签名 | 主要职责 |
|---|---|---|---|
| `HistoryRepo` | `db_layer/history_repo.py` | `(conn, session_id=None)` | Pending stage / commit / rollback、history 读取 |
| `OperationsRepo` | `db_layer/operations_repo.py` | `(conn, session_id=None)` | RcloneInventory / DedupRecords / PikpakHistory |
| `ReportsRepo` | `db_layer/reports_repo.py` | `(conn, session_id=None)` | ReportSessions / ReportMovies / ReportTorrents |
| `StatsRepo` | `db_layer/stats_repo.py` | `(conn, session_id=None)` | SpiderStats / UploaderStats / PikpakStats |

`session_id` 在写入路径上必传，读取路径可省略。绑定到构造时杀掉了 `db_session._active` 全局状态（ADR-001 Phase 3 落地）。

### Layering Invariant（分层不变量）

`javdb/**` **不依赖** `apps/**`。所有跨层依赖必须从 apps → javdb 方向单向流动；任何 javdb 内的代码若需要 URL/解析等工具，要么内联，要么放进 `javdb/spider/` 或 `javdb/infra/` 的工具模块。

> 此不变量由 ADR-005 确立、ADR-007 重命名后沿用（旧表述用的是 `packages/**`，对应当时的 `packages/python/javdb_*/` 树；Phase 3 已将其整体迁移至顶级 `javdb/`）。原始动机：旧 db.py 直接 `import apps.api.parsers.common` 是反向依赖。

### Depth（深度）

模块的"深度"由接口复杂度与实现复杂度的比值决定：
- **深模块** — 小接口，大实现（高杠杆）
- **浅模块** — 接口与实现复杂度相当（低杠杆）

**目标**：拆分后的模块应该是"深"的——每个模块提供简单的接口，隐藏复杂的实现。

### Locality（局部性）

相关的代码应该集中在一个模块中，而不是分散在多个模块。

**反例**（ADR-001 之前）：
- 理解"如何保存一条历史记录"需要阅读 `db.py`（6,370 行）的多个函数

**正例**（ADR-005 之后）：
- 理解"如何保存一条历史记录"只需阅读 `HistoryRepo` 类的方法签名 + 其 `_apply_pending()` 实现

---

## 术语对照表

| 中文 | 英文 | 说明 |
|------|------|------|
| 会话 | Session | 一次 pipeline 运行 |
| 写入模式 | Write Mode | 当前 `pending` 或 `audit`；ADR-005 后只剩 `pending` |
| 待定暂存 | Pending Stage | 写入先进入 Pending 表 |
| 审计轨迹 | Audit Trail | 保存旧行的 JSON；ADR-005 后退役 |
| 存储后端 | Storage Backend | `sqlite`, `d1`, `dual` |
| 漂移 | Drift | Dual 模式下的数据不一致 |
| 回滚 | Rollback | 撤销 Session 的所有写入 |
| 孤儿审计 | Orphan Audit | 已提交 Session 的残留审计行；ADR-005 后退役 |
| 接缝 | Seam | 模块之间的接口边界 |
| 适配器 | Adapter | 满足接口的具体实现 |
| 仓储类 | Repo | `HistoryRepo`/`OperationsRepo`/`ReportsRepo`/`StatsRepo`，`(conn, session_id)` 构造 |
| 分层不变量 | Layering Invariant | javdb 不依赖 apps，单向流动 |
| 版本迁移文件 | Version Migration File | `v{N}_*.py` 暴露 `migrate(conn) -> None` |
| 深度 | Depth | 接口简单度与实现复杂度的比值 |
| 局部性 | Locality | 相关代码的集中程度 |
| 熔断器 | Circuit Breaker | Coordinator 连续失败后断路保护 |
| 降级模式 | Degraded Mode | Coordinator 不可达时的本地 throttle 回退 |
| Runner 缩放 | Runner Scale | 按活跃 runner 数缩放 throttle 窗口 |
| 子分片 | Sub-Shard | 按 href hash 细分 MovieClaim DO 实例 |
| 扇出 | Fan-Out | Session 操作广播到所有子分片 |
| 维护页 | Maintenance Page | 站点维护响应（不触发 CF 惩罚） |
| 运行者 | Runner | Worker 端进程注册单位（holder_id 维度，≠ Session） |
| 运行者注册表 | RunnerRegistry DO | 跨 isolate 唯一的 Runner / Signal 注册表 |
| 动态配置 | ConfigState DO | 运行时可调全局配置 + override |
| 全局登录状态 | GlobalLoginState DO | 共享 JavDB session cookie + re-login 互斥 |
| 运维信号 | Signal | 操作员推送的运行时干预（throttle/ban/pause/resume） |
| 快照 | Snapshots | Worker 端周期采样的时序数据（≠ MovieHistory） |
| 事件日志 | Event Log | Worker 端追加式生命周期事件流 |
| 审计日志 | Audit Log | Worker 端配置变更审计（≠ 已弃用的 Audit Mode） |
| 空闲抑制 | Idle Suppression | Snapshots 写入跳过策略 |
| 过渡标记 | Transition Marker | active/idle 边界写入的折线收尾点 |

---

## 相关文档

- [CLAUDE.md](../../CLAUDE.md) — 项目概览和开发指南
- [docs/handbook/en/ops/d1-rollback.md](../handbook/en/ops/d1-rollback.md) — 存储后端架构和回滚流程
- GitHub Wiki（自动从 `docs/handbook/en/` 同步生成）— 完整的数据库模式
