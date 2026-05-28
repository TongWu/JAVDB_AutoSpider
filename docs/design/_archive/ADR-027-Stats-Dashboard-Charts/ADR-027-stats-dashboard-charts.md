# ADR-027: Stats Dashboard Chart Expansion

| Field       | Value                                    |
| ----------- | ---------------------------------------- |
| **Status**  | Completed — implementation delivered 2026-05-28 |
| **Created** | 2026-05-28                               |
| **Author**  | Ted                                      |
| **Related** | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

## Context

The current StatsPage (`src/pages/stats/StatsPage.vue`) has 8 summary cards and 8 trend charts across 3 tabs (Runs, Growth, System). All charts use `vue-chartjs` (Chart.js) and fetch data from the TS backend (`server/routes/stats.ts`) via two endpoints:

- `GET /api/stats/summary` — aggregate counts
- `GET /api/stats/trend?metric=X&period=Y` — time-series `{date, value}[]`

Several rich data tables are **not surfaced** in the dashboard:

| Table              | DB          | Key fields not yet charted                                                             |
| ------------------ | ----------- | -------------------------------------------------------------------------------------- |
| SpiderStats        | REPORTS_DB  | Phase1/Phase2 Discovered, Processed, Skipped, NoNew, Failed                            |
| UploaderStats      | REPORTS_DB  | TotalTorrents, DuplicateCount, SuccessRate, SubtitleCount, NoSubtitleCount              |
| PikpakStats        | REPORTS_DB  | SuccessfulCount, FailedCount, DeleteFailedCount                                         |
| ReportMovies       | REPORTS_DB  | Rate, CommentNumber                                                                     |
| TorrentHistory     | HISTORY_DB  | SubtitleIndicator, CensorIndicator, ResolutionType, Size, FileCount                     |
| MovieHistory       | HISTORY_DB  | PerfectMatchIndicator, HiResIndicator                                                   |
| EmailNotification  | OPS_DB      | Status (sent/failed/resent)                                                             |
| OpsIncidents       | REPORTS_DB  | incident_type, status, confidence                                                       |

This ADR adds ~15 new charts to the Stats dashboard, organized into an expanded tab structure with sub-tabs.

## Decision

### Tab Structure

```
StatsPage
├── Runs (existing, expanded)
│   ├── Overview — success rate, avg duration, daily movies, daily torrents (existing 4)
│   └── Spider Detail — phase breakdown, discovery efficiency, skip rate, failure rate (A1-A4)
├── Content (new)
│   ├── Quality — avg rating trend, rating distribution (B1-B2)
│   └── Coverage — subtitle coverage, resolution distribution, HiRes/PerfectMatch (B3-B5)
├── Upload (new)
│   ├── qBittorrent — upload success rate, duplicate rate (C1-C2)
│   └── PikPak — success rate, failure analysis (C3-C4)
├── Growth (existing, unchanged)
│   └── History Growth, PikPak Volume (2 charts)
└── System (existing, expanded)
    ├── Infrastructure — proxy bans, dedup freed (existing 2)
    └── Operations — email notifications, ops incidents (D1-D2)
```

- Main tabs use `NTabs type="line"` (consistent with current design).
- Sub-tabs use `NTabs type="segment"` to visually distinguish hierarchy.
- Tabs with a single group (Growth) skip the sub-tab layer.

### Chart Specifications

#### A. Spider Detail (Runs > Spider Detail)

| ID  | Chart                   | Type         | SQL Logic (REPORTS_DB)                                                                   | Y-Axis   |
| --- | ----------------------- | ------------ | ---------------------------------------------------------------------------------------- | -------- |
| A1  | Run Processing Breakdown | Stacked Bar  | Daily SUM of TotalProcessed, TotalSkipped, TotalNoNew, TotalFailed from SpiderStats       | Count    |
| A2  | Discovery Efficiency    | Line (area)  | Daily SUM(TotalProcessed) / NULLIF(SUM(TotalDiscovered),0)                                | % (0-100)|
| A3  | Skip Rate               | Line (area)  | Daily SUM(TotalSkipped) / NULLIF(SUM(TotalDiscovered),0)                                  | % (0-100)|
| A4  | Failure Rate            | Line (area)  | Daily SUM(TotalFailed) / NULLIF(SUM(TotalDiscovered),0)                                   | % (0-100)|

**A1 stack colors:** Processed (green `#18a058`), Skipped (gray `#a0a0a0`), NoNew (blue `#6395ff`), Failed (red `#d03050`).

#### B. Content

**Sub-tab "Quality":**

| ID  | Chart                  | Type           | SQL Logic (REPORTS_DB / HISTORY_DB)                                                                  | Y-Axis   |
| --- | ---------------------- | -------------- | ---------------------------------------------------------------------------------------------------- | -------- |
| B1  | Avg Rating Trend       | Line (area)    | Daily AVG(rm.Rate) FROM ReportMovies rm JOIN ReportSessions rs WHERE rm.Rate > 0                      | 0-10     |
| B2  | Rating Distribution    | Bar (histogram)| COUNT per bucket (0-2, 2-4, 4-6, 6-8, 8-10) from ReportMovies WHERE Rate > 0, filtered by period     | Count    |

