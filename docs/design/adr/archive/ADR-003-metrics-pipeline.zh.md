# ADR-003: Metrics Pipeline（混合写入 + 空闲抑制）

**状态**: 已完成 2026-05-17 —— `MetricsState` DO + 每分钟 scheduled cron（`* * * * *`）+ 空闲抑制（`is_transition_marker` / `is_heartbeat_anchor` 列）+ 5 秒 bucket JSON 快照按决策矩阵全部落地。
**日期**: 2026-05-16
**决策者**: Proxy Coordinator Dashboard 改造
**关联实现计划 (Related Implementation Plans)**: [IMP-ADR003-01](../../impl/IMP-ADR003-01-dashboard-phase2-worker-backend.md)（worker backend——pipeline 已交付）、[IMP-ADR003-02](../../impl/IMP-ADR003-02-dashboard-phase3-ui.md)（主仪表盘 UI 消费方——计划中）

---

## 背景 (Context)

Dashboard 需要展示时序图表（latency / health score / active runners / queue depth / CF-bypass 占比 / per-proxy 多线趋势 等）。当前 Worker 端**所有 DO 只保存"当前快照"**，没有历史时序数据。

要画时序图，必须把每个时间点的状态持久化为一连串采样点。设计空间有 4 个相互独立的轴：

1. **数据源粒度**：客户端浏览器内存累积 vs Worker DO 持久化
2. **写入触发器**：Cron 定时拉取 vs Dashboard 轮询副作用 vs Runner 自上报 vs 混合
3. **采样间隔**：5 秒 / 30 秒 / 1 分钟 / 更长
4. **存储 schema**：单行 JSON 快照 vs 多行按 metric 拆分

### 关键约束

- **生产时间分布严重不均**：GH Actions 跑批一天活跃约 6 小时，其余 18 小时几乎完全空闲（active_runners=0）
- **Cloudflare Cron 最小间隔 1 分钟**（不能 30 秒）
- **DO IO 是计费项**：CF DO SQLite 按"rows written / read"计费，需要节省
- **dashboard 主要价值在 active 时段**：空闲时段的"全零"数据点对运维无信息

---

## 决策 (Decision)

### 选择：Worker DO 持久化 + 混合写入 + JSON 快照 + 空闲抑制

具体配置：

| 轴 | 选定 |
|---|---|
| 数据源粒度 | **新建 MetricsState DO 持久化** |
| 写入触发器 | **Cron 1 分钟 + Dashboard 5 秒轮询副作用** |
| 采样间隔 | **5 秒 bucket** (`floor(now_ms/5000)*5000`) |
| 存储 schema | **单行 JSON 快照** (`metrics_snapshots(ts INTEGER PK, payload TEXT, source TEXT)`) |
| 去重策略 | **`INSERT OR REPLACE`** —— 同 5 秒 bucket 内最后一次写覆盖 |
| 空闲抑制 | **active 才写；active↔idle 边界写 transition；整点写心跳锚点** |
| Retention | **30 天滚动 TTL + 100k 行硬上限** |

### Idle 判定（全部同时满足）
```
active_runners == 0
  AND queue_depth == 0 AND in_flight == 0
  AND active_signals == 0
  AND (过去 5 分钟无任何 proxy 的 lease / report 活动)
```

### 写入决策矩阵
| 上 tick | 本 tick | 行为 |
|---|---|---|
| active | active | ✅ 写 |
| active | idle | ✅ 写 **Transition Marker**（折线收尾） |
| idle | active | ✅ 写恢复点 |
| idle | idle | ❌ **跳过**（主要省 IO 场景） |
| any | any（当前为整点 :00） | ✅ 写心跳锚点 |

---

## 备选方案 (Alternatives Considered)

### 备选 A：浏览器内存累积，不持久化

dashboard JS 在内存里 ring buffer 累积每次轮询的数据；只显示开窗后的时段；刷新归零。

**优点**：实现极轻量；零 Worker storage 成本；零 IO
**缺点（不选的原因）**：
- **History 需求（grill-me Q5）要求跨刷新可见**——浏览器内存归零破坏审计价值
- 多操作员同时打开 dashboard 看到不同的"历史"
- 关闭后历史立刻丢失，事后分析无法回溯

### 备选 B：纯 Cron 1 分钟触发写入

只在 cron alarm 里采样。完全解耦于 dashboard 使用情况。

**优点**：实现最干净；不耦合主请求路径
**缺点（部分不选的原因）**：
- 1 分钟分辨率对 active 时段不够细——latency 抖动看不清
- 实际上 1 分钟分辨率已经够用，**这是和 (IV) 混合方案的主要 trade-off**——参见"原因"

### 备选 C：Runner 自上报（每次 heartbeat 推送 metrics）

Runner 每次 heartbeat（约 15 秒）推送自己视角的指标到 MetricsState DO。

**优点**：天然高分辨率；不依赖 dashboard
**缺点（不选的原因）**：
- 多 runner 同时上报需要去重逻辑（取最大？平均？）—— 复杂
- 每次 heartbeat 多一次 DO 写入，主路径成本上升
- 视角是 runner 本地的，不是 Worker 全局状态（health score 是 ProxyCoordinator DO 算的，不是 runner 能直接看到的）

