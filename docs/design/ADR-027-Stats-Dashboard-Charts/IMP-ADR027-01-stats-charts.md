# Stats Dashboard Chart Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value                                                        |
| ----------- | ------------------------------------------------------------ |
| **Status**  | Draft                                                        |
| **Related** | [ADR-027](ADR-027-stats-dashboard-charts.md)                 |

**Goal:** Add ~15 new charts to the StatsPage covering spider efficiency, content quality, upload performance, and operations health.

**Architecture:** Extend the existing `/api/stats/trend` endpoint with ~20 new metric names; add a new `/api/stats/distribution` endpoint for non-time-series data (rating histogram, resolution pie chart). On the frontend, expand the StatsPage from 3 tabs to 5 tabs with sub-tabs via `NTabs type="segment"`. Each tab content is extracted into its own Vue component to keep file sizes manageable.

**Tech Stack:** Hono (TS backend on Cloudflare Workers), D1 SQL, Vue 3 + Naive UI + vue-chartjs (Chart.js), Vitest with `@cloudflare/vitest-pool-workers`.

**Important codebase conventions:**
- Backend: `server/routes/stats.ts` — Hono routes querying D1 databases (`REPORTS_DB`, `HISTORY_DB`, `OPERATIONS_DB`).
- Frontend API client: `src/api/stats.ts` — axios-based typed wrappers.
- Frontend page: `src/pages/stats/StatsPage.vue` — currently a single 767-line SFC.
- Tests: `server/__tests__/stats-routes.test.ts` — Vitest with D1 miniflare stubs.
- i18n: `src/i18n/locales/{en,zh-CN,ja}.json` under `stats.*` keys.
- Run server tests: `npm run test:server` from the web repo root.
- Three D1 databases: `HISTORY_DB`, `REPORTS_DB`, `OPERATIONS_DB` (bound via `wrangler.toml`).

---

## File Structure

### Files to Create

| File | Responsibility |
| --- | --- |
| `src/pages/stats/tabs/RunsOverviewTab.vue` | Existing 4 run charts (extracted from StatsPage) |
| `src/pages/stats/tabs/SpiderDetailTab.vue` | A1-A4: spider phase breakdown, efficiency, skip rate, failure rate |
| `src/pages/stats/tabs/ContentQualityTab.vue` | B1-B2: avg rating trend, rating distribution histogram |
| `src/pages/stats/tabs/ContentCoverageTab.vue` | B3-B5: subtitle coverage, resolution doughnut, HiRes/PerfectMatch |
| `src/pages/stats/tabs/UploadQbTab.vue` | C1-C2: upload success rate, duplicate rate |
| `src/pages/stats/tabs/UploadPikpakTab.vue` | C3-C4: pikpak success rate, pikpak failure analysis |
| `src/pages/stats/tabs/SystemInfraTab.vue` | Existing 2 system charts (extracted from StatsPage) |
| `src/pages/stats/tabs/SystemOpsTab.vue` | D1-D2: email notifications, ops incidents |
| `src/pages/stats/chartOptions.ts` | Shared Chart.js option presets (reusable across tabs) |

### Files to Modify

| File | Changes |
| --- | --- |
| `server/routes/stats.ts` | Add ~20 new metric cases to trend handler; add `/distribution` route |
| `src/api/stats.ts` | Add `DistributionResponse` type and `getStatsDistribution()` function |
| `src/pages/stats/StatsPage.vue` | Rewrite to orchestrate 5 main tabs with sub-tabs, delegate chart rendering to tab components |
| `src/i18n/locales/en.json` | Add ~25 new `stats.*` i18n keys |
| `src/i18n/locales/zh-CN.json` | Add matching Chinese translations |
| `src/i18n/locales/ja.json` | Add matching Japanese translations |
| `server/__tests__/stats-routes.test.ts` | Add tests for new trend metrics and distribution endpoint |

---

## Task 1: Backend — Add Spider Trend Metrics (A1-A4)

**Files:**
- Modify: `server/routes/stats.ts`
- Test: `server/__tests__/stats-routes.test.ts`

### Step 1: Write the failing tests

- [ ] Add a test block for spider metrics in `server/__tests__/stats-routes.test.ts`. Insert this after the existing `seedTables()` function body (before the closing `}`), to seed the `SpiderStats` table:

```typescript
  // SpiderStats (REPORTS_DB)
  await env.REPORTS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS SpiderStats (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId TEXT NOT NULL,
      Phase1Discovered INTEGER, Phase1Processed INTEGER, Phase1Skipped INTEGER,
      Phase1NoNew INTEGER, Phase1Failed INTEGER,
      Phase2Discovered INTEGER, Phase2Processed INTEGER, Phase2Skipped INTEGER,
      Phase2NoNew INTEGER, Phase2Failed INTEGER,
      TotalDiscovered INTEGER, TotalProcessed INTEGER, TotalSkipped INTEGER,
      TotalNoNew INTEGER, TotalFailed INTEGER,
      FailedMovies TEXT, DateTimeCreated TEXT
    )`,
  ).run();
  await env.REPORTS_DB.prepare(
    `INSERT INTO SpiderStats (SessionId, TotalDiscovered, TotalProcessed, TotalSkipped, TotalNoNew, TotalFailed, DateTimeCreated)
     VALUES ('sess-001', 100, 60, 20, 15, 5, datetime('now', '-1 day'))`,
  ).run();
```

- [ ] Add these test cases inside the existing `describe("Stats routes", ...)` block:

```typescript
  it("GET /api/stats/trend?metric=spider_processed returns daily totals", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=spider_processed&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.metric).toBe("spider_processed");
    expect(data.data_points.length).toBeGreaterThan(0);
    expect(data.data_points[0].value).toBe(60);
  });

  it("GET /api/stats/trend?metric=spider_efficiency returns ratio as 0-1", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=spider_efficiency&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 60 processed / 100 discovered = 0.6
    expect(data.data_points[0].value).toBeCloseTo(0.6, 1);
  });

  it("GET /api/stats/trend?metric=spider_skip_rate returns ratio", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=spider_skip_rate&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 20 skipped / 100 discovered = 0.2
    expect(data.data_points[0].value).toBeCloseTo(0.2, 1);
  });

  it("GET /api/stats/trend?metric=spider_failure_rate returns ratio", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=spider_failure_rate&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 5 failed / 100 discovered = 0.05
    expect(data.data_points[0].value).toBeCloseTo(0.05, 2);
  });
```

### Step 2: Run tests to verify they fail

- [ ] Run from the web repo root:

```bash
cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npm run test:server -- --reporter=verbose 2>&1 | tail -30
```

Expected: 4 new tests FAIL with `400` status (invalid metric).

### Step 3: Implement spider metrics in the backend

- [ ] In `server/routes/stats.ts`, expand the `VALID_METRICS` set. Replace the existing line:

```typescript
const VALID_METRICS = new Set([
  "success_rate",
  "duration",
  "movies",
  "torrents",
  "history_growth",
  "pikpak",
  "dedup",
  "proxy_bans",
]);
```

with:

```typescript
const VALID_METRICS = new Set([
  // Existing
  "success_rate",
  "duration",
  "movies",
  "torrents",
  "history_growth",
  "pikpak",
  "dedup",
  "proxy_bans",
  // Spider (A1-A4)
  "spider_processed",
  "spider_skipped",
  "spider_nonew",
  "spider_failed",
  "spider_efficiency",
  "spider_skip_rate",
  "spider_failure_rate",
]);
```

- [ ] In the same file's `switch (metric)` block, add these cases **before** the `default:` case:

```typescript
      // --- Spider raw counts (A1 stacked bar series) ---
      case "spider_processed":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date, SUM(ss.TotalProcessed) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
      case "spider_skipped":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date, SUM(ss.TotalSkipped) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
      case "spider_nonew":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date, SUM(ss.TotalNoNew) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
      case "spider_failed":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date, SUM(ss.TotalFailed) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Spider ratios (A2-A4 line charts) ---
      case "spider_efficiency":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date,
                      CAST(SUM(ss.TotalProcessed) AS REAL) / NULLIF(SUM(ss.TotalDiscovered), 0) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
      case "spider_skip_rate":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date,
                      CAST(SUM(ss.TotalSkipped) AS REAL) / NULLIF(SUM(ss.TotalDiscovered), 0) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
      case "spider_failure_rate":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ss.DateTimeCreated) AS date,
                      CAST(SUM(ss.TotalFailed) AS REAL) / NULLIF(SUM(ss.TotalDiscovered), 0) AS value
               FROM SpiderStats ss
               WHERE ss.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ss.DateTimeCreated)
               ORDER BY date`;
        break;
```

### Step 4: Run tests to verify they pass

- [ ] Run:

```bash
npm run test:server -- --reporter=verbose 2>&1 | tail -30
```

Expected: All 4 new tests PASS. All existing tests still PASS.

### Step 5: Commit

- [ ] Commit:

```bash
git add server/routes/stats.ts server/__tests__/stats-routes.test.ts
git commit -m "feat(stats): add spider trend metrics (A1-A4)"
```

---

## Task 2: Backend — Add Content, Upload, and System Trend Metrics

**Files:**
- Modify: `server/routes/stats.ts`
- Test: `server/__tests__/stats-routes.test.ts`

### Step 1: Write the failing tests

- [ ] Add seed data in `seedTables()`. Insert this after the SpiderStats seed from Task 1:

