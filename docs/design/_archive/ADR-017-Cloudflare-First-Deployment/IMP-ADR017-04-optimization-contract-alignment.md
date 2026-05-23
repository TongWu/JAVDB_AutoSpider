# Optimization + Contract Alignment — Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value                                                    |
| ----------- | -------------------------------------------------------- |
| **Status**  | Completed                                                |
| **Date**    | 2026-05-24                                               |
| **Related** | [ADR-017](ADR-017-cloudflare-first-deployment.md), [IMP-ADR017-03](IMP-ADR017-03-execution-gh-actions-bridge.md) |

**Goal:** Align TS backend response shapes with the frontend's OpenAPI contract (`api.gen.ts`), add missing high-value endpoints (stats, session commit/rollback, history export), stub remaining Python-only endpoints, and verify full API coverage.

**Architecture:** This phase fixes the "last mile" — the TS backend implements all routes but some response shapes don't match what the frontend expects (based on `api.gen.ts` generated from the Python backend's OpenAPI schema). The frontend uses `openapi-fetch` with these types, so shape mismatches cause TypeScript errors or runtime failures.

**Tech Stack:** Same as prior phases: Hono 4, D1 native bindings, Vitest + `@cloudflare/vitest-pool-workers`

**Working Directory:** All paths are relative to `JAVDB_AutoSpider_Web/` (`/Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/`).

---

## Design Decisions

### DD1: Fix tasks response to match `ListTasksResponse` / `JobSummaryResponse`

The frontend's `api.gen.ts` expects `ListTasksResponse`:
```typescript
ListTasksResponse: {
  next_schedule: NextScheduleInfo;
  tasks: JobSummaryResponse[];
}
```

Where `JobSummaryResponse` has fields: `job_id`, `status`, `kind`, `mode`, `url`, `command`, `log`, `log_size`, `source`, `created_at`, `completed_at`.

The current TS backend returns `{ items: JobRun[] }` where `JobRun` has `job_id`, `workflow`, `gh_run_id`, `status`, `inputs`, `created_at`, `updated_at`.