### 备选 D：每 metric 一行的结构化 schema

`metrics(ts, metric_kind, dim_key, value)` 多 row 存储。

**优点**：按 metric 精确查询，走 SQL index 高效
**缺点（不选的原因）**：
- **写放大严重**：10 proxies × 5 metrics + 4 global = ~54 rows/tick；与 (P) 单行 JSON 的 1 row/tick 相差 54 倍
- Schema 演化需要 migration（加新 metric 就要 ALTER TABLE）
- dashboard 主要用法是"读一个时段的全图"，并不需要按 metric 精挑

---

## 为什么选 (IV) 混合写入而不是纯 Cron

最初决策是纯 Cron 1 分钟。讨论后改成 Cron + Dashboard 5 秒混合，原因：

- **Cron 保证基线**：即使 dashboard 没人开，过去活跃时段的 1 分钟分辨率历史已经写入。事后回看不会断档。
- **Dashboard 加密 5 秒**：操作员盯盘时获得 5 秒分辨率（看 latency 抖动有用）。
- **5 秒 bucket 主键 + INSERT OR REPLACE 自然去重**：Cron 和 Dashboard 偶然撞在同一桶里时，最后一次写覆盖前一次，无并发冲突。
- **dashboard 写入用 `ctx.waitUntil()` 异步**：不阻塞 `/ops/snapshot` 主响应路径。
- **空闲抑制对两者都生效**：dashboard 开着但系统空闲时也不写。

---

## 实现策略 (Implementation)

### Phase 2：基础设施

1. 创建 `MetricsState` DO 类（`src/metrics_state.ts`），实现 `recordSnapshot(payload, source)` + `queryRange(fromTs, toTs)` + GC alarm 跑 retention sweep
2. `wrangler.toml`：
   - 新增 DO binding `METRICS_STATE_DO`
   - 添加 cron trigger `* * * * *`（每分钟）
3. Worker `scheduled` handler：每分钟拉取 `aggregateOpsSnapshot()` 结果，调用 `MetricsState.recordSnapshot(..., 'cron')`
4. `/ops/snapshot` 末尾用 `ctx.waitUntil(metricsState.recordSnapshot(..., 'dashboard'))` fire-and-forget

### Phase 3：dashboard 接入

新增 `GET /metrics/range?from=...&to=...` 端点；dashboard JS 在选定时间范围时拉取并喂给 uPlot。

### 测试覆盖

- `test/metrics_state.test.ts`：
  - 5 秒 bucket 去重
  - idle 跳过逻辑
  - transition marker 写入
  - 心跳锚点写入
  - retention sweep 删除超期行
  - 100k 行硬上限触发清理

---

## 后果 (Consequences)

### 正面影响

1. **跨刷新可见的真实历史**：操作员关闭 dashboard 也不丢数据
2. **空闲时段几乎零 IO**：估算从 1440 写/天降到 ~320 写/天（约 -78%）
3. **5 秒分辨率（active+dashboard 时）+ 1 分钟分辨率（active 但无 dashboard 时）+ 心跳锚点**——多档采样自然适配场景
4. **统一 schema 简单**：单行 JSON 快照，未来加 metric 不需要 migration

### 负面影响

1. **新增 1 个 DO + 1 个 cron trigger**：部署复杂度小幅上升
2. **JSON 解析成本**：dashboard 读 24h 历史 = 解析 ~1440 个 JSON object（active 全开情况下），轻量但非零
3. **dashboard 主路径增加一次 DO 写入**（`waitUntil` 异步，不阻塞响应但消耗 CPU 配额）

### 风险

1. **Cron 触发未触发**（CF 偶尔丢 cron 调度）→ history 出现空洞
   - **缓解**：整点心跳锚点；dashboard 5 秒采样冗余兜底
2. **5 秒 bucket 在跨 isolate 时钟漂移下偶尔错位**
   - **缓解**：CF Worker 时钟同步精度 < 1 秒，5 秒 bucket 容忍度足够
3. **MetricsState DO 存储意外膨胀**（idle suppression 失效 + 30 天 TTL 失效双重故障）
   - **缓解**：100k 行硬上限；GC alarm 持续扫描

---

## 相关决策 (Related Decisions)

- **ADR-002**：可观测性数据存储拓扑（为什么 metrics 单独一个 DO 而不是合并到 RunnerRegistry）
- **ADR-004**：Runner 上报 PROXY_POOL（影响 `metrics_snapshots` payload 里的 proxies 字段）

---

## 参考资料 (References)

- [CONTEXT.md](../../../../CONTEXT.md) — Snapshots / Idle Suppression / Transition Marker 定义
- Cloudflare Cron Triggers minimum interval: <https://developers.cloudflare.com/workers/configuration/cron-triggers/>
- Cloudflare DO SQLite pricing model: <https://developers.cloudflare.com/durable-objects/platform/pricing/>