```typescript
  // UploaderStats (REPORTS_DB)
  await env.REPORTS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS UploaderStats (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId TEXT NOT NULL,
      TotalTorrents INTEGER, DuplicateCount INTEGER, Attempted INTEGER,
      SuccessfullyAdded INTEGER, FailedCount INTEGER,
      HackedSub INTEGER, HackedNosub INTEGER,
      SubtitleCount INTEGER, NoSubtitleCount INTEGER,
      SuccessRate REAL, DateTimeCreated TEXT
    )`,
  ).run();
  await env.REPORTS_DB.prepare(
    `INSERT INTO UploaderStats (SessionId, TotalTorrents, DuplicateCount, SubtitleCount, NoSubtitleCount, SuccessRate, DateTimeCreated)
     VALUES ('sess-001', 50, 10, 30, 20, 0.85, datetime('now', '-1 day'))`,
  ).run();

  // PikpakStats (REPORTS_DB)
  await env.REPORTS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS PikpakStats (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId TEXT NOT NULL,
      ThresholdDays INTEGER, TotalTorrents INTEGER, FilteredOld INTEGER,
      SuccessfulCount INTEGER, FailedCount INTEGER,
      UploadedCount INTEGER, DeleteFailedCount INTEGER,
      DateTimeCreated TEXT
    )`,
  ).run();
  await env.REPORTS_DB.prepare(
    `INSERT INTO PikpakStats (SessionId, TotalTorrents, SuccessfulCount, FailedCount, DeleteFailedCount, DateTimeCreated)
     VALUES ('sess-001', 40, 35, 3, 2, datetime('now', '-1 day'))`,
  ).run();

  // ReportMovies — add Rate column to existing table
  await env.REPORTS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS ReportMovies (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId TEXT NOT NULL, Href TEXT, VideoCode TEXT,
      Page INTEGER, Actor TEXT, Rate REAL, CommentNumber INTEGER
    )`,
  ).run();
  await env.REPORTS_DB.prepare(
    `INSERT INTO ReportMovies (SessionId, VideoCode, Rate, CommentNumber)
     VALUES ('sess-001', 'TEST-001', 7.5, 42)`,
  ).run();
  await env.REPORTS_DB.prepare(
    `INSERT INTO ReportMovies (SessionId, VideoCode, Rate, CommentNumber)
     VALUES ('sess-001', 'TEST-002', 3.2, 10)`,
  ).run();

  // MovieHistory — add HiRes and PerfectMatch columns
  await env.HISTORY_DB.prepare(
    "ALTER TABLE MovieHistory ADD COLUMN PerfectMatchIndicator INTEGER DEFAULT 0",
  ).run();
  await env.HISTORY_DB.prepare(
    "ALTER TABLE MovieHistory ADD COLUMN HiResIndicator INTEGER DEFAULT 0",
  ).run();
  await env.HISTORY_DB.prepare(
    "UPDATE MovieHistory SET PerfectMatchIndicator = 1, HiResIndicator = 1 WHERE Id = 1",
  ).run();

  // TorrentHistory — add resolution column
  await env.HISTORY_DB.prepare(
    "ALTER TABLE TorrentHistory ADD COLUMN SubtitleIndicator INTEGER DEFAULT 0",
  ).run();
  await env.HISTORY_DB.prepare(
    "ALTER TABLE TorrentHistory ADD COLUMN ResolutionType INTEGER DEFAULT 0",
  ).run();
  await env.HISTORY_DB.prepare(
    "UPDATE TorrentHistory SET ResolutionType = 2 WHERE Id = 1",
  ).run();
  await env.HISTORY_DB.prepare(
    "UPDATE TorrentHistory SET ResolutionType = 1 WHERE Id = 2",
  ).run();

  // EmailNotificationHistory (OPERATIONS_DB)
  await env.OPERATIONS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS EmailNotificationHistory (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId TEXT, Recipient TEXT NOT NULL, Subject TEXT NOT NULL,
      Status TEXT NOT NULL DEFAULT 'sent', ErrorMessage TEXT,
      AttachmentNames TEXT, SentAt TEXT NOT NULL, ResentAt TEXT, CreatedBy TEXT
    )`,
  ).run();
  await env.OPERATIONS_DB.prepare(
    `INSERT INTO EmailNotificationHistory (SessionId, Recipient, Subject, Status, SentAt)
     VALUES ('sess-001', 'a@b.com', 'Report', 'sent', datetime('now', '-1 day'))`,
  ).run();
  await env.OPERATIONS_DB.prepare(
    `INSERT INTO EmailNotificationHistory (SessionId, Recipient, Subject, Status, SentAt)
     VALUES ('sess-001', 'a@b.com', 'Report', 'failed', datetime('now', '-1 day'))`,
  ).run();

  // OpsIncidents (REPORTS_DB)
  await env.REPORTS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS OpsIncidents (
      incident_id TEXT PRIMARY KEY, trigger_source TEXT NOT NULL,
      run_id TEXT, run_attempt INTEGER, session_id TEXT,
      incident_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
      persistence_status TEXT NOT NULL DEFAULT 'd1_written',
      model_version TEXT NOT NULL, detector_version TEXT NOT NULL,
      bundle_schema_version TEXT NOT NULL,
      confidence TEXT NOT NULL DEFAULT 'low',
      confirmed_findings_json TEXT, likely_causes_json TEXT,
      unknowns_json TEXT, recommended_next_actions_json TEXT,
      unsafe_actions_json TEXT, evidence_refs_json TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT
    )`,
  ).run();
  await env.REPORTS_DB.prepare(
    `INSERT INTO OpsIncidents (incident_id, trigger_source, incident_type, status, model_version, detector_version, bundle_schema_version, created_at, updated_at)
     VALUES ('inc-001', 'ci', 'spider_failure', 'open', 'v1', 'v1', 'v1', datetime('now', '-1 day'), datetime('now', '-1 day'))`,
  ).run();
```

- [ ] Add these test cases inside the existing `describe`:

```typescript
  // --- Content metrics ---
  it("GET /api/stats/trend?metric=avg_rating returns average rating per day", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=avg_rating&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // avg of 7.5 and 3.2 = 5.35
    expect(data.data_points[0].value).toBeCloseTo(5.35, 1);
  });

  it("GET /api/stats/trend?metric=subtitle_coverage returns ratio", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=subtitle_coverage&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 30 / (30 + 20) = 0.6
    expect(data.data_points[0].value).toBeCloseTo(0.6, 1);
  });

  it("GET /api/stats/trend?metric=hires_ratio returns ratio", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=hires_ratio&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 1 HiRes out of 1 movie = 1.0
    expect(data.data_points[0].value).toBeCloseTo(1.0, 1);
  });

  // --- Upload metrics ---
  it("GET /api/stats/trend?metric=upload_success_rate returns rate", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=upload_success_rate&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    expect(data.data_points[0].value).toBeCloseTo(0.85, 2);
  });

  it("GET /api/stats/trend?metric=duplicate_rate returns ratio", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=duplicate_rate&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 10 / 50 = 0.2
    expect(data.data_points[0].value).toBeCloseTo(0.2, 1);
  });

  it("GET /api/stats/trend?metric=pikpak_success_rate returns ratio", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=pikpak_success_rate&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    // 35 / 40 = 0.875
    expect(data.data_points[0].value).toBeCloseTo(0.875, 2);
  });

  it("GET /api/stats/trend?metric=pikpak_failed returns count", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=pikpak_failed&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    expect(data.data_points[0].value).toBe(3);
  });

  // --- System/Ops metrics ---
  it("GET /api/stats/trend?metric=email_sent returns count", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=email_sent&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    expect(data.data_points[0].value).toBe(1);
  });

  it("GET /api/stats/trend?metric=ops_incidents returns count", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=ops_incidents&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.data_points.length).toBeGreaterThan(0);
    expect(data.data_points[0].value).toBe(1);
  });