**Sub-tab "Coverage":**

| ID  | Chart                    | Type         | SQL Logic                                                                                           | Y-Axis   |
| --- | ------------------------ | ------------ | --------------------------------------------------------------------------------------------------- | -------- |
| B3  | Subtitle Coverage        | Line (area)  | Daily SUM(SubtitleCount) / NULLIF(SUM(SubtitleCount+NoSubtitleCount),0) from UploaderStats           | % (0-100)|
| B4  | Resolution Distribution  | Doughnut     | COUNT(*) GROUP BY ResolutionType from TorrentHistory, filtered by period                             | Count    |
| B5  | HiRes/PerfectMatch Ratio | Line (dual)  | Daily AVG(HiResIndicator)*100, AVG(PerfectMatchIndicator)*100 from MovieHistory                     | % (0-100)|

#### C. Upload

**Sub-tab "qBittorrent":**

| ID  | Chart              | Type        | SQL Logic (REPORTS_DB)                                                                      | Y-Axis   |
| --- | ------------------ | ----------- | ------------------------------------------------------------------------------------------- | -------- |
| C1  | Upload Success Rate | Line (area)| Daily AVG(SuccessRate) from UploaderStats JOIN ReportSessions                                 | % (0-100)|
| C2  | Duplicate Rate      | Line (area)| Daily SUM(DuplicateCount) / NULLIF(SUM(TotalTorrents),0) from UploaderStats                  | % (0-100)|

**Sub-tab "PikPak":**

| ID  | Chart                | Type         | SQL Logic (REPORTS_DB)                                                                              | Y-Axis   |
| --- | -------------------- | ------------ | --------------------------------------------------------------------------------------------------- | -------- |
| C3  | PikPak Success Rate  | Line (area)  | Daily SUM(SuccessfulCount) / NULLIF(SUM(TotalTorrents),0) from PikpakStats JOIN ReportSessions      | % (0-100)|
| C4  | PikPak Failure Detail| Stacked Bar  | Daily SUM(FailedCount), SUM(DeleteFailedCount) from PikpakStats JOIN ReportSessions                 | Count    |

#### D. Operations (System > Operations)

| ID  | Chart                 | Type         | SQL Logic                                                                                  | Y-Axis |
| --- | --------------------- | ------------ | ------------------------------------------------------------------------------------------ | ------ |
| D1  | Email Notifications   | Stacked Bar  | Daily COUNT per Status (sent/failed/resent) from EmailNotificationHistory                   | Count  |
| D2  | Ops Incidents         | Bar          | Daily COUNT(*) from OpsIncidents GROUP BY DATE(created_at)                                  | Count  |

### API Changes

#### Extended `GET /api/stats/trend`

Add to `VALID_METRICS`:

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

All return the existing `TrendResponse` shape: `{ metric, period, data_points: {date, value}[] }`.

**Multi-series charts** (A1, C4, D1) fetch one metric per series and combine datasets in the frontend. This keeps the API surface simple and each query lightweight.

#### New `GET /api/stats/distribution`

```typescript
interface DistributionResponse {
  metric: string
  period: string
  buckets: Array<{ label: string; value: number }>
}
```

Supported metrics:
- `rating_distribution` — buckets: `["0-2", "2-4", "4-6", "6-8", "8-10"]`
- `resolution_distribution` — buckets: dynamic from data (e.g. `["SD", "720p", "1080p", "4K"]`)

Accepts `period` param to filter data by time range (consistent with trend API).

### Frontend Implementation

- **Chart library:** continue using `vue-chartjs` (Chart.js), already installed.
- **New Chart.js registration:** add `DoughnutController`, `ArcElement` for B4.
- **Sub-tab component:** reuse `NTabs type="segment"` for sub-tab navigation within each main tab.
- **Lazy loading:** each sub-tab fetches data on activation (same pattern as current tab switching).
- **Period selector:** shared across all tabs (existing behavior, unchanged).

### Mapping of ResolutionType Values

The `ResolutionType` column in `TorrentHistory` stores integer values. The display labels for B4 (Resolution Distribution) chart:

| Value | Label |
| ----- | ----- |
| 0     | SD    |
| 1     | 720p  |
| 2     | 1080p |
| 3     | 4K    |

If unknown values appear, display as "Other".

## Consequences

- **+15 new charts** across 4 categories, providing visibility into spider efficiency, content quality, upload performance, and operations health.
- **+1 new API endpoint** (`/api/stats/distribution`) for non-time-series data.
- **~20 new trend metrics** added to existing `/api/stats/trend`.
- StatsPage grows from ~760 lines to ~1500+ lines. If maintainability becomes a concern, consider extracting tab content into separate components in a follow-up.
- No database schema changes required — all charts use existing tables.
