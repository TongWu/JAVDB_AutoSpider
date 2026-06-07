# ADR-027: 统计仪表板图表扩展

| 字段       | 值                                       |
| ---------- | ---------------------------------------- |
| **状态**   | 已完成 — 实现已于 2026-05-28 交付        |
| **创建**   | 2026-05-28                               |
| **作者**   | Ted                                      |
| **关联**   | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

## 背景

当前 StatsPage（`src/pages/stats/StatsPage.vue`）有 8 个汇总卡片和 8 个趋势图表，分布在 3 个 Tab（Runs、Growth、System）中。所有图表使用 `vue-chartjs`（Chart.js），通过 TS 后端（`server/routes/stats.ts`）的两个端点获取数据：

- `GET /api/stats/summary` — 聚合计数
- `GET /api/stats/trend?metric=X&period=Y` — 时间序列 `{date, value}[]`

以下数据表**尚未**在仪表板中展示：

| 表名               | 数据库      | 未展示的关键字段                                                                        |
| ------------------ | ----------- | -------------------------------------------------------------------------------------- |
| SpiderStats        | REPORTS_DB  | Phase1/Phase2 Discovered, Processed, Skipped, NoNew, Failed                            |
| UploaderStats      | REPORTS_DB  | TotalTorrents, DuplicateCount, SuccessRate, SubtitleCount, NoSubtitleCount              |
| PikpakStats        | REPORTS_DB  | SuccessfulCount, FailedCount, DeleteFailedCount                                         |
| ReportMovies       | REPORTS_DB  | Rate, CommentNumber                                                                     |
| TorrentHistory     | HISTORY_DB  | SubtitleIndicator, CensorIndicator, ResolutionType, Size, FileCount                     |
| MovieHistory       | HISTORY_DB  | PerfectMatchIndicator, HiResIndicator                                                   |
| EmailNotification  | OPS_DB      | Status (sent/failed/resent)                                                             |
| OpsIncidents       | REPORTS_DB  | incident_type, status, confidence                                                       |

本 ADR 为统计仪表板新增约 15 个图表，采用扩展的 Tab + 子 Tab 结构组织。

## 决策

### Tab 结构

```
StatsPage
├── Runs（现有，扩展）
│   ├── Overview — 成功率、平均耗时、每日电影、每日种子（现有 4 图）
│   └── Spider Detail — 处理分解、发现效率、跳过率、失败率（A1-A4）
├── Content（新增）
│   ├── Quality — 平均评分趋势、评分分布（B1-B2）
│   └── Coverage — 字幕覆盖率、分辨率分布、HiRes/PerfectMatch（B3-B5）
├── Upload（新增）
│   ├── qBittorrent — 上传成功率、重复率（C1-C2）
│   └── PikPak — 成功率、失败分析（C3-C4）
├── Growth（现有，不变）
│   └── History Growth、PikPak Volume（2 图）
└── System（现有，扩展）
    ├── Infrastructure — 代理封禁、去重释放（现有 2 图）
    └── Operations — 邮件通知、运维事件（D1-D2）
```

- 主 Tab 使用 `NTabs type="line"`（与当前设计一致）。
- 子 Tab 使用 `NTabs type="segment"` 以在视觉上区分层级。
- 仅有一个分组的 Tab（Growth）跳过子 Tab 层。

### 图表规格

#### A. Spider Detail（Runs > Spider Detail）

| ID  | 图表名称             | 类型         | SQL 逻辑（REPORTS_DB）                                                                    | Y 轴     |
| --- | -------------------- | ------------ | ---------------------------------------------------------------------------------------- | -------- |
| A1  | 运行处理分解          | 堆叠柱状图    | 每日 SUM(TotalProcessed), SUM(TotalSkipped), SUM(TotalNoNew), SUM(TotalFailed)             | 计数     |
| A2  | 发现效率             | 折线图（面积） | 每日 SUM(TotalProcessed) / NULLIF(SUM(TotalDiscovered),0)                                  | %（0-100）|
| A3  | 跳过率               | 折线图（面积） | 每日 SUM(TotalSkipped) / NULLIF(SUM(TotalDiscovered),0)                                    | %（0-100）|
| A4  | 失败率               | 折线图（面积） | 每日 SUM(TotalFailed) / NULLIF(SUM(TotalDiscovered),0)                                     | %（0-100）|

**A1 堆叠颜色：** Processed（绿 `#18a058`）、Skipped（灰 `#a0a0a0`）、NoNew（蓝 `#6395ff`）、Failed（红 `#d03050`）。

#### B. Content

**子 Tab "Quality"：**

| ID  | 图表名称             | 类型           | SQL 逻辑（REPORTS_DB / HISTORY_DB）                                                                   | Y 轴     |
| --- | -------------------- | -------------- | ---------------------------------------------------------------------------------------------------- | -------- |
| B1  | 平均评分趋势          | 折线图（面积）  | 每日 AVG(rm.Rate) FROM ReportMovies rm JOIN ReportSessions rs WHERE rm.Rate > 0                       | 0-10     |
| B2  | 评分分布             | 柱状图（直方图）| COUNT 按桶 (0-2, 2-4, 4-6, 6-8, 8-10) FROM ReportMovies WHERE Rate > 0，按 period 过滤               | 计数     |

**子 Tab "Coverage"：**