```

### Step 2: Run tests to verify they fail

- [ ] Run:

```bash
npm run test:server -- --reporter=verbose 2>&1 | tail -40
```

Expected: 9 new tests FAIL with `400` status.

### Step 3: Implement remaining trend metrics

- [ ] In `server/routes/stats.ts`, expand `VALID_METRICS` to its final form. Replace the entire set with:

```typescript
const VALID_METRICS = new Set([
  // Existing
  "success_rate",
  "duration",
  "movies",
  "torrents",
  "history_growth",
  "pikpak",
  "dedup",
  "proxy_bans",
  // Spider (A1-A4)
  "spider_processed",
  "spider_skipped",
  "spider_nonew",
  "spider_failed",
  "spider_efficiency",
  "spider_skip_rate",
  "spider_failure_rate",
  // Content (B1, B3, B5)
  "avg_rating",
  "subtitle_coverage",
  "hires_ratio",
  "perfectmatch_ratio",
  // Upload (C1-C4)
  "upload_success_rate",
  "duplicate_rate",
  "pikpak_success_rate",
  "pikpak_failed",
  "pikpak_delete_failed",
  // System/Ops (D1-D2)
  "email_sent",
  "email_failed",
  "email_resent",
  "ops_incidents",
]);
```

- [ ] Add the remaining `switch` cases before the `default:` case. These go after the spider cases added in Task 1:

```typescript
      // --- Content: avg rating (B1) ---
      case "avg_rating":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(rs.DateTimeCreated) AS date, AVG(rm.Rate) AS value
               FROM ReportSessions rs
               JOIN ReportMovies rm ON rm.SessionId = rs.Id
               WHERE rm.Rate > 0
                 AND rs.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(rs.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Content: subtitle coverage (B3) ---
      case "subtitle_coverage":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(us.DateTimeCreated) AS date,
                      CAST(SUM(us.SubtitleCount) AS REAL) / NULLIF(SUM(us.SubtitleCount + us.NoSubtitleCount), 0) AS value
               FROM UploaderStats us
               WHERE us.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(us.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Content: HiRes ratio (B5) ---
      case "hires_ratio":
        db = c.env.HISTORY_DB;
        sql = `SELECT DATE(DateTimeCreated) AS date,
                      AVG(CAST(HiResIndicator AS REAL)) AS value
               FROM MovieHistory
               WHERE DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Content: PerfectMatch ratio (B5) ---
      case "perfectmatch_ratio":
        db = c.env.HISTORY_DB;
        sql = `SELECT DATE(DateTimeCreated) AS date,
                      AVG(CAST(PerfectMatchIndicator AS REAL)) AS value
               FROM MovieHistory
               WHERE DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Upload: QB success rate (C1) ---
      case "upload_success_rate":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(us.DateTimeCreated) AS date, AVG(us.SuccessRate) AS value
               FROM UploaderStats us
               WHERE us.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(us.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Upload: duplicate rate (C2) ---
      case "duplicate_rate":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(us.DateTimeCreated) AS date,
                      CAST(SUM(us.DuplicateCount) AS REAL) / NULLIF(SUM(us.TotalTorrents), 0) AS value
               FROM UploaderStats us
               WHERE us.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(us.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Upload: PikPak success rate (C3) ---
      case "pikpak_success_rate":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ps.DateTimeCreated) AS date,
                      CAST(SUM(ps.SuccessfulCount) AS REAL) / NULLIF(SUM(ps.TotalTorrents), 0) AS value
               FROM PikpakStats ps
               WHERE ps.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ps.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Upload: PikPak failed count (C4 series 1) ---
      case "pikpak_failed":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ps.DateTimeCreated) AS date, SUM(ps.FailedCount) AS value
               FROM PikpakStats ps
               WHERE ps.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ps.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- Upload: PikPak delete-failed count (C4 series 2) ---
      case "pikpak_delete_failed":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(ps.DateTimeCreated) AS date, SUM(ps.DeleteFailedCount) AS value
               FROM PikpakStats ps
               WHERE ps.DateTimeCreated >= datetime('now', '-${days} days')
               GROUP BY DATE(ps.DateTimeCreated)
               ORDER BY date`;
        break;
      // --- System: email sent (D1 series 1) ---
      case "email_sent":
        db = c.env.OPERATIONS_DB;
        sql = `SELECT DATE(SentAt) AS date, COUNT(*) AS value
               FROM EmailNotificationHistory
               WHERE Status = 'sent'
                 AND SentAt >= datetime('now', '-${days} days')
               GROUP BY DATE(SentAt)
               ORDER BY date`;
        break;
      // --- System: email failed (D1 series 2) ---
      case "email_failed":
        db = c.env.OPERATIONS_DB;
        sql = `SELECT DATE(SentAt) AS date, COUNT(*) AS value
               FROM EmailNotificationHistory
               WHERE Status = 'failed'
                 AND SentAt >= datetime('now', '-${days} days')
               GROUP BY DATE(SentAt)
               ORDER BY date`;
        break;
      // --- System: email resent (D1 series 3) ---
      case "email_resent":
        db = c.env.OPERATIONS_DB;
        sql = `SELECT DATE(COALESCE(ResentAt, SentAt)) AS date, COUNT(*) AS value
               FROM EmailNotificationHistory
               WHERE Status = 'resent'
                 AND COALESCE(ResentAt, SentAt) >= datetime('now', '-${days} days')
               GROUP BY DATE(COALESCE(ResentAt, SentAt))
               ORDER BY date`;
        break;
      // --- System: ops incidents (D2) ---
      case "ops_incidents":
        db = c.env.REPORTS_DB;
        sql = `SELECT DATE(created_at) AS date, COUNT(*) AS value
               FROM OpsIncidents
               WHERE created_at >= datetime('now', '-${days} days')
               GROUP BY DATE(created_at)
               ORDER BY date`;
        break;
```

### Step 4: Run tests to verify they pass

- [ ] Run:

```bash
npm run test:server -- --reporter=verbose 2>&1 | tail -40
```

Expected: All 9 new tests PASS. All previous tests still PASS.

### Step 5: Commit

- [ ] Commit:

```bash
git add server/routes/stats.ts server/__tests__/stats-routes.test.ts
git commit -m "feat(stats): add content, upload, and system trend metrics"
```

---

## Task 3: Backend — Add Distribution Endpoint (B2, B4)

**Files:**
- Modify: `server/routes/stats.ts`
- Test: `server/__tests__/stats-routes.test.ts`

### Step 1: Write the failing tests

- [ ] Add these test cases inside the existing `describe`:

```typescript
  // --- Distribution endpoint ---
  it("GET /api/stats/distribution?metric=rating_distribution returns buckets", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/distribution?metric=rating_distribution&period=90d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.metric).toBe("rating_distribution");
    expect(Array.isArray(data.buckets)).toBe(true);
    expect(data.buckets.length).toBe(5);
    // Expect labels in order
    expect(data.buckets.map((b: any) => b.label)).toEqual(["0-2", "2-4", "4-6", "6-8", "8-10"]);
    // TEST-002 rate 3.2 goes to "2-4" bucket, TEST-001 rate 7.5 goes to "6-8" bucket
    const bucket24 = data.buckets.find((b: any) => b.label === "2-4");
    const bucket68 = data.buckets.find((b: any) => b.label === "6-8");
    expect(bucket24.value).toBe(1);
    expect(bucket68.value).toBe(1);
  });

  it("GET /api/stats/distribution?metric=resolution_distribution returns buckets", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/distribution?metric=resolution_distribution&period=90d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.metric).toBe("resolution_distribution");
    expect(Array.isArray(data.buckets)).toBe(true);
    // We seeded ResolutionType 2 (1080p) and 1 (720p)
    const b1080 = data.buckets.find((b: any) => b.label === "1080p");
    const b720 = data.buckets.find((b: any) => b.label === "720p");
    expect(b1080.value).toBe(1);
    expect(b720.value).toBe(1);
  });

  it("GET /api/stats/distribution with invalid metric returns 400", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/distribution?metric=bad_metric",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(400);
  });
```

### Step 2: Run tests to verify they fail

- [ ] Run:

```bash
npm run test:server -- --reporter=verbose 2>&1 | tail -20
```

Expected: 3 new tests FAIL (404 — route does not exist yet).

### Step 3: Implement the distribution endpoint

- [ ] Add the following at the **end** of `server/routes/stats.ts` (after the existing `/trend` route):

```typescript
// --- GET /distribution ---

const VALID_DISTRIBUTION_METRICS = new Set([
  "rating_distribution",
  "resolution_distribution",
]);

const RESOLUTION_LABELS: Record<number, string> = {
  0: "SD",
  1: "720p",
  2: "1080p",
  3: "4K",
};

statsRoutes.get("/distribution", async (c) => {
  const metric = c.req.query("metric") ?? "";
  const period = c.req.query("period") ?? "30d";

  if (!VALID_DISTRIBUTION_METRICS.has(metric)) {
    throw new HTTPException(400, {
      message: JSON.stringify({
        error: {
          code: "stats.invalid_metric",
          message: `Invalid distribution metric '${metric}'. Supported: ${[...VALID_DISTRIBUTION_METRICS].join(", ")}`,
        },
      }),
    });
  }

  if (!VALID_PERIODS.has(period)) {
    throw new HTTPException(400, {
      message: JSON.stringify({
        error: {
          code: "stats.invalid_period",
          message: `Invalid period '${period}'. Supported: ${[...VALID_PERIODS].join(", ")}`,
        },
      }),
    });
  }

  const days = periodToDays(period);
  let buckets: Array<{ label: string; value: number }> = [];

  try {
    if (metric === "rating_distribution") {
      const db = c.env.REPORTS_DB;
      const rows = await db
        .prepare(
          `SELECT
             CASE
               WHEN Rate >= 0 AND Rate < 2 THEN '0-2'
               WHEN Rate >= 2 AND Rate < 4 THEN '2-4'
               WHEN Rate >= 4 AND Rate < 6 THEN '4-6'
               WHEN Rate >= 6 AND Rate < 8 THEN '6-8'
               ELSE '8-10'
             END AS label,
             COUNT(*) AS value
           FROM ReportMovies rm
           JOIN ReportSessions rs ON rs.Id = rm.SessionId
           WHERE rm.Rate > 0
             AND rs.DateTimeCreated >= datetime('now', '-${days} days')
           GROUP BY label`,
        )
        .all<{ label: string; value: number }>();

      // Ensure all 5 buckets exist in order
      const bucketOrder = ["0-2", "2-4", "4-6", "6-8", "8-10"];
      const map = new Map(rows.results.map((r) => [r.label, r.value]));
      buckets = bucketOrder.map((l) => ({ label: l, value: map.get(l) ?? 0 }));
    } else if (metric === "resolution_distribution") {
      const db = c.env.HISTORY_DB;
      const rows = await db
        .prepare(
          `SELECT ResolutionType AS res_type, COUNT(*) AS value
           FROM TorrentHistory
           WHERE DateTimeCreated >= datetime('now', '-${days} days')
             AND ResolutionType IS NOT NULL
           GROUP BY ResolutionType
           ORDER BY ResolutionType`,
        )
        .all<{ res_type: number; value: number }>();

      buckets = rows.results.map((r) => ({
        label: RESOLUTION_LABELS[r.res_type] ?? "Other",
        value: r.value,
      }));
    }
  } catch {
    // Table may not exist — return empty
  }

  return c.json({ metric, period, buckets });
});
```

### Step 4: Run tests to verify they pass

- [ ] Run:

```bash
npm run test:server -- --reporter=verbose 2>&1 | tail -20
```

Expected: All 3 new tests PASS. All previous tests still PASS.

### Step 5: Commit

- [ ] Commit:

```bash
git add server/routes/stats.ts server/__tests__/stats-routes.test.ts
git commit -m "feat(stats): add /distribution endpoint for rating and resolution"
```

---

## Task 4: Frontend API Client — Add Distribution Support

**Files:**
- Modify: `src/api/stats.ts`

### Step 1: Add the distribution types and function

- [ ] In `src/api/stats.ts`, add after the existing `TrendResponse` interface:

```typescript
export interface DistributionBucket {
  label: string
  value: number
}

export interface DistributionResponse {
  metric: string
  period: string
  buckets: DistributionBucket[]
}
```

- [ ] Add after the existing `getStatsTrend` function:

```typescript
export async function getStatsDistribution(
  metric: string,
  period: string = '30d',
): Promise<DistributionResponse> {
  const { data } = await http.get<DistributionResponse>('/api/stats/distribution', {
    params: { metric, period },
  })
  return data
}
```

### Step 2: Commit

- [ ] Commit:

```bash
git add src/api/stats.ts
git commit -m "feat(stats): add distribution API client types and function"
```

---

## Task 5: Frontend — Extract Shared Chart Options

**Files:**
- Create: `src/pages/stats/chartOptions.ts`

### Step 1: Create the shared chart options file

- [ ] Create `src/pages/stats/chartOptions.ts` with all reusable chart option presets:

```typescript
import type { ChartOptions, TooltipItem } from 'chart.js'

// --- Formatters ---

const MB = 1024 * 1024
const GB = 1024 * 1024 * 1024

export function formatBytesScaled(bytes: number): string {
  if (!bytes || bytes < 0) return '0 MB'
  if (bytes >= GB) return `${(bytes / GB).toFixed(2)} GB`
  return `${(bytes / MB).toFixed(2)} MB`
}

// --- Shared base configs ---

const BASE_LINE: ChartOptions<'line'> = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: { x: { grid: { display: false } } },
}

const BASE_BAR: ChartOptions<'bar'> = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: { x: { grid: { display: false } } },
}

// --- Exported presets ---

/** Generic line chart — no special Y axis formatting. */
export const lineChartOptions: ChartOptions<'line'> = { ...BASE_LINE }

/** Generic bar chart — no special Y axis formatting. */
export const barChartOptions: ChartOptions<'bar'> = { ...BASE_BAR }

/** Percentage Y axis (0-100), tooltip shows "X.X%". */
export const percentLineOptions: ChartOptions<'line'> = {
  ...BASE_LINE,
  scales: {
    x: { grid: { display: false } },
    y: { min: 0, max: 100, ticks: { callback: (v) => `${v}%` } },
  },
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx: TooltipItem<'line'>) =>
          ctx.parsed.y == null ? '' : `${ctx.parsed.y.toFixed(1)}%`,
      },
    },
  },
}

/** Duration Y axis (seconds), tooltip shows "Xs". */
export const durationLineOptions: ChartOptions<'line'> = {
  ...BASE_LINE,
  scales: {
    x: { grid: { display: false } },
    y: { beginAtZero: true, ticks: { callback: (v) => `${v}s` } },
  },
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx: TooltipItem<'line'>) =>
          ctx.parsed.y == null ? '' : `${Math.round(ctx.parsed.y)}s`,
      },
    },
  },
}

/** Bytes Y axis, tooltip shows scaled MB/GB. */
export const bytesBarOptions: ChartOptions<'bar'> = {
  ...BASE_BAR,
  scales: {
    x: { grid: { display: false } },
    y: { beginAtZero: true, ticks: { callback: (v) => formatBytesScaled(Number(v)) } },
  },
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx: TooltipItem<'bar'>) =>
          ctx.parsed.y == null ? '' : formatBytesScaled(ctx.parsed.y),
      },
    },
  },
}

/** Stacked bar chart — shows legend, stacks bars. */
export const stackedBarOptions: ChartOptions<'bar'> = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: true, position: 'bottom' } },
  scales: {
    x: { stacked: true, grid: { display: false } },
    y: { stacked: true, beginAtZero: true },
  },
}

/** Rating Y axis (0-10), no special tooltip. */
export const ratingLineOptions: ChartOptions<'line'> = {
  ...BASE_LINE,
  scales: {
    x: { grid: { display: false } },
    y: { min: 0, max: 10 },
  },
}

/** Dual-line chart with legend visible. */
export const dualLineOptions: ChartOptions<'line'> = {
  ...BASE_LINE,
  plugins: { legend: { display: true, position: 'bottom' } },
  scales: {
    x: { grid: { display: false } },
    y: { min: 0, max: 100, ticks: { callback: (v) => `${v}%` } },
  },
}

/** Doughnut chart (for distribution). */
export const doughnutOptions: ChartOptions<'doughnut'> = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: true, position: 'right' },
  },
}
```

### Step 2: Commit

- [ ] Commit:

```bash
git add src/pages/stats/chartOptions.ts
git commit -m "refactor(stats): extract shared chart option presets"
```

---

## Task 6: Frontend — Add i18n Keys

**Files:**
- Modify: `src/i18n/locales/en.json`
- Modify: `src/i18n/locales/zh-CN.json`
- Modify: `src/i18n/locales/ja.json`

### Step 1: Add English i18n keys

- [ ] In `src/i18n/locales/en.json`, replace the existing `"stats"` object with:

```json
  "stats": {
    "subtitle": "Aggregated metrics and trends across all ingestion runs.",
    "totalRuns": "Total Runs",
    "successRate": "Success Rate",
    "avgDuration": "Avg Duration",
    "totalMovies": "Total Movies",
    "totalTorrents": "Total Torrents",
    "dailyMovies": "Daily Movies",
    "dailyTorrents": "Daily Torrents",
    "pikpakVolume": "PikPak Transfers",
    "dedupFreed": "Dedup Freed",
    "proxyBans": "Proxy Bans (7d)",
    "period": { "7d": "7 days", "30d": "30 days", "90d": "90 days" },
    "tabs": {
      "runs": "Run Metrics",
      "content": "Content",
      "upload": "Upload",
      "growth": "Growth",
      "system": "System"
    },
    "subtabs": {
      "overview": "Overview",
      "spiderDetail": "Spider Detail",
      "quality": "Quality",
      "coverage": "Coverage",
      "qbittorrent": "qBittorrent",
      "pikpak": "PikPak",
      "infrastructure": "Infrastructure",
      "operations": "Operations"
    },
    "noData": "No data available yet.",
    "seconds": "{n}s",
    "na": "N/A",
    "spiderProcessed": "Processed",
    "spiderSkipped": "Skipped",
    "spiderNoNew": "No New",
    "spiderFailed": "Failed",
    "phaseBreakdown": "Run Processing Breakdown",
    "discoveryEfficiency": "Discovery Efficiency",
    "skipRate": "Skip Rate",
    "failureRate": "Failure Rate",
    "avgRating": "Average Rating",
    "ratingDistribution": "Rating Distribution",
    "subtitleCoverage": "Subtitle Coverage",
    "resolutionDistribution": "Resolution Distribution",
    "hiresRatio": "HiRes Ratio",
    "perfectMatchRatio": "PerfectMatch Ratio",
    "uploadSuccessRate": "Upload Success Rate",
    "duplicateRate": "Duplicate Rate",
    "pikpakSuccessRate": "PikPak Success Rate",
    "pikpakFailureDetail": "PikPak Failure Detail",
    "pikpakFailed": "Upload Failed",
    "pikpakDeleteFailed": "Delete Failed",
    "emailNotifications": "Email Notifications",
    "emailSent": "Sent",
    "emailFailed": "Failed",
    "emailResent": "Resent",
    "opsIncidents": "Ops Incidents"
  }
```

### Step 2: Add Chinese i18n keys

- [ ] In `src/i18n/locales/zh-CN.json`, replace the existing `"stats"` object with the Chinese translations. Mirror the same key structure; translate display strings to Chinese while preserving code-like values (metric names, units) verbatim. Example subset — apply the same pattern for all keys:

```json
  "stats": {
    "subtitle": "所有抓取运行的汇总指标和趋势。",
    "totalRuns": "总运行次数",
    "successRate": "成功率",
    "avgDuration": "平均耗时",
    "totalMovies": "总电影数",
    "totalTorrents": "总种子数",
    "dailyMovies": "每日电影",
    "dailyTorrents": "每日种子",
    "pikpakVolume": "PikPak 传输",
    "dedupFreed": "去重释放",
    "proxyBans": "代理封禁 (7天)",
    "period": { "7d": "7 天", "30d": "30 天", "90d": "90 天" },
    "tabs": {
      "runs": "运行指标",
      "content": "内容",
      "upload": "上传",
      "growth": "增长",
      "system": "系统"
    },
    "subtabs": {
      "overview": "概览",
      "spiderDetail": "爬虫详情",
      "quality": "质量",
      "coverage": "覆盖",
      "qbittorrent": "qBittorrent",
      "pikpak": "PikPak",
      "infrastructure": "基础设施",
      "operations": "运维"
    },
    "noData": "暂无数据。",
    "seconds": "{n}秒",
    "na": "N/A",
    "spiderProcessed": "已处理",
    "spiderSkipped": "已跳过",
    "spiderNoNew": "无新增",
    "spiderFailed": "失败",
    "phaseBreakdown": "运行处理分解",
    "discoveryEfficiency": "发现效率",
    "skipRate": "跳过率",
    "failureRate": "失败率",
    "avgRating": "平均评分",
    "ratingDistribution": "评分分布",
    "subtitleCoverage": "字幕覆盖率",
    "resolutionDistribution": "分辨率分布",
    "hiresRatio": "高清比例",
    "perfectMatchRatio": "完美匹配比例",
    "uploadSuccessRate": "上传成功率",
    "duplicateRate": "重复率",
    "pikpakSuccessRate": "PikPak 成功率",
    "pikpakFailureDetail": "PikPak 失败详情",
    "pikpakFailed": "上传失败",
    "pikpakDeleteFailed": "删除失败",
    "emailNotifications": "邮件通知",
    "emailSent": "已发送",
    "emailFailed": "发送失败",
    "emailResent": "已重发",
    "opsIncidents": "运维事件"
  }
```

### Step 3: Add Japanese i18n keys

- [ ] In `src/i18n/locales/ja.json`, replace the existing `"stats"` object with the Japanese translations using the same key structure:

```json
  "stats": {
    "subtitle": "すべての取り込み実行の集計メトリクスとトレンド。",
    "totalRuns": "総実行回数",
    "successRate": "成功率",
    "avgDuration": "平均所要時間",
    "totalMovies": "総映画数",
    "totalTorrents": "総トレント数",
    "dailyMovies": "日次映画",
    "dailyTorrents": "日次トレント",
    "pikpakVolume": "PikPak 転送",
    "dedupFreed": "重複排除解放",
    "proxyBans": "プロキシBAN (7日)",
    "period": { "7d": "7日間", "30d": "30日間", "90d": "90日間" },
    "tabs": {
      "runs": "実行メトリクス",
      "content": "コンテンツ",
      "upload": "アップロード",
      "growth": "成長",
      "system": "システム"
    },
    "subtabs": {
      "overview": "概要",
      "spiderDetail": "スパイダー詳細",
      "quality": "品質",
      "coverage": "カバレッジ",
      "qbittorrent": "qBittorrent",
      "pikpak": "PikPak",
      "infrastructure": "インフラ",
      "operations": "オペレーション"
    },
    "noData": "データがありません。",
    "seconds": "{n}秒",
    "na": "N/A",
    "spiderProcessed": "処理済み",
    "spiderSkipped": "スキップ",
    "spiderNoNew": "新規なし",
    "spiderFailed": "失敗",
    "phaseBreakdown": "実行処理内訳",
    "discoveryEfficiency": "発見効率",
    "skipRate": "スキップ率",
    "failureRate": "失敗率",
    "avgRating": "平均評価",
    "ratingDistribution": "評価分布",
    "subtitleCoverage": "字幕カバー率",
    "resolutionDistribution": "解像度分布",
    "hiresRatio": "高画質比率",
    "perfectMatchRatio": "完全一致比率",
    "uploadSuccessRate": "アップロード成功率",
    "duplicateRate": "重複率",
    "pikpakSuccessRate": "PikPak 成功率",
    "pikpakFailureDetail": "PikPak 失敗詳細",
    "pikpakFailed": "アップロード失敗",
    "pikpakDeleteFailed": "削除失敗",
    "emailNotifications": "メール通知",
    "emailSent": "送信済み",
    "emailFailed": "送信失敗",
    "emailResent": "再送信済み",
    "opsIncidents": "運用インシデント"
  }
```

### Step 4: Commit

- [ ] Commit:

```bash
git add src/i18n/locales/en.json src/i18n/locales/zh-CN.json src/i18n/locales/ja.json
git commit -m "feat(i18n): add stats dashboard chart expansion keys"
```

---

## Task 7: Frontend — Extract Existing Tabs into Components

This task extracts the existing charts from the monolithic `StatsPage.vue` into standalone tab components, without changing any behaviour.

**Files:**
- Create: `src/pages/stats/tabs/RunsOverviewTab.vue`
- Create: `src/pages/stats/tabs/SystemInfraTab.vue`

### Step 1: Create RunsOverviewTab.vue

- [ ] Create `src/pages/stats/tabs/RunsOverviewTab.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NGrid, NGi } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line, Bar } from 'vue-chartjs'
import type { TrendResponse } from '@/api/stats'
import { percentLineOptions, durationLineOptions, barChartOptions } from '../chartOptions'

const props = defineProps<{
  successRateTrend: TrendResponse | null
  durationTrend: TrendResponse | null
  moviesTrend: TrendResponse | null
  torrentsTrend: TrendResponse | null
}>()

const { t } = useI18n()

const successRateChartData = computed(() => ({
  labels: props.successRateTrend?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.successRate'),
      data: props.successRateTrend?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
      borderColor: '#18a058',
      backgroundColor: 'rgba(24,160,88,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const durationChartData = computed(() => ({
  labels: props.durationTrend?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.avgDuration'),
      data: props.durationTrend?.data_points.map((d) => d.value) ?? [],
      borderColor: '#f0a020',
      backgroundColor: 'rgba(240,160,32,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const moviesChartData = computed(() => ({
  labels: props.moviesTrend?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.dailyMovies'),
      data: props.moviesTrend?.data_points.map((d) => d.value) ?? [],
      backgroundColor: 'rgba(99,149,255,0.7)',
    },
  ],
}))

const torrentsChartData = computed(() => ({
  labels: props.torrentsTrend?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.dailyTorrents'),
      data: props.torrentsTrend?.data_points.map((d) => d.value) ?? [],
      backgroundColor: 'rgba(64,158,255,0.7)',
    },
  ],
}))
</script>

<template>
  <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
    <NGi span="2 m:1">
      <NCard :title="t('stats.successRate')" size="small">
        <div class="chart-wrap">
          <Line v-if="(successRateTrend?.data_points.length ?? 0) > 0" :data="successRateChartData" :options="percentLineOptions" />
          <p v-else class="no-data">{{ t('stats.noData') }}</p>
        </div>
      </NCard>
    </NGi>
    <NGi span="2 m:1">
      <NCard :title="t('stats.avgDuration')" size="small">
        <div class="chart-wrap">
          <Line v-if="(durationTrend?.data_points.length ?? 0) > 0" :data="durationChartData" :options="durationLineOptions" />
          <p v-else class="no-data">{{ t('stats.noData') }}</p>
        </div>
      </NCard>
    </NGi>
    <NGi span="2 m:1">
      <NCard :title="t('stats.dailyMovies')" size="small">
        <div class="chart-wrap">
          <Bar v-if="(moviesTrend?.data_points.length ?? 0) > 0" :data="moviesChartData" :options="barChartOptions" />
          <p v-else class="no-data">{{ t('stats.noData') }}</p>
        </div>
      </NCard>
    </NGi>
    <NGi span="2 m:1">
      <NCard :title="t('stats.dailyTorrents')" size="small">
        <div class="chart-wrap">
          <Bar v-if="(torrentsTrend?.data_points.length ?? 0) > 0" :data="torrentsChartData" :options="barChartOptions" />
          <p v-else class="no-data">{{ t('stats.noData') }}</p>
        </div>
      </NCard>
    </NGi>
  </NGrid>
</template>
```

### Step 2: Create SystemInfraTab.vue

- [ ] Create `src/pages/stats/tabs/SystemInfraTab.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NGrid, NGi } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line, Bar } from 'vue-chartjs'
import type { TrendResponse } from '@/api/stats'
import { lineChartOptions, bytesBarOptions } from '../chartOptions'

const props = defineProps<{
  proxyBansTrend: TrendResponse | null
  dedupTrend: TrendResponse | null
}>()

const { t } = useI18n()

const proxyBansChartData = computed(() => ({
  labels: props.proxyBansTrend?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.proxyBans'),
      data: props.proxyBansTrend?.data_points.map((d) => d.value) ?? [],
      borderColor: '#d03050',
      backgroundColor: 'rgba(208,48,80,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const dedupChartData = computed(() => ({
  labels: props.dedupTrend?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.dedupFreed'),
      data: props.dedupTrend?.data_points.map((d) => d.value) ?? [],
      backgroundColor: 'rgba(114,46,209,0.7)',
    },
  ],
}))
</script>

<template>
  <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
    <NGi span="2 m:1">
      <NCard :title="t('stats.proxyBans')" size="small">
        <div class="chart-wrap">
          <Line v-if="(proxyBansTrend?.data_points.length ?? 0) > 0" :data="proxyBansChartData" :options="lineChartOptions" />
          <p v-else class="no-data">{{ t('stats.noData') }}</p>
        </div>
      </NCard>
    </NGi>
    <NGi span="2 m:1">
      <NCard :title="t('stats.dedupFreed')" size="small">
        <div class="chart-wrap">
          <Bar v-if="(dedupTrend?.data_points.length ?? 0) > 0" :data="dedupChartData" :options="bytesBarOptions" />
          <p v-else class="no-data">{{ t('stats.noData') }}</p>
        </div>
      </NCard>
    </NGi>
  </NGrid>
</template>
```

### Step 3: Commit

- [ ] Commit:

```bash
git add src/pages/stats/tabs/RunsOverviewTab.vue src/pages/stats/tabs/SystemInfraTab.vue
git commit -m "refactor(stats): extract RunsOverview and SystemInfra tab components"
```

---

## Task 8: Frontend — Create Spider Detail Tab (A1-A4)

**Files:**
- Create: `src/pages/stats/tabs/SpiderDetailTab.vue`

### Step 1: Create SpiderDetailTab.vue

- [ ] Create `src/pages/stats/tabs/SpiderDetailTab.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NCard, NGrid, NGi, NSpin } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line, Bar } from 'vue-chartjs'
import { getStatsTrend, type TrendResponse } from '@/api/stats'
import { stackedBarOptions, percentLineOptions } from '../chartOptions'

const props = defineProps<{ period: string }>()

const { t } = useI18n()
const loading = ref(false)

const processedTrend = ref<TrendResponse | null>(null)
const skippedTrend = ref<TrendResponse | null>(null)
const nonewTrend = ref<TrendResponse | null>(null)
const failedTrend = ref<TrendResponse | null>(null)
const efficiencyTrend = ref<TrendResponse | null>(null)
const skipRateTrend = ref<TrendResponse | null>(null)
const failureRateTrend = ref<TrendResponse | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const [pr, sk, nn, fa, ef, sr, fr] = await Promise.all([
      getStatsTrend('spider_processed', props.period),
      getStatsTrend('spider_skipped', props.period),
      getStatsTrend('spider_nonew', props.period),
      getStatsTrend('spider_failed', props.period),
      getStatsTrend('spider_efficiency', props.period),
      getStatsTrend('spider_skip_rate', props.period),
      getStatsTrend('spider_failure_rate', props.period),
    ])
    processedTrend.value = pr
    skippedTrend.value = sk
    nonewTrend.value = nn
    failedTrend.value = fa
    efficiencyTrend.value = ef
    skipRateTrend.value = sr
    failureRateTrend.value = fr
  } finally {
    loading.value = false
  }
}

watch(() => props.period, fetchData)
onMounted(fetchData)

// A1: Stacked bar — merge 4 series by date
const breakdownChartData = computed(() => {
  const dates = processedTrend.value?.data_points.map((d) => d.date) ?? []
  const toMap = (tr: TrendResponse | null) =>
    new Map((tr?.data_points ?? []).map((d) => [d.date, d.value]))
  const pMap = toMap(processedTrend.value)
  const sMap = toMap(skippedTrend.value)
  const nMap = toMap(nonewTrend.value)
  const fMap = toMap(failedTrend.value)
  return {
    labels: dates,
    datasets: [
      { label: t('stats.spiderProcessed'), data: dates.map((d) => pMap.get(d) ?? 0), backgroundColor: '#18a058' },
      { label: t('stats.spiderSkipped'), data: dates.map((d) => sMap.get(d) ?? 0), backgroundColor: '#a0a0a0' },
      { label: t('stats.spiderNoNew'), data: dates.map((d) => nMap.get(d) ?? 0), backgroundColor: '#6395ff' },
      { label: t('stats.spiderFailed'), data: dates.map((d) => fMap.get(d) ?? 0), backgroundColor: '#d03050' },
    ],
  }
})

function ratioChartData(trend: TrendResponse | null, label: string, color: string) {
  return {
    labels: trend?.data_points.map((d) => d.date) ?? [],
    datasets: [
      {
        label,
        data: trend?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
        borderColor: color,
        backgroundColor: color.replace(')', ',0.1)').replace('rgb', 'rgba'),
        fill: true,
        tension: 0.3,
      },
    ],
  }
}

const efficiencyData = computed(() => ratioChartData(efficiencyTrend.value, t('stats.discoveryEfficiency'), '#18a058'))
const skipRateData = computed(() => ratioChartData(skipRateTrend.value, t('stats.skipRate'), '#a0a0a0'))
const failureRateData = computed(() => ratioChartData(failureRateTrend.value, t('stats.failureRate'), '#d03050'))

const hasBreakdownData = computed(() => (processedTrend.value?.data_points.length ?? 0) > 0)
</script>

<template>
  <NSpin :show="loading">
    <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
      <NGi span="2">
        <NCard :title="t('stats.phaseBreakdown')" size="small">
          <div class="chart-wrap">
            <Bar v-if="hasBreakdownData" :data="breakdownChartData" :options="stackedBarOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.discoveryEfficiency')" size="small">
          <div class="chart-wrap">
            <Line v-if="(efficiencyTrend?.data_points.length ?? 0) > 0" :data="efficiencyData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.skipRate')" size="small">
          <div class="chart-wrap">
            <Line v-if="(skipRateTrend?.data_points.length ?? 0) > 0" :data="skipRateData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.failureRate')" size="small">
          <div class="chart-wrap">
            <Line v-if="(failureRateTrend?.data_points.length ?? 0) > 0" :data="failureRateData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
    </NGrid>
  </NSpin>
</template>
```

### Step 2: Commit

- [ ] Commit:

```bash
git add src/pages/stats/tabs/SpiderDetailTab.vue
git commit -m "feat(stats): add SpiderDetailTab component (A1-A4)"
```

---

## Task 9: Frontend — Create Content Tab Components (B1-B5)

**Files:**
- Create: `src/pages/stats/tabs/ContentQualityTab.vue`
- Create: `src/pages/stats/tabs/ContentCoverageTab.vue`

### Step 1: Create ContentQualityTab.vue

- [ ] Create `src/pages/stats/tabs/ContentQualityTab.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NCard, NGrid, NGi, NSpin } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line, Bar } from 'vue-chartjs'
import { getStatsTrend, getStatsDistribution, type TrendResponse, type DistributionResponse } from '@/api/stats'
import { ratingLineOptions, barChartOptions } from '../chartOptions'

const props = defineProps<{ period: string }>()

const { t } = useI18n()
const loading = ref(false)

const avgRatingTrend = ref<TrendResponse | null>(null)
const ratingDist = ref<DistributionResponse | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const [ar, rd] = await Promise.all([
      getStatsTrend('avg_rating', props.period),
      getStatsDistribution('rating_distribution', props.period),
    ])
    avgRatingTrend.value = ar
    ratingDist.value = rd
  } finally {
    loading.value = false
  }
}

watch(() => props.period, fetchData)
onMounted(fetchData)

const avgRatingData = computed(() => ({
  labels: avgRatingTrend.value?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.avgRating'),
      data: avgRatingTrend.value?.data_points.map((d) => Math.round(d.value * 10) / 10) ?? [],
      borderColor: '#f0a020',
      backgroundColor: 'rgba(240,160,32,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const ratingDistData = computed(() => ({
  labels: ratingDist.value?.buckets.map((b) => b.label) ?? [],
  datasets: [
    {
      label: t('stats.ratingDistribution'),
      data: ratingDist.value?.buckets.map((b) => b.value) ?? [],
      backgroundColor: [
        'rgba(208,48,80,0.7)',
        'rgba(240,160,32,0.7)',
        'rgba(99,149,255,0.7)',
        'rgba(24,160,88,0.7)',
        'rgba(114,46,209,0.7)',
      ],
    },
  ],
}))
</script>

<template>
  <NSpin :show="loading">
    <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
      <NGi span="2 m:1">
        <NCard :title="t('stats.avgRating')" size="small">
          <div class="chart-wrap">
            <Line v-if="(avgRatingTrend?.data_points.length ?? 0) > 0" :data="avgRatingData" :options="ratingLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.ratingDistribution')" size="small">
          <div class="chart-wrap">
            <Bar v-if="(ratingDist?.buckets.length ?? 0) > 0" :data="ratingDistData" :options="barChartOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
    </NGrid>
  </NSpin>
</template>
```

### Step 2: Create ContentCoverageTab.vue

- [ ] Create `src/pages/stats/tabs/ContentCoverageTab.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NCard, NGrid, NGi, NSpin } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line, Doughnut } from 'vue-chartjs'
import { DoughnutController } from 'chart.js'
import { Chart as ChartJS } from 'chart.js'
import { getStatsTrend, getStatsDistribution, type TrendResponse, type DistributionResponse } from '@/api/stats'
import { percentLineOptions, dualLineOptions, doughnutOptions } from '../chartOptions'

// Register DoughnutController (not registered in StatsPage's global registration)
ChartJS.register(DoughnutController)

const props = defineProps<{ period: string }>()

const { t } = useI18n()
const loading = ref(false)

const subtitleTrend = ref<TrendResponse | null>(null)
const resDist = ref<DistributionResponse | null>(null)
const hiresTrend = ref<TrendResponse | null>(null)
const perfectMatchTrend = ref<TrendResponse | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const [sc, rd, hr, pm] = await Promise.all([
      getStatsTrend('subtitle_coverage', props.period),
      getStatsDistribution('resolution_distribution', props.period),
      getStatsTrend('hires_ratio', props.period),
      getStatsTrend('perfectmatch_ratio', props.period),
    ])
    subtitleTrend.value = sc
    resDist.value = rd
    hiresTrend.value = hr
    perfectMatchTrend.value = pm
  } finally {
    loading.value = false
  }
}

watch(() => props.period, fetchData)
onMounted(fetchData)

const subtitleData = computed(() => ({
  labels: subtitleTrend.value?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.subtitleCoverage'),
      data: subtitleTrend.value?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
      borderColor: '#18a058',
      backgroundColor: 'rgba(24,160,88,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const RESOLUTION_COLORS = ['#a0a0a0', '#f0a020', '#18a058', '#722ed1']

const resDistData = computed(() => ({
  labels: resDist.value?.buckets.map((b) => b.label) ?? [],
  datasets: [
    {
      data: resDist.value?.buckets.map((b) => b.value) ?? [],
      backgroundColor: resDist.value?.buckets.map((_, i) => RESOLUTION_COLORS[i] ?? '#6395ff') ?? [],
    },
  ],
}))

const hiresMatchData = computed(() => {
  const dates = hiresTrend.value?.data_points.map((d) => d.date) ?? []
  return {
    labels: dates,
    datasets: [
      {
        label: t('stats.hiresRatio'),
        data: hiresTrend.value?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
        borderColor: '#722ed1',
        backgroundColor: 'rgba(114,46,209,0.1)',
        fill: false,
        tension: 0.3,
      },
      {
        label: t('stats.perfectMatchRatio'),
        data: perfectMatchTrend.value?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
        borderColor: '#18a058',
        backgroundColor: 'rgba(24,160,88,0.1)',
        fill: false,
        tension: 0.3,
      },
    ],
  }
})
</script>

<template>
  <NSpin :show="loading">
    <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
      <NGi span="2 m:1">
        <NCard :title="t('stats.subtitleCoverage')" size="small">
          <div class="chart-wrap">
            <Line v-if="(subtitleTrend?.data_points.length ?? 0) > 0" :data="subtitleData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.resolutionDistribution')" size="small">
          <div class="chart-wrap">
            <Doughnut v-if="(resDist?.buckets.length ?? 0) > 0" :data="resDistData" :options="doughnutOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2">
        <NCard :title="t('stats.hiresRatio') + ' / ' + t('stats.perfectMatchRatio')" size="small">
          <div class="chart-wrap">
            <Line v-if="(hiresTrend?.data_points.length ?? 0) > 0" :data="hiresMatchData" :options="dualLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
    </NGrid>
  </NSpin>
</template>
```

### Step 3: Commit

- [ ] Commit:

```bash
git add src/pages/stats/tabs/ContentQualityTab.vue src/pages/stats/tabs/ContentCoverageTab.vue
git commit -m "feat(stats): add Content Quality and Coverage tab components (B1-B5)"
```

---

## Task 10: Frontend — Create Upload Tab Components (C1-C4)

**Files:**
- Create: `src/pages/stats/tabs/UploadQbTab.vue`
- Create: `src/pages/stats/tabs/UploadPikpakTab.vue`

### Step 1: Create UploadQbTab.vue

- [ ] Create `src/pages/stats/tabs/UploadQbTab.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NCard, NGrid, NGi, NSpin } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line } from 'vue-chartjs'
import { getStatsTrend, type TrendResponse } from '@/api/stats'
import { percentLineOptions } from '../chartOptions'

const props = defineProps<{ period: string }>()

const { t } = useI18n()
const loading = ref(false)

const uploadSuccessTrend = ref<TrendResponse | null>(null)
const duplicateRateTrend = ref<TrendResponse | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const [us, dr] = await Promise.all([
      getStatsTrend('upload_success_rate', props.period),
      getStatsTrend('duplicate_rate', props.period),
    ])
    uploadSuccessTrend.value = us
    duplicateRateTrend.value = dr
  } finally {
    loading.value = false
  }
}

watch(() => props.period, fetchData)
onMounted(fetchData)

const uploadSuccessData = computed(() => ({
  labels: uploadSuccessTrend.value?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.uploadSuccessRate'),
      data: uploadSuccessTrend.value?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
      borderColor: '#18a058',
      backgroundColor: 'rgba(24,160,88,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const duplicateRateData = computed(() => ({
  labels: duplicateRateTrend.value?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.duplicateRate'),
      data: duplicateRateTrend.value?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
      borderColor: '#f0a020',
      backgroundColor: 'rgba(240,160,32,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))
</script>

<template>
  <NSpin :show="loading">
    <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
      <NGi span="2 m:1">
        <NCard :title="t('stats.uploadSuccessRate')" size="small">
          <div class="chart-wrap">
            <Line v-if="(uploadSuccessTrend?.data_points.length ?? 0) > 0" :data="uploadSuccessData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.duplicateRate')" size="small">
          <div class="chart-wrap">
            <Line v-if="(duplicateRateTrend?.data_points.length ?? 0) > 0" :data="duplicateRateData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
    </NGrid>
  </NSpin>
</template>
```

### Step 2: Create UploadPikpakTab.vue

- [ ] Create `src/pages/stats/tabs/UploadPikpakTab.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NCard, NGrid, NGi, NSpin } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Line, Bar } from 'vue-chartjs'
import { getStatsTrend, type TrendResponse } from '@/api/stats'
import { percentLineOptions, stackedBarOptions } from '../chartOptions'

const props = defineProps<{ period: string }>()

const { t } = useI18n()
const loading = ref(false)

const pikpakSuccessTrend = ref<TrendResponse | null>(null)
const pikpakFailedTrend = ref<TrendResponse | null>(null)
const pikpakDeleteFailedTrend = ref<TrendResponse | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const [ps, pf, pdf] = await Promise.all([
      getStatsTrend('pikpak_success_rate', props.period),
      getStatsTrend('pikpak_failed', props.period),
      getStatsTrend('pikpak_delete_failed', props.period),
    ])
    pikpakSuccessTrend.value = ps
    pikpakFailedTrend.value = pf
    pikpakDeleteFailedTrend.value = pdf
  } finally {
    loading.value = false
  }
}

watch(() => props.period, fetchData)
onMounted(fetchData)

const pikpakSuccessData = computed(() => ({
  labels: pikpakSuccessTrend.value?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.pikpakSuccessRate'),
      data: pikpakSuccessTrend.value?.data_points.map((d) => Math.round(d.value * 100)) ?? [],
      borderColor: '#18a058',
      backgroundColor: 'rgba(24,160,88,0.1)',
      fill: true,
      tension: 0.3,
    },
  ],
}))

const failureDetailData = computed(() => {
  const dates = pikpakFailedTrend.value?.data_points.map((d) => d.date) ?? []
  const dfMap = new Map((pikpakDeleteFailedTrend.value?.data_points ?? []).map((d) => [d.date, d.value]))
  return {
    labels: dates,
    datasets: [
      {
        label: t('stats.pikpakFailed'),
        data: pikpakFailedTrend.value?.data_points.map((d) => d.value) ?? [],
        backgroundColor: '#d03050',
      },
      {
        label: t('stats.pikpakDeleteFailed'),
        data: dates.map((d) => dfMap.get(d) ?? 0),
        backgroundColor: '#f0a020',
      },
    ],
  }
})
</script>

<template>
  <NSpin :show="loading">
    <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
      <NGi span="2 m:1">
        <NCard :title="t('stats.pikpakSuccessRate')" size="small">
          <div class="chart-wrap">
            <Line v-if="(pikpakSuccessTrend?.data_points.length ?? 0) > 0" :data="pikpakSuccessData" :options="percentLineOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.pikpakFailureDetail')" size="small">
          <div class="chart-wrap">
            <Bar v-if="(pikpakFailedTrend?.data_points.length ?? 0) > 0" :data="failureDetailData" :options="stackedBarOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
    </NGrid>
  </NSpin>
</template>
```

### Step 3: Commit

- [ ] Commit:

```bash
git add src/pages/stats/tabs/UploadQbTab.vue src/pages/stats/tabs/UploadPikpakTab.vue
git commit -m "feat(stats): add Upload QB and PikPak tab components (C1-C4)"
```

---

## Task 11: Frontend — Create System Operations Tab (D1-D2)

**Files:**
- Create: `src/pages/stats/tabs/SystemOpsTab.vue`

### Step 1: Create SystemOpsTab.vue

- [ ] Create `src/pages/stats/tabs/SystemOpsTab.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NCard, NGrid, NGi, NSpin } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { Bar } from 'vue-chartjs'
import { getStatsTrend, type TrendResponse } from '@/api/stats'
import { stackedBarOptions, barChartOptions } from '../chartOptions'

const props = defineProps<{ period: string }>()

const { t } = useI18n()
const loading = ref(false)

const emailSentTrend = ref<TrendResponse | null>(null)
const emailFailedTrend = ref<TrendResponse | null>(null)
const emailResentTrend = ref<TrendResponse | null>(null)
const incidentsTrend = ref<TrendResponse | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const [es, ef, er, oi] = await Promise.all([
      getStatsTrend('email_sent', props.period),
      getStatsTrend('email_failed', props.period),
      getStatsTrend('email_resent', props.period),
      getStatsTrend('ops_incidents', props.period),
    ])
    emailSentTrend.value = es
    emailFailedTrend.value = ef
    emailResentTrend.value = er
    incidentsTrend.value = oi
  } finally {
    loading.value = false
  }
}

watch(() => props.period, fetchData)
onMounted(fetchData)

const emailChartData = computed(() => {
  // Collect all unique dates across the three series
  const dateSet = new Set<string>()
  for (const tr of [emailSentTrend.value, emailFailedTrend.value, emailResentTrend.value]) {
    for (const dp of tr?.data_points ?? []) dateSet.add(dp.date)
  }
  const dates = [...dateSet].sort()
  const toMap = (tr: TrendResponse | null) =>
    new Map((tr?.data_points ?? []).map((d) => [d.date, d.value]))
  const sMap = toMap(emailSentTrend.value)
  const fMap = toMap(emailFailedTrend.value)
  const rMap = toMap(emailResentTrend.value)
  return {
    labels: dates,
    datasets: [
      { label: t('stats.emailSent'), data: dates.map((d) => sMap.get(d) ?? 0), backgroundColor: '#18a058' },
      { label: t('stats.emailFailed'), data: dates.map((d) => fMap.get(d) ?? 0), backgroundColor: '#d03050' },
      { label: t('stats.emailResent'), data: dates.map((d) => rMap.get(d) ?? 0), backgroundColor: '#f0a020' },
    ],
  }
})

const hasEmailData = computed(() =>
  (emailSentTrend.value?.data_points.length ?? 0) > 0 ||
  (emailFailedTrend.value?.data_points.length ?? 0) > 0 ||
  (emailResentTrend.value?.data_points.length ?? 0) > 0,
)

const incidentsData = computed(() => ({
  labels: incidentsTrend.value?.data_points.map((d) => d.date) ?? [],
  datasets: [
    {
      label: t('stats.opsIncidents'),
      data: incidentsTrend.value?.data_points.map((d) => d.value) ?? [],
      backgroundColor: 'rgba(208,48,80,0.7)',
    },
  ],
}))
</script>

<template>
  <NSpin :show="loading">
    <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
      <NGi span="2 m:1">
        <NCard :title="t('stats.emailNotifications')" size="small">
          <div class="chart-wrap">
            <Bar v-if="hasEmailData" :data="emailChartData" :options="stackedBarOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
      <NGi span="2 m:1">
        <NCard :title="t('stats.opsIncidents')" size="small">
          <div class="chart-wrap">
            <Bar v-if="(incidentsTrend?.data_points.length ?? 0) > 0" :data="incidentsData" :options="barChartOptions" />
            <p v-else class="no-data">{{ t('stats.noData') }}</p>
          </div>
        </NCard>
      </NGi>
    </NGrid>
  </NSpin>
</template>
```

### Step 2: Commit

- [ ] Commit:

```bash
git add src/pages/stats/tabs/SystemOpsTab.vue
git commit -m "feat(stats): add System Operations tab component (D1-D2)"
```

---

## Task 12: Frontend — Rewrite StatsPage with New Tab Structure

**Files:**
- Modify: `src/pages/stats/StatsPage.vue`

### Step 1: Rewrite StatsPage.vue

- [ ] Replace the **entire** contents of `src/pages/stats/StatsPage.vue` with:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NCard,
  NGrid,
  NGi,
  NStatistic,
  NTabs,
  NTabPane,
  NSelect,
  NSpin,
  NAlert,
  NButton,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  DoughnutController,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import { Line } from 'vue-chartjs'
import {
  getStatsSummary,
  getStatsTrend,
  type StatsSummary,
  type TrendResponse,
} from '@/api/stats'
import { formatBytesScaled, lineChartOptions } from './chartOptions'

// Tab components
import RunsOverviewTab from './tabs/RunsOverviewTab.vue'
import SpiderDetailTab from './tabs/SpiderDetailTab.vue'
import ContentQualityTab from './tabs/ContentQualityTab.vue'
import ContentCoverageTab from './tabs/ContentCoverageTab.vue'
import UploadQbTab from './tabs/UploadQbTab.vue'
import UploadPikpakTab from './tabs/UploadPikpakTab.vue'
import SystemInfraTab from './tabs/SystemInfraTab.vue'
import SystemOpsTab from './tabs/SystemOpsTab.vue'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  DoughnutController,
  Title,
  Tooltip,
  Legend,
  Filler,
)

const { t } = useI18n()

// --- State ---
const loadingSummary = ref(false)
const summaryError = ref<string | null>(null)
const summary = ref<StatsSummary | null>(null)

const activeTab = ref('runs')
const period = ref('30d')

// Runs Overview data (fetched at StatsPage level for backward compat)
const loadingRunsOverview = ref(false)
const runsOverviewError = ref<string | null>(null)
const successRateTrend = ref<TrendResponse | null>(null)
const moviesTrend = ref<TrendResponse | null>(null)
const durationTrend = ref<TrendResponse | null>(null)
const torrentsTrend = ref<TrendResponse | null>(null)

// Growth data
const loadingGrowth = ref(false)
const historyGrowthTrend = ref<TrendResponse | null>(null)
const pikpakTrend = ref<TrendResponse | null>(null)

// System Infra data
const loadingSystemInfra = ref(false)
const proxyBansTrend = ref<TrendResponse | null>(null)
const dedupTrend = ref<TrendResponse | null>(null)

// Sub-tab state
const runsSubTab = ref('overview')
const contentSubTab = ref('quality')
const uploadSubTab = ref('qbittorrent')
const systemSubTab = ref('infrastructure')

// --- Period options ---
const periodOptions = computed(() => [
  { label: t('stats.period.7d'), value: '7d' },
  { label: t('stats.period.30d'), value: '30d' },
  { label: t('stats.period.90d'), value: '90d' },
])

// --- Formatters ---
function formatSuccessRate(rate: number | null): string {
  if (rate === null) return t('stats.na')
  return `${(rate * 100).toFixed(1)}%`
}

function formatDuration(secs: number | null): string {
  if (secs === null) return t('stats.na')
  return t('stats.seconds', { n: Math.round(secs) })
}

// --- Data fetching ---
async function fetchSummary() {
  loadingSummary.value = true
  summaryError.value = null
  try {
    summary.value = await getStatsSummary()
  } catch (err) {
    summaryError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loadingSummary.value = false
  }
}

async function fetchRunsOverview() {
  loadingRunsOverview.value = true
  runsOverviewError.value = null
  try {
    const [sr, mv, du, tr] = await Promise.all([
      getStatsTrend('success_rate', period.value),
      getStatsTrend('movies', period.value),
      getStatsTrend('duration', period.value),
      getStatsTrend('torrents', period.value),
    ])
    successRateTrend.value = sr
    moviesTrend.value = mv
    durationTrend.value = du
    torrentsTrend.value = tr
  } catch (err) {
    runsOverviewError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loadingRunsOverview.value = false
  }
}

async function fetchGrowth() {
  loadingGrowth.value = true
  try {
    const [hg, pp] = await Promise.all([
      getStatsTrend('history_growth', period.value),
      getStatsTrend('pikpak', period.value),
    ])
    historyGrowthTrend.value = hg
    pikpakTrend.value = pp
  } finally {
    loadingGrowth.value = false
  }
}

async function fetchSystemInfra() {
  loadingSystemInfra.value = true
  try {
    const [pb, dd] = await Promise.all([
      getStatsTrend('proxy_bans', period.value),
      getStatsTrend('dedup', period.value),
    ])
    proxyBansTrend.value = pb
    dedupTrend.value = dd
  } finally {
    loadingSystemInfra.value = false
  }
}

function fetchForActiveTab() {
  if (activeTab.value === 'runs' && runsSubTab.value === 'overview') {
    void fetchRunsOverview()
  } else if (activeTab.value === 'growth') {
    void fetchGrowth()
  } else if (activeTab.value === 'system' && systemSubTab.value === 'infrastructure') {
    void fetchSystemInfra()
  }
  // Other sub-tabs fetch their own data internally
}

watch([activeTab, period], () => fetchForActiveTab())
watch(runsSubTab, () => { if (activeTab.value === 'runs') fetchForActiveTab() })
watch(systemSubTab, () => { if (activeTab.value === 'system') fetchForActiveTab() })

onMounted(() => {
  void fetchSummary()
  fetchForActiveTab()
})
</script>

<template>
  <div class="stats-page">
    <header class="page-header">
      <h1>{{ t('nav.stats') }}</h1>
      <p class="subtitle">{{ t('stats.subtitle') }}</p>
    </header>

    <!-- Summary cards -->
    <NSpin :show="loadingSummary">
      <NAlert v-if="summaryError" type="error" class="summary-error">
        {{ summaryError }}
        <NButton size="small" style="margin-left: 12px" @click="fetchSummary">{{ t('common.retry') }}</NButton>
      </NAlert>

      <NGrid v-else :cols="4" :x-gap="12" :y-gap="12" responsive="screen" :item-responsive="true" class="summary-grid">
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.totalRuns')" :value="summary?.total_runs ?? '—'" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.successRate')" :value="formatSuccessRate(summary?.success_rate ?? null)" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.avgDuration')" :value="formatDuration(summary?.avg_duration_seconds ?? null)" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.totalMovies')" :value="summary?.total_movies ?? '—'" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.totalTorrents')" :value="summary?.total_torrents ?? '—'" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.pikpakVolume')" :value="summary?.total_pikpak ?? '—'" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.dedupFreed')" :value="summary != null ? formatBytesScaled(summary.total_dedup_freed_bytes) : '—'" /></NCard>
        </NGi>
        <NGi span="4 s:2 m:1">
          <NCard size="small"><NStatistic :label="t('stats.proxyBans')" :value="summary?.proxy_bans_last_7d ?? '—'" /></NCard>
        </NGi>
      </NGrid>
    </NSpin>

    <!-- Charts section -->
    <NCard class="charts-card" :bordered="false">
      <div class="charts-toolbar">
        <NSelect v-model:value="period" :options="periodOptions" size="small" style="width: 120px" />
      </div>

      <NTabs v-model:value="activeTab" type="line" animated>
        <!-- ═══ Runs ═══ -->
        <NTabPane name="runs" :tab="t('stats.tabs.runs')">
          <NTabs v-model:value="runsSubTab" type="segment" size="small" style="margin-bottom: 12px">
            <NTabPane name="overview" :tab="t('stats.subtabs.overview')">
              <NSpin :show="loadingRunsOverview">
                <NAlert v-if="runsOverviewError" type="error">{{ runsOverviewError }}</NAlert>
                <RunsOverviewTab
                  :success-rate-trend="successRateTrend"
                  :duration-trend="durationTrend"
                  :movies-trend="moviesTrend"
                  :torrents-trend="torrentsTrend"
                />
              </NSpin>
            </NTabPane>
            <NTabPane name="spider" :tab="t('stats.subtabs.spiderDetail')">
              <SpiderDetailTab :period="period" />
            </NTabPane>
          </NTabs>
        </NTabPane>

        <!-- ═══ Content ═══ -->
        <NTabPane name="content" :tab="t('stats.tabs.content')">
          <NTabs v-model:value="contentSubTab" type="segment" size="small" style="margin-bottom: 12px">
            <NTabPane name="quality" :tab="t('stats.subtabs.quality')">
              <ContentQualityTab :period="period" />
            </NTabPane>
            <NTabPane name="coverage" :tab="t('stats.subtabs.coverage')">
              <ContentCoverageTab :period="period" />
            </NTabPane>
          </NTabs>
        </NTabPane>

        <!-- ═══ Upload ═══ -->
        <NTabPane name="upload" :tab="t('stats.tabs.upload')">
          <NTabs v-model:value="uploadSubTab" type="segment" size="small" style="margin-bottom: 12px">
            <NTabPane name="qbittorrent" :tab="t('stats.subtabs.qbittorrent')">
              <UploadQbTab :period="period" />
            </NTabPane>
            <NTabPane name="pikpak" :tab="t('stats.subtabs.pikpak')">
              <UploadPikpakTab :period="period" />
            </NTabPane>
          </NTabs>
        </NTabPane>

        <!-- ═══ Growth (no sub-tabs) ═══ -->
        <NTabPane name="growth" :tab="t('stats.tabs.growth')">
          <NSpin :show="loadingGrowth">
            <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" class="charts-grid">
              <NGi span="2 m:1">
                <NCard :title="t('stats.totalMovies')" size="small">
                  <div class="chart-wrap">
                    <Line v-if="(historyGrowthTrend?.data_points.length ?? 0) > 0" :data="{
                      labels: historyGrowthTrend?.data_points.map((d) => d.date) ?? [],
                      datasets: [{ label: t('stats.totalMovies'), data: historyGrowthTrend?.data_points.map((d) => d.value) ?? [], borderColor: '#6395ff', backgroundColor: 'rgba(99,149,255,0.1)', fill: true, tension: 0.3 }]
                    }" :options="lineChartOptions" />
                    <p v-else class="no-data">{{ t('stats.noData') }}</p>
                  </div>
                </NCard>
              </NGi>
              <NGi span="2 m:1">
                <NCard :title="t('stats.pikpakVolume')" size="small">
                  <div class="chart-wrap">
                    <Line v-if="(pikpakTrend?.data_points.length ?? 0) > 0" :data="{
                      labels: pikpakTrend?.data_points.map((d) => d.date) ?? [],
                      datasets: [{ label: t('stats.pikpakVolume'), data: pikpakTrend?.data_points.map((d) => d.value) ?? [], borderColor: '#f0a020', backgroundColor: 'rgba(240,160,32,0.1)', fill: true, tension: 0.3 }]
                    }" :options="lineChartOptions" />
                    <p v-else class="no-data">{{ t('stats.noData') }}</p>
                  </div>
                </NCard>
              </NGi>
            </NGrid>
          </NSpin>
        </NTabPane>

        <!-- ═══ System ═══ -->
        <NTabPane name="system" :tab="t('stats.tabs.system')">
          <NTabs v-model:value="systemSubTab" type="segment" size="small" style="margin-bottom: 12px">
            <NTabPane name="infrastructure" :tab="t('stats.subtabs.infrastructure')">
              <NSpin :show="loadingSystemInfra">
                <SystemInfraTab :proxy-bans-trend="proxyBansTrend" :dedup-trend="dedupTrend" />
              </NSpin>
            </NTabPane>
            <NTabPane name="operations" :tab="t('stats.subtabs.operations')">
              <SystemOpsTab :period="period" />
            </NTabPane>
          </NTabs>
        </NTabPane>
      </NTabs>
    </NCard>
  </div>
