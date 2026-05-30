# ADR-034：媒体闭环 Web 面

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — ADR-033 的 web 对应面;分期镜像 ADR-033                     |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-008](../_archive/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md), [ADR-027](../_archive/ADR-027-Stats-Dashboard-Charts/ADR-027-stats-dashboard-chart-expansion.md), [ADR-030](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

> 源自 2026-05-29 一次(用了视觉伴侣的)关于
> [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) 媒体闭环 web 面的头脑风暴。

## 背景 (Context)

[ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) 构建了三层媒体闭环,并落到新的 D1 表（`AcquisitionOutcome`,以及后续的 `OwnershipLedger`、`ConsumptionSignal`）。这些数据目前**不可见**——没有任何 web 面承载它。运维者跑着 Vue 控制台（`javdb-autospider-web`,ADR-008），却看不到昨晚选中的种子有没有真正落地、跨源拥有了什么、看了什么。

web 平台是**一个 Vue 前端背后两套后端**（ADR-017）:一套 TypeScript Worker（Hono,在 Cloudflare 上查 D1），一套 Python FastAPI（全本地执行）。ADR-018/030 要求二者重叠的查询面保持 parity。因此闭环的任何新只读面都必须设计成**双后端契约**,而非单后端特性。

本 ADR 定义闭环的 **web 面**:导航放置、Phase 1 的 Acquisition 视图、双后端只读端点、能力门控。它分期镜像 ADR-033——Phase 1 只有 `AcquisitionOutcome`,所以先只交付 Acquisition 视图。

## 决策 (Decision)

为 Vue 控制台新增一个顶级 **Library** 页面,含三个子 tab（Acquisition / Ownership / Consumption），由**双后端、只读**端点（读 ADR-033 的 D1 表）支撑,并由 `closed_loop` 能力 flag 门控。先交付 Acquisition 视图;Ownership 与 Consumption 跟随 ADR-033 的 Phase 2、3。

### 设计决策 (Design Decisions)

**D1. 新建顶级「Library」页面,含三个子 tab。** 闭环是一个独立领域（"我获取了/拥有了/看了什么"），不是"更多 stats",所以给它自己的页面而非塞进 Stats 仪表盘。子 tab:`Acquisition`（Phase 1）、`Ownership`（Phase 2,禁用占位）、`Consumption`（Phase 3,禁用占位）。否决了"塞进 Stats"（把内容领域埋进分析里）与"按用途拆到 Tasks+Library"（一个故事两个家）。

**D2. Acquisition 视图只读:漏斗 + KPI 卡 + 近期表。** 一条水平漏斗（`queued → downloading → completed`）、五张 KPI 卡（queued / downloading / completed / stalled / failed）、一张带状态 chip 与状态筛选的近期 `NDataTable`。**Phase 1 无写操作**——`Re-queue`/`Dismiss` 明确推迟（它们要重加 qB,只有能直连 LAN 的 Python 后端能做;见非目标）。

**D3. 只读端点双后端 full parity。** 三个只读端点在 **TS Worker**（`server/routes/library.ts`）与 **Python 后端**（`apps/api/routers/library.py`）**各实现一份**,执行对 D1 `AcquisitionOutcome` 表相同的 SQL,走现有 JWT 认证中间件:

| 端点 | 返回 | 驱动 |
| --- | --- | --- |
| `GET /api/library/acquisition/summary` | `{queued, downloading, completed, stalled, failed, total}` | KPI 卡 + 漏斗 |
| `GET /api/library/acquisition/recent?state=&limit=&offset=` | `[{qb_hash, video_code, href, category, state, queued_at, completed_at, last_seen_at}]` | 近期表 |
| `GET /api/library/acquisition/trend?period=30d` | `[{date, completed, stalled, failed}]` | 可选趋势图（ADR-027 trend 形状） |

形状写进 `openapi.json`（由 Python 应用生成,供 TS 前端生成类型），所以两套后端与前端共享同一份契约。这在双向上都遵守 ADR-018/030 的 parity 规则。

**D4. 用 `closed_loop` flag 做能力门控——能力诚实。** `GET /api/capabilities` 新增一个 `closed_loop` 布尔（当 `AcquisitionOutcome` 表存在 / reconcile 已配置时为 true）。前端在 flag 为 false 时隐藏 Library 导航项,这样没建闭环表的部署绝不会看到坏页面。这延续 ADR-008 D5 的能力驱动发现模式。

**D5. 前端分期镜像 ADR-033。** FE Phase 1 交付 Library 骨架 + Acquisition 视图 + 三个只读端点。FE Phase 2 加 Ownership 视图（当 `OwnershipLedger` 落地）。FE Phase 3 加 `(instance, library)` 粒度的 Consumption 视图（当 `ConsumptionSignal` 落地）。禁用的占位 tab 让路线图对运维者一目了然。

**D6. i18n parity（en + zh）覆盖全部新字符串。** 每个新标签、KPI 标题、状态 chip、表头都在同一次改动里加进 `en` 与 `zh` 两个 locale 文件——翻译漂移是缺陷（与仓库双语规则一致）。

**D7. 跨仓边界显式。** Vue 组件与 TS 路由落独立仓 `javdb-autospider-web`;Python 路由落本仓（`apps/api/routers/library.py`）;`openapi.json` 是接缝。IMP 会逐一列明哪个文件落哪个仓,以免被误当成单仓改动。

## 后果 (Consequences)

### 正面 (Positive)

- 闭环数据变**可见**——运维者终于能看到选中的种子有没有落地、卡在哪。
- **天生 parity**——一份契约（`openapi.json`），两个后端 adapter;两种部署拓扑（Cloudflare 控制台、本地 Docker）都能服务该页。
- **能力诚实**——页面只在能被服务的地方出现。
- **可加且可读**——Ownership/Consumption 随 ADR-033 Phase 2/3 落地而嵌入同一页;禁用 tab 预告即将到来的内容。

### 负面 (Negative)

- **同一套 SQL 两份实现**（TS + Python）——正是 2026-05-29 架构评审点名的 Candidate B parity 代价;此处作为 ADR-018/030 现状接受,直到出现共享查询接缝。
- **多一个能力 flag 要打通**（capabilities + 前端门控）。
- **先只读**——想从 UI 重排卡住种子的运维者,得等后续 Python-only 的 actions 增量。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| FE Phase 1 — Acquisition | IMP-ADR034-01（占位） | Library 页骨架（3 tab,2 禁用）;Acquisition 视图（漏斗 + KPI + 近期表,只读）;`GET /api/library/acquisition/{summary,recent,trend}` 于**两套**后端;`closed_loop` 能力 flag + 导航门控;en/zh 字符串 | Ownership/Consumption 视图;任何写操作 |
| FE Phase 2 — Ownership | IMP-ADR034-02（占位） | 基于 `OwnershipLedger` 的 Ownership 视图 | — |
| FE Phase 3 — Consumption | IMP-ADR034-03（占位） | 基于 `ConsumptionSignal` 的 `(instance, library)` 粒度 Consumption 视图 | — |

FE Phase 1 只依赖 ADR-033 Phase 1（`AcquisitionOutcome`）。Phase 2/3 依赖 ADR-033 Phase 2/3,待其落地后再细化。

**规划节奏。** 暂未写任何 FE IMP。**IMP-ADR034-01 刻意推迟**,直到 ADR-033 Phase 1（`AcquisitionOutcome` + 只读端点的数据）落地;它的详细计划——以及 IMP-ADR034-02/03——将在对应后端阶段交付后,用一轮 `grill-me` + `brainstorming` 产出,使 FE 计划反映真实端点形状而非纸面契约。

### 明确的非目标 (YAGNI)

- **Phase 1 无写操作**——无 `Re-queue` / `Dismiss` / `Open in qB`。它们需要直连 LAN 的 Python 后端,是后续清晰定界的 actions 增量,不属于只读面。
- **无实时**——页面在加载/手动刷新时取数;无 websocket。
- **Phase 3 前不做 Emby/Plex 逐库下钻**（`ConsumptionSignal` 之前数据不存在）。
- **不引入新图表库**——复用 `vue-chartjs`（Chart.js），ADR-027 已在用。

## 备选方案 (Alternatives Considered)

- **把闭环塞进 Stats 仪表盘**（放置 B）——否决（D1）:把内容领域埋进分析 tab。
- **按用途拆分:漏斗进 Tasks,拥有/消费进 Library**（放置 C）——否决（D1）:一个连贯故事两个家。
- **Phase 1 就做可操作运维台**（布局 B）——否决（D2）:写操作要 Python 后端,且在全新 reconcile 循环之上叠加风险。
- **仅 TS 或仅 Python 端点**——否决（D3）:破坏 ADR-018/030 parity;页面在另一种部署拓扑会 404。

## 参考 (References)

- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-008 — Frontend Rewrite](../_archive/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md)
- [ADR-027 — Stats Dashboard Chart Expansion](../_archive/ADR-027-Stats-Dashboard-Charts/ADR-027-stats-dashboard-chart-expansion.md)
- [ADR-030 — Web Feature Parity](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md)
- [ADR-017 — Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed（ADR-033 的 web 对应面;FE Phase 1 已定界,IMP 待出）。
- 2026-05-29: IMP-ADR034-01 推迟到 ADR-033 Phase 1 落地后;FE IMP 待后端落地后的
  一轮 `grill-me` + `brainstorming` 细化。