| ID  | 图表名称               | 类型         | SQL 逻辑                                                                                            | Y 轴     |
| --- | ---------------------- | ------------ | --------------------------------------------------------------------------------------------------- | -------- |
| B3  | 字幕覆盖率             | 折线图（面积） | 每日 SUM(SubtitleCount) / NULLIF(SUM(SubtitleCount+NoSubtitleCount),0) from UploaderStats             | %（0-100）|
| B4  | 分辨率分布             | 环形图        | COUNT(*) GROUP BY ResolutionType from TorrentHistory，按 period 过滤                                  | 计数     |
| B5  | HiRes/PerfectMatch 比例 | 折线图（双线） | 每日 AVG(HiResIndicator)*100, AVG(PerfectMatchIndicator)*100 from MovieHistory                       | %（0-100）|

#### C. Upload

**子 Tab "qBittorrent"：**

| ID  | 图表名称           | 类型         | SQL 逻辑（REPORTS_DB）                                                                     | Y 轴     |
| --- | ------------------ | ------------ | ------------------------------------------------------------------------------------------ | -------- |
| C1  | 上传成功率          | 折线图（面积） | 每日 AVG(SuccessRate) from UploaderStats JOIN ReportSessions                                 | %（0-100）|
| C2  | 种子重复率          | 折线图（面积） | 每日 SUM(DuplicateCount) / NULLIF(SUM(TotalTorrents),0) from UploaderStats                   | %（0-100）|

**子 Tab "PikPak"：**

| ID  | 图表名称              | 类型         | SQL 逻辑（REPORTS_DB）                                                                              | Y 轴     |
| --- | --------------------- | ------------ | --------------------------------------------------------------------------------------------------- | -------- |
| C3  | PikPak 成功率          | 折线图（面积） | 每日 SUM(SuccessfulCount) / NULLIF(SUM(TotalTorrents),0) from PikpakStats JOIN ReportSessions        | %（0-100）|
| C4  | PikPak 失败详情        | 堆叠柱状图    | 每日 SUM(FailedCount), SUM(DeleteFailedCount) from PikpakStats JOIN ReportSessions                   | 计数     |

#### D. Operations（System > Operations）

| ID  | 图表名称            | 类型         | SQL 逻辑                                                                                   | Y 轴   |
| --- | ------------------- | ------------ | ------------------------------------------------------------------------------------------ | ------ |
| D1  | 邮件通知状态         | 堆叠柱状图    | 每日 COUNT per Status (sent/failed/resent) from EmailNotificationHistory                    | 计数   |
| D2  | 运维事件            | 柱状图        | 每日 COUNT(*) from OpsIncidents GROUP BY DATE(created_at)                                   | 计数   |

### API 变更

#### 扩展 `GET /api/stats/trend`

新增 `VALID_METRICS`：

```
spider_processed, spider_skipped, spider_nonew, spider_failed,
spider_efficiency, spider_skip_rate, spider_failure_rate,
avg_rating,
subtitle_coverage,
hires_ratio, perfectmatch_ratio,
upload_success_rate, duplicate_rate,
pikpak_success_rate, pikpak_failed, pikpak_delete_failed,
email_sent, email_failed, email_resent,
ops_incidents
```

全部返回现有的 `TrendResponse` 格式：`{ metric, period, data_points: {date, value}[] }`。

**多系列图表**（A1、C4、D1）每个系列独立调用一次 trend 接口，在前端合并为一个 Chart.js datasets 数组。这保持了 API 接口的简洁性，每次查询都很轻量。

#### 新增 `GET /api/stats/distribution`

```typescript
interface DistributionResponse {
  metric: string
  period: string
  buckets: Array<{ label: string; value: number }>
}
```

支持的指标：
- `rating_distribution` — 桶：`["0-2", "2-4", "4-6", "6-8", "8-10"]`
- `resolution_distribution` — 桶：从数据动态生成（如 `["SD", "720p", "1080p", "4K"]`）

接受 `period` 参数按时间范围过滤（与 trend API 一致）。

### 前端实现

- **图表库：** 继续使用 `vue-chartjs`（Chart.js），已安装。
- **新增 Chart.js 注册：** 为 B4 添加 `DoughnutController`、`ArcElement`。
- **子 Tab 组件：** 使用 `NTabs type="segment"` 实现主 Tab 内的子 Tab 导航。
- **懒加载：** 每个子 Tab 在激活时获取数据（与当前 Tab 切换模式一致）。
- **Period 选择器：** 全 Tab 共享（现有行为，不变）。

### ResolutionType 值映射

`TorrentHistory` 中的 `ResolutionType` 列存储整数值。B4（分辨率分布）图表的显示标签：

| 值   | 标签  |
| ---- | ----- |
| 0    | SD    |
| 1    | 720p  |
| 2    | 1080p |
| 3    | 4K    |

未知值显示为 "Other"。

## 影响

- **新增约 15 个图表**，覆盖爬虫效率、内容质量、上传表现和运维健康四个类别。
- **新增 1 个 API 端点**（`/api/stats/distribution`）用于非时间序列数据。
- **新增约 20 个趋势指标**到现有的 `/api/stats/trend`。
- StatsPage 从约 760 行增长到约 1500+ 行。如维护性成为问题，后续可考虑将 Tab 内容拆分为独立组件。
- **无需数据库 schema 变更** — 所有图表使用现有表。