</template>

<style scoped>
.stats-page {
  padding: 24px;
  max-width: 1200px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
}

.subtitle {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: 13px;
}

.summary-error {
  margin-bottom: 16px;
}

.summary-grid {
  margin-bottom: 24px;
}

.charts-card {
  padding: 0;
}

.charts-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.charts-grid {
  margin-top: 16px;
}

.chart-wrap {
  height: 240px;
  position: relative;
}

.no-data {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--n-text-color-3, #999);
  font-size: 13px;
  margin: 0;
}
</style>
```

Note: The Growth tab uses the `Line` component from `vue-chartjs` directly with inline data because it's only 2 charts and not worth an extra component file.

### Step 2: Verify the app builds

- [ ] Run:

```bash
cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npx vue-tsc --noEmit 2>&1 | tail -20
```

Expected: No type errors. If there are type errors related to `Line` usage in the Growth tab, import `Line` from `vue-chartjs` at the top of the script and use it directly instead of `component :is`.

### Step 3: Commit

- [ ] Commit:

```bash
git add src/pages/stats/StatsPage.vue
git commit -m "feat(stats): rewrite StatsPage with 5 main tabs and sub-tab layout"
```

---

## Task 13: Verify — Full Build and Test Suite

### Step 1: Run backend tests

- [ ] Run:

```bash
cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npm run test:server -- --reporter=verbose 2>&1 | tail -40
```

Expected: All tests PASS (existing + new).

### Step 2: Run typecheck

- [ ] Run:

```bash
npx vue-tsc --noEmit 2>&1 | tail -20
```

Expected: No type errors.

### Step 3: Run lint

- [ ] Run:

```bash
npm run lint 2>&1 | tail -20
```

Expected: No new lint errors. Fix any that appear (likely formatting).

### Step 4: Fix any issues found and commit

- [ ] If fixes were needed:

```bash
git add -A
git commit -m "fix(stats): resolve build/lint issues from chart expansion"
```