**Fix:** Map `job_runs` columns to `JobSummaryResponse` shape. Add `next_schedule` with static values (Cloudflare mode doesn't have cron info). Parse `inputs` JSON to extract `kind`/`url`/etc.

### DD2: Stats routes — pure D1 aggregations

`GET /api/stats/summary` aggregates counts across all three D1 databases:
- `total_movies` / `total_torrents` from HISTORY_DB
- `total_runs` / `success_rate` / `avg_duration_seconds` from REPORTS_DB
- `total_pikpak` / `total_dedup_freed_bytes` from OPERATIONS_DB

`GET /api/stats/trend` returns time-series data from ReportSessions, grouped by date.

### DD3: Session commit/rollback — D1 query + GH Actions dispatch

- `POST /api/sessions/{id}/commit` — Updates session status in REPORTS_DB, moves pending rows. This is a D1 write operation that can run in Workers.
- `POST /api/sessions/{id}/rollback` — Dispatches `RollbackD1.yml` via GH Actions (rollback is complex: cleans pending tables, reports, operations across all 3 DBs).

### DD4: History export — CSV from D1

`GET /api/history/movies/export` and `GET /api/history/torrents/export` return CSV data from D1. Use `text/csv` content type with appropriate headers.

### DD5: Remaining stubs — 501 for all Python-only endpoints

All endpoints that require subprocess, local filesystem, or direct network access get 501 stubs. This ensures the frontend gets a clean error message instead of a 404.

### DD6: `TaskStatsResponse` shape alignment

Frontend expects: `{ daily_success, daily_failed, daily_running, adhoc_running }`.
Current backend returns: `{ total, dispatched, in_progress, completed, failed }`.
**Fix:** Remap the response shape to match.

---

## File Map

### New Files

| File | Responsibility |
| ---- | -------------- |
| `server/routes/stats.ts` | `/api/stats/*` — summary + trend (D1 aggregate queries) |
| `server/routes/stubs.ts` | 501 stubs for all Python-only endpoints |
| `server/__tests__/stats-routes.test.ts` | Tests for stats routes |
| `server/__tests__/stubs-routes.test.ts` | Tests for stub routes |

### Modified Files

| File | Change |
| ---- | ------ |
| `server/routes/tasks.ts` | Fix response shapes to match `ListTasksResponse` / `JobSummaryResponse` / `TaskStatsResponse` |
| `server/routes/sessions.ts` | Add commit + rollback POST endpoints |
| `server/routes/history.ts` | Add CSV export endpoints |
| `server/app.ts` | Mount stats and stubs routes |
| `server/__tests__/tasks-routes.test.ts` | Update tests for new response shapes |
| `server/__tests__/sessions-routes.test.ts` | Add tests for commit/rollback |
| `server/__tests__/history-routes.test.ts` | Add tests for CSV export |

---

## Task Breakdown

### Task 1: Fix Tasks Response Shapes

**Files:**
- Modify: `server/routes/tasks.ts`
- Modify: `server/__tests__/tasks-routes.test.ts`

**Context:** The frontend uses typed API client generated from `api.gen.ts`. The `ListTasksResponse` type expects `{ tasks: JobSummaryResponse[], next_schedule: NextScheduleInfo }`. The `TaskStatsResponse` type expects `{ daily_success, daily_failed, daily_running, adhoc_running }`. The current TS backend returns incompatible shapes. This task fixes the response mapping.

**Response shape mapping:**

For `GET /api/tasks` — change response from `{ items: JobRun[] }` to:
```typescript
{
  tasks: Array<{
    job_id: string;
    status: string;
    kind: string | null;      // extracted from job_id prefix (e.g. "daily", "adhoc")
    mode: string | null;       // "pipeline" for daily, null for adhoc
    url: string | null;        // extracted from inputs JSON
    command: null;             // no command in Workers mode
    log: null;                 // no local log in Workers mode
    log_size: null;
    source: "gh_actions";      // always GH Actions in Workers mode
    created_at: string;
    completed_at: string | null; // use updated_at when status is terminal
  }>,
  next_schedule: {
    cron_pipeline: "N/A",      // no cron info in Workers mode
    cron_spider: "N/A",
    source: "cloudflare"
  }
}
```

For `GET /api/tasks/stats` — change response to:
```typescript
{
  daily_success: number;   // count where kind starts with "daily" and status = "completed"
  daily_failed: number;    // count where kind starts with "daily" and status in ("failure","cancelled")
  daily_running: number;   // count where kind starts with "daily" and status in ("dispatched","in_progress")
  adhoc_running: number;   // count where kind starts with "adhoc" and status in ("dispatched","in_progress")
}
```

- [ ] **Step 1: Update `server/routes/tasks.ts`**

Modify the `GET /` handler to return `{ tasks, next_schedule }` instead of `{ items }`. Add a `mapJobToSummary()` helper that transforms `JobRun` to `JobSummaryResponse` shape. Modify the `GET /stats` handler to return the `TaskStatsResponse` shape.

For stats, change the SQL in `job-runs.ts` stats() or add a new query in the route, doing:
```sql
SELECT
  SUM(CASE WHEN job_id LIKE 'daily-%' AND status = 'completed' THEN 1 ELSE 0 END) as daily_success,
  SUM(CASE WHEN job_id LIKE 'daily-%' AND status IN ('failure','cancelled') THEN 1 ELSE 0 END) as daily_failed,
  SUM(CASE WHEN job_id LIKE 'daily-%' AND status IN ('dispatched','in_progress','queued') THEN 1 ELSE 0 END) as daily_running,
  SUM(CASE WHEN job_id LIKE 'adhoc-%' AND status IN ('dispatched','in_progress','queued') THEN 1 ELSE 0 END) as adhoc_running
FROM job_runs
WHERE created_at >= datetime('now', '-7 days')
```

- [ ] **Step 2: Update `server/__tests__/tasks-routes.test.ts`**

Update assertions to check for `tasks` array (not `items`) and `next_schedule` object. Update stats assertions to check for `daily_success`, `daily_failed`, etc.

- [ ] **Step 3: Run tests**

Run: `npx vitest run --config vitest.server.config.ts server/__tests__/tasks-routes.test.ts`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add server/routes/tasks.ts server/__tests__/tasks-routes.test.ts
git commit -m "fix(api): align tasks response shapes with OpenAPI contract"
```

---

### Task 2: Stats Routes

**Files:**
- Create: `server/routes/stats.ts`
- Create: `server/__tests__/stats-routes.test.ts`
- Modify: `server/app.ts` (mount statsRoutes)

**Context:** The frontend expects `GET /api/stats/summary` returning `StatsSummary` and `GET /api/stats/trend` returning `TrendResponse`. These are pure D1 aggregate queries across all three databases. No subprocess needed.

**`StatsSummary` from `api.gen.ts`:**
```typescript
StatsSummary: {
  total_movies: number;
  total_torrents: number;
  total_runs: number;
  success_rate: number | null;
  avg_duration_seconds: number | null;
  total_pikpak: number;
  total_dedup_freed_bytes: number;
  proxy_bans_last_7d: number;
}
```

**`TrendResponse` from `api.gen.ts`:**
```typescript
TrendResponse: {
  metric: string;           // e.g. "movies", "runs"
  period: string;           // e.g. "7d", "30d"
  data_points: Array<{ date: string; value: number }>;
}
```

**SQL queries for summary:**
- `total_movies`: `SELECT COUNT(*) FROM MovieHistory` (HISTORY_DB)
- `total_torrents`: `SELECT COUNT(*) FROM TorrentHistory` (HISTORY_DB)
- `total_runs`: `SELECT COUNT(*) FROM ReportSessions` (REPORTS_DB)
- `success_rate`: `SELECT ROUND(CAST(SUM(CASE WHEN Status='committed' THEN 1 ELSE 0 END) AS REAL) / NULLIF(COUNT(*),0) * 100, 1) FROM ReportSessions` (REPORTS_DB)
- `avg_duration_seconds`: compute from `DateTimeCreated` and `CommittedAt` where both exist (REPORTS_DB)
- `total_pikpak`: `SELECT COUNT(*) FROM PikpakHistory` (OPERATIONS_DB) — catch if table doesn't exist
- `total_dedup_freed_bytes`: `SELECT COALESCE(SUM(ExistingFolderSize),0) FROM DedupRecords WHERE Status='completed'` (OPERATIONS_DB) — catch if table doesn't exist
- `proxy_bans_last_7d`: 0 (proxy ban data is in Python memory, not D1)

**SQL for trend** (metric=`runs`, period=`30d`):
```sql
SELECT DATE(DateTimeCreated) as date, COUNT(*) as value
FROM ReportSessions
WHERE DateTimeCreated >= datetime('now', '-30 days')
GROUP BY DATE(DateTimeCreated)
ORDER BY date
```

- [ ] **Step 1: Create `server/routes/stats.ts`**

Implement `GET /summary` and `GET /trend` with D1 queries. Use try/catch for tables that may not exist. Trend supports `?metric=runs|movies|torrents` and `?period=7d|30d|90d`.

- [ ] **Step 2: Mount in `server/app.ts`**

```typescript
import { statsRoutes } from "./routes/stats";
app.route("/api/stats", statsRoutes);
```

- [ ] **Step 3: Create `server/__tests__/stats-routes.test.ts`**

Seed ReportSessions with a few test rows in beforeAll. Test:
- GET /api/stats/summary returns all expected fields
- GET /api/stats/trend?metric=runs&period=7d returns data_points array

- [ ] **Step 4: Run tests and commit**

Run: `npx vitest run --config vitest.server.config.ts server/__tests__/stats-routes.test.ts`
Commit: `feat(api): add stats routes — summary + trend aggregations`

---

### Task 3: Session Commit + Rollback

**Files:**
- Modify: `server/routes/sessions.ts`
- Modify: `server/__tests__/sessions-routes.test.ts`

**Context:** The frontend needs `POST /api/sessions/{id}/commit` and `POST /api/sessions/{id}/rollback`. These are critical actions. Commit is a D1 operation (update status). Rollback dispatches `RollbackD1.yml` via GH Actions (rollback is complex, spans 3 databases).

**`SessionCommitPayload`:** `{ force?: boolean, drop_pending?: boolean, emit_metrics?: boolean, fanout_claims?: boolean }`
**`SessionCommitResponse`:** `{ session_id, new_state, pending_dropped }`

**`SessionRollbackPayload`:** `{ dry_run?: boolean, include_pending?: boolean }`
**`SessionRollbackResponse`:** `{ session_id, dry_run, actions: [], summary: {} }`

**For commit in Workers:**
- Read session from REPORTS_DB
- Validate session exists and status is `in_progress` or `finalizing`
- UPDATE ReportSessions SET Status = 'committed', CommittedAt = datetime('now')
- If `drop_pending`: DELETE from PendingMovieHistoryWrites / PendingTorrentHistoryWrites WHERE SessionId = ?
- Return `{ session_id, new_state: "committed", pending_dropped: count }`

**For rollback in Workers:**
- Dispatch `RollbackD1.yml` with `session_id` input
- Return `{ session_id, dry_run, actions: [{type: "dispatched", workflow: "RollbackD1.yml"}], summary: {dispatched: true} }`
- If `dry_run`: skip dispatch, return preview

- [ ] **Step 1: Add commit + rollback to `server/routes/sessions.ts`**

Add two POST handlers after the existing GET handlers. Both need admin role + CSRF. Commit does D1 writes. Rollback dispatches GH Actions (requireGhActions check).

- [ ] **Step 2: Update `server/__tests__/sessions-routes.test.ts`**

Add test cases:
- POST /api/sessions/{id}/commit succeeds with test session
- POST /api/sessions/{id}/commit returns 404 for unknown session
- POST /api/sessions/{id}/rollback returns 503 without GH token

- [ ] **Step 3: Run tests and commit**

Run: `npx vitest run --config vitest.server.config.ts server/__tests__/sessions-routes.test.ts`
Commit: `feat(api): add session commit/rollback endpoints`

---

### Task 4: History CSV Export

**Files:**
- Modify: `server/routes/history.ts`
- Modify: `server/__tests__/history-routes.test.ts`

**Context:** The frontend calls `GET /api/history/movies/export` and `GET /api/history/torrents/export` to download CSV files. These are D1 queries that stream results as `text/csv`.

- [ ] **Step 1: Add export endpoints to `server/routes/history.ts`**

Add two GET handlers:
- `GET /movies/export` — query all MovieHistory, format as CSV with headers, return with `Content-Type: text/csv` and `Content-Disposition: attachment; filename=movies.csv`
- `GET /torrents/export` — same for TorrentHistory

CSV columns for movies: `Id,Href,VideoCode,HiRes,PerfectMatch,ActorName,ActorGender,SupportingActors,TorrentCount,DatetimeCreated,DatetimeUpdated,SessionId`
CSV columns for torrents: `Id,MovieHref,MagnetUri,Size,ResolutionType,CensorIndicator,SubtitleIndicator,FileCount,DatetimeCreated,SessionId`

- [ ] **Step 2: Update `server/__tests__/history-routes.test.ts`**

Seed MovieHistory with test data, test that export returns CSV content-type.

- [ ] **Step 3: Run tests and commit**

Run: `npx vitest run --config vitest.server.config.ts server/__tests__/history-routes.test.ts`
Commit: `feat(api): add history CSV export endpoints`

---

### Task 5: Stub Routes for Python-Only Endpoints

**Files:**
- Create: `server/routes/stubs.ts`
- Create: `server/__tests__/stubs-routes.test.ts`
- Modify: `server/app.ts` (mount stubs)

**Context:** The frontend may call endpoints that only exist on the Python backend (dev tools, subprocess-based operations). These need to return 501 with a clear message instead of 404. This prevents frontend errors when switching between backends.

**Endpoints to stub (all return 501):**
- `POST /api/crawl/index`
- `POST /api/detect-page-type`
- `POST /api/health-check`
- `POST /api/login/refresh`
- `GET /api/logs/search`
- `POST /api/parse/category`
- `POST /api/parse/detail`
- `POST /api/parse/index`
- `POST /api/parse/tags`
- `POST /api/parse/top`
- `POST /api/parse/url`
- `POST /api/jobs/spider`
- `GET /api/jobs/:job_id/status`
- `GET /api/migrations/`
- `POST /api/migrations/:id/run`

Note: `/api/explore/download-magnet` is already handled by explore routes (it connects to qB, returns 501). Auth change-password (`POST /api/auth/change-password`) could be implemented later — for now, 501.

- [ ] **Step 1: Create `server/routes/stubs.ts`**

Use a helper function that returns a 501 JSON response. Mount each route.

- [ ] **Step 2: Mount in `server/app.ts`**

```typescript
import { stubRoutes } from "./routes/stubs";
// Mount BEFORE the 404 fallback, AFTER requireAuth()
app.route("/api", stubRoutes);
```

- [ ] **Step 3: Create `server/__tests__/stubs-routes.test.ts`**

Test a representative sample (3-4 endpoints) returning 501.

- [ ] **Step 4: Run tests and commit**

Run: `npx vitest run --config vitest.server.config.ts server/__tests__/stubs-routes.test.ts`
Commit: `feat(api): add 501 stubs for Python-only endpoints`

---

### Task 6: Full Test Suite + Deploy

**Files:**
- No new files

- [ ] **Step 1: Run full test suite**

Run: `npx vitest run --config vitest.server.config.ts`
Expected: All tests PASS.

- [ ] **Step 2: Build**

Run: `npm run build`

- [ ] **Step 3: Deploy**

Run: `npx wrangler deploy`

- [ ] **Step 4: Smoke test**

```bash
TOKEN=$(curl -s -X POST https://javdb-autospider-web.wuengineer.workers.dev/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"admin","password":"<pw>"}' | jq -r .access_token)

# Stats summary
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/stats/summary \
  -H "Authorization: Bearer $TOKEN" | jq .

# Tasks (new shape)
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/tasks \
  -H "Authorization: Bearer $TOKEN" | jq .

# Stub endpoint
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/parse/detail \
  -H "Authorization: Bearer $TOKEN" -X POST -H 'Content-Type: application/json' -d '{}' | jq .
```
