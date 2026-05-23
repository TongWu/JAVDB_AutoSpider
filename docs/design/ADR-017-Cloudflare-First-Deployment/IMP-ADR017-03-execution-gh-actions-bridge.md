# Execution + GH Actions Bridge — Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value                                                    |
| ----------- | -------------------------------------------------------- |
| **Status**  | Draft                                                    |
| **Date**    | 2026-05-24                                               |
| **Related** | [ADR-017](ADR-017-cloudflare-first-deployment.md), [IMP-ADR017-01](IMP-ADR017-01-cloudflare-pages-setup.md), [IMP-ADR017-02](IMP-ADR017-02-config-diagnostics-explore.md) |

**Goal:** Add task dispatch, operations, and GitHub Actions routes to the Cloudflare Worker API — enabling the frontend to trigger spider/pipeline runs, manage operations (rclone, qB filter, cleanup), and monitor GitHub Actions workflows.

**Architecture:** Workers cannot run subprocesses. ALL heavy operations (spider, pipeline, rclone, qB file filter, session rollback, stale cleanup) are dispatched to existing GitHub Actions workflows via the `workflow_dispatch` REST API. Job metadata is tracked in a D1 `job_runs` table in OPERATIONS_DB. The API endpoint triggers the dispatch and returns immediately with a `job_id`; the frontend polls for status updates via the GitHub Actions Runs API.

**Tech Stack:** Hono 4, D1 native bindings, GitHub REST API (fetch), Vitest + `@cloudflare/vitest-pool-workers`

**Working Directory:** All paths are relative to `JAVDB_AutoSpider_Web/` (`/Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/`).

---

## Design Decisions

### DD1: Task dispatch → GH Actions workflow_dispatch, not subprocess

Python backend runs tasks as child processes (`subprocess.Popen`). Workers have no subprocess capability. Every task dispatch becomes a `POST /repos/{repo}/actions/workflows/{workflow}/dispatches` call. The response is 204 No Content (GitHub doesn't return a run ID from dispatch), so we generate a local `job_id`, store it in D1, and the frontend polls GitHub Runs API to match the dispatched workflow run.

### DD2: Job metadata in D1 `job_runs` table

Python tracks jobs in memory (`JOBS` dict) + `.meta.json` files. Workers are stateless — no in-memory state persists across requests, no filesystem. Job metadata goes into a D1 table in OPERATIONS_DB:

```sql
CREATE TABLE IF NOT EXISTS job_runs (
    job_id       TEXT PRIMARY KEY,
    workflow     TEXT NOT NULL,
    gh_run_id    INTEGER,
    status       TEXT NOT NULL DEFAULT 'dispatched',
    inputs       TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Status values: `dispatched` (sent to GH), `queued` (GH picked up), `in_progress`, `completed`, `failure`, `cancelled`.

### DD3: No log streaming from Workers

Python streams logs from local `.log` files. In Workers, there are no local log files. The tasks detail endpoint returns the GitHub Actions logs URL (a 302 redirect to an S3 download). The frontend opens this URL directly for log access.

### DD4: GH Actions tier-based access control

The `GH_ACTIONS_TIER` env var controls which GH Actions endpoints are available:

| Tier | Endpoints |
| ---- | --------- |
| `none` (default) | All GH Actions endpoints return 503 |
| `monitor` | List workflows, list runs, get run logs |
| `edit` | + Get/update workflow YAML files |
| `admin` | + Dispatch workflows, manage secrets |

The tier is checked at the route level. `GH_ACTIONS_REPO` (format: `owner/repo`) and `GH_ACTIONS_TOKEN` (PAT) are required for any tier above `none`.

### DD5: Operations endpoints — stubs for Workers-incompatible features

Operations that need direct network access to internal services (qB torrent list, PikPak queue, SMTP email) cannot work from Workers. Strategy:

| Operation | Workers Mode |
| --------- | ------------ |
| qB filter-small | Dispatch `QBFileFilter.yml` via GH Actions |
| qB torrents list | 501 (requires direct qB API access) |
| PikPak transfer | 501 (requires direct PikPak API) |
| PikPak queue | 501 (requires direct PikPak API) |
| Email test/history/resend | 501 (requires SMTP) |
| Rclone run | Dispatch `RcloneManager.yml` via GH Actions |
| Rclone last | D1 query (RcloneInventory + DedupRecords) |
| Cleanup stale-sessions | Dispatch `StaleSessionCleanup.yml` via GH Actions |
| Cleanup claim-stages | 501 (requires Coordinator DO access) |

### DD6: Secrets management deferred

The GitHub Secrets API requires NaCl sealed-box encryption (libsodium). This needs `tweetnacl` or similar. The secrets endpoints (`GET/POST/DELETE /api/gh-actions/secrets`) are excluded from Phase 3 scope — they can be added in Phase 4 if needed. The Python backend handles secrets management.

### DD7: Task payload mapping

The frontend sends task payloads compatible with the Python backend. The TS backend maps these to GitHub Actions workflow dispatch inputs:

| Frontend payload field | GH Actions input |
| ---------------------- | ---------------- |
| `use_proxy` | (not a direct GH input — handled by workflow internals) |
| `dry_run` | `dry_run: "true"` (string, not boolean — GH dispatch inputs are strings) |
| `start_page` / `end_page` | `start_page` / `end_page` (strings) |
| `url` (adhoc) | `url` (string, required) |
| `phase` | `phase` (string) |
| `disable_all_filters` | `disable_all_filters: "true"` |

---

## File Map

### New Files

| File | Responsibility |
| ---- | -------------- |
| `server/services/gh-client.ts` | GitHub REST API client: dispatch workflow, list workflows/runs, get logs URL, get/update workflow content |
| `server/services/job-runs.ts` | D1 `job_runs` CRUD: create, update status, list, get by ID, stats |
| `server/routes/tasks.ts` | `/api/tasks/*` — daily/adhoc dispatch, list, stats, detail |
| `server/routes/gh-actions.ts` | `/api/gh-actions/*` — workflows, runs, dispatch, logs, workflow YAML |
| `server/routes/operations.ts` | `/api/ops/*` — rclone, qB filter, cleanup (dispatch); qB/pikpak/email (stubs) |
| `server/__tests__/tasks-routes.test.ts` | Tests for tasks routes |
| `server/__tests__/gh-actions-routes.test.ts` | Tests for GH Actions routes |
| `server/__tests__/operations-routes.test.ts` | Tests for operations routes |

### Modified Files

| File | Change |
| ---- | ------ |
| `server/app.ts` | Mount `tasksRoutes`, `ghActionsRoutes`, `operationsRoutes` |
| `server/env.ts` | No changes needed (GH_ACTIONS_* already present) |
| `vitest.server.config.ts` | Add `GH_ACTIONS_TIER: "admin"` and `GH_ACTIONS_REPO: "test-owner/test-repo"` bindings |

---

## Workflow Dispatch Input Reference

For subagent context — the actual GH Actions workflow inputs that the TS backend must map to:

**DailyIngestion.yml:**
- `write_mode_override`: choice (`""` | `"pending"`)
- `disable_all_filters`: boolean
- `always_bypass_time`: number (minutes)
- `runner`: string (runner type)

**AdHocIngestion.yml:**
- `write_mode_override`: choice
- `disable_all_filters`: boolean
- `url`: string (required)
- `start_page`: number
- `end_page`: number (probably, from grep context)

**QBFileFilter.yml:**
- `min_size_mb`: string (default `"100"`)
- `days`: string (default `"2"`)
- `categories`: string (JSON array, default `'["Ad Hoc", "Daily Ingestion", "顶级"]'`)
- `dry_run`: boolean (default `false`)
- `delete_local_files`: boolean

**RcloneManager.yml:**
- `scan`: boolean
- `report`: boolean
- `execute`: boolean
- `root_path`: string (default `"gdrive:/剧集/不可以色色/JAV-Sync"`)
- `dry_run`: boolean (default `true`)
- `incremental`: boolean

**RollbackD1.yml:**
- `session_id`: string
- `run_id`: string
- `attempt`: string
- `run_started_at`: string (ISO timestamp)

**StaleSessionCleanup.yml:**
- `max_age_hours`: string (default `"48"`)
- `apply`: boolean (default `false`)
- `scope`: string (default `"all"`)

---

## Task Breakdown

### Task 1: GH Client Service + Job Runs Service

**Files:**
- Create: `server/services/gh-client.ts`
- Create: `server/services/job-runs.ts`

**Context:** These are the two core services that all three route files depend on. `gh-client.ts` wraps the GitHub REST API. `job-runs.ts` wraps the D1 `job_runs` table CRUD. Both are standalone modules with no route-level dependencies, so they can be built and tested first.

**Auth pattern:** Follow the existing test auth helper pattern from `server/__tests__/config-routes.test.ts`. Each test file has `getToken()` and `getCsrf()` helpers.

**Test env:** Tests use `@cloudflare/vitest-pool-workers` with `cloudflareTest()` plugin. Access env via `import { env } from "cloudflare:test"`. miniflare 4.x rejects multi-statement `db.exec()`; use individual `db.prepare().run()` for seeding.

- [ ] **Step 1: Create `server/services/gh-client.ts`**

```typescript
// GitHub REST API client for Workers
// Uses native fetch() — no external HTTP library needed

interface GhClientConfig {
  token: string;
  repo: string; // "owner/repo"
}

interface WorkflowRun {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  created_at: string;
  updated_at: string;
  workflow_id: number;
}

interface Workflow {
  id: number;
  name: string;
  path: string;
  state: string;
}

export function createGhClient(config: GhClientConfig) {
  const { token, repo } = config;
  const baseUrl = `https://api.github.com/repos/${repo}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  return {
    async listWorkflows(): Promise<{ workflows: Workflow[] }> {
      const res = await fetch(`${baseUrl}/actions/workflows`, { headers });
      if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
      return res.json();
    },

    async listRuns(params?: {
      workflow_id?: number;
      per_page?: number;
      page?: number;
    }): Promise<{ total_count: number; workflow_runs: WorkflowRun[] }> {
      const qs = new URLSearchParams();
      if (params?.workflow_id) qs.set("workflow_id", String(params.workflow_id));
      qs.set("per_page", String(params?.per_page ?? 30));
      if (params?.page) qs.set("page", String(params.page));
      const res = await fetch(`${baseUrl}/actions/runs?${qs}`, { headers });
      if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
      return res.json();
    },

    async dispatchWorkflow(
      workflowFile: string,
      inputs: Record<string, string>,
      ref = "main",
    ): Promise<void> {
      const res = await fetch(
        `${baseUrl}/actions/workflows/${workflowFile}/dispatches`,
        {
          method: "POST",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({ ref, inputs }),
        },
      );
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Dispatch failed (${res.status}): ${body}`);
      }
      // 204 No Content on success
    },

    async getRunLogsUrl(runId: number): Promise<string> {
      const res = await fetch(`${baseUrl}/actions/runs/${runId}/logs`, {
        headers,
        redirect: "manual",
      });
      if (res.status === 302) {
        return res.headers.get("Location") ?? "";
      }
      if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
      return "";
    },

    async getWorkflowContent(
      filename: string,
    ): Promise<{ content: string; sha: string }> {
      const res = await fetch(
        `${baseUrl}/contents/.github/workflows/${filename}`,
        { headers: { ...headers, Accept: "application/vnd.github.raw+json" } },
      );
      if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
      // Need the SHA for updates — re-fetch with JSON accept
      const jsonRes = await fetch(
        `${baseUrl}/contents/.github/workflows/${filename}`,
        { headers },
      );
      const meta = (await jsonRes.json()) as { sha: string };
      const content = await res.text();
      return { content, sha: meta.sha };
    },

    async updateWorkflowContent(
      filename: string,
      content: string,
      sha: string,
      message: string,
    ): Promise<void> {
      const res = await fetch(
        `${baseUrl}/contents/.github/workflows/${filename}`,
        {
          method: "PUT",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            content: btoa(content),
            sha,
          }),
        },
      );
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Update failed (${res.status}): ${body}`);
      }
    },
  };
}

export type GhClient = ReturnType<typeof createGhClient>;
```

- [ ] **Step 2: Create `server/services/job-runs.ts`**

```typescript
interface JobRun {
  job_id: string;
  workflow: string;
  gh_run_id: number | null;
  status: string;
  inputs: string | null;
  created_at: string;
  updated_at: string;
}

function generateJobId(kind: string): string {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, "");
  const time = now.toISOString().slice(11, 19).replace(/:/g, "");
  const rand = Math.random().toString(16).slice(2, 6);
  return `${kind}-${date}-${time}-${rand}`;
}

export function createJobRunsRepo(db: D1Database) {
  return {
    async ensureTable(): Promise<void> {
      await db
        .prepare(
          `CREATE TABLE IF NOT EXISTS job_runs (
            job_id       TEXT PRIMARY KEY,
            workflow     TEXT NOT NULL,
            gh_run_id    INTEGER,
            status       TEXT NOT NULL DEFAULT 'dispatched',
            inputs       TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
          )`,
        )
        .run();
    },

    async create(
      kind: string,
      workflow: string,
      inputs: Record<string, string>,
    ): Promise<JobRun> {
      const jobId = generateJobId(kind);
      await db
        .prepare(
          `INSERT INTO job_runs (job_id, workflow, inputs)
           VALUES (?, ?, ?)`,
        )
        .bind(jobId, workflow, JSON.stringify(inputs))
        .run();
      const row = await db
        .prepare("SELECT * FROM job_runs WHERE job_id = ?")
        .bind(jobId)
        .first<JobRun>();
      return row!;
    },

    async updateStatus(
      jobId: string,
      status: string,
      ghRunId?: number,
    ): Promise<void> {
      if (ghRunId !== undefined) {
        await db
          .prepare(
            `UPDATE job_runs SET status = ?, gh_run_id = ?, updated_at = datetime('now')
             WHERE job_id = ?`,
          )
          .bind(status, ghRunId, jobId)
          .run();
      } else {
        await db
          .prepare(
            `UPDATE job_runs SET status = ?, updated_at = datetime('now')
             WHERE job_id = ?`,
          )
          .bind(status, jobId)
          .run();
      }
    },

    async get(jobId: string): Promise<JobRun | null> {
      return db
        .prepare("SELECT * FROM job_runs WHERE job_id = ?")
        .bind(jobId)
        .first<JobRun>();
    },

    async list(limit = 50): Promise<JobRun[]> {
      const { results } = await db
        .prepare(
          "SELECT * FROM job_runs ORDER BY created_at DESC LIMIT ?",
        )
        .bind(limit)
        .all<JobRun>();
      return results;
    },

    async stats(): Promise<{
      total: number;
      dispatched: number;
      in_progress: number;
      completed: number;
      failed: number;
    }> {
      const row = await db
        .prepare(
          `SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'dispatched' THEN 1 ELSE 0 END) as dispatched,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status IN ('failure', 'cancelled') THEN 1 ELSE 0 END) as failed
           FROM job_runs
           WHERE created_at >= datetime('now', '-7 days')`,
        )
        .first<Record<string, number>>();
      return {
        total: row?.total ?? 0,
        dispatched: row?.dispatched ?? 0,
        in_progress: row?.in_progress ?? 0,
        completed: row?.completed ?? 0,
        failed: row?.failed ?? 0,
      };
    },
  };
}

export type JobRunsRepo = ReturnType<typeof createJobRunsRepo>;
```

- [ ] **Step 3: Write tests for both services**

Create `server/__tests__/services.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { createJobRunsRepo } from "../services/job-runs";

describe("JobRunsRepo", () => {
  beforeAll(async () => {
    const repo = createJobRunsRepo(env.OPERATIONS_DB);
    await repo.ensureTable();
  });

  it("creates a job run and retrieves it", async () => {
    const repo = createJobRunsRepo(env.OPERATIONS_DB);
    const job = await repo.create("daily", "DailyIngestion.yml", {
      dry_run: "false",
    });
    expect(job.job_id).toMatch(/^daily-/);
    expect(job.workflow).toBe("DailyIngestion.yml");
    expect(job.status).toBe("dispatched");

    const fetched = await repo.get(job.job_id);
    expect(fetched).not.toBeNull();
    expect(fetched!.job_id).toBe(job.job_id);
  });

  it("updates job status", async () => {
    const repo = createJobRunsRepo(env.OPERATIONS_DB);
    const job = await repo.create("adhoc", "AdHocIngestion.yml", {
      url: "https://javdb.com/actors/test",
    });
    await repo.updateStatus(job.job_id, "in_progress", 12345);
    const updated = await repo.get(job.job_id);
    expect(updated!.status).toBe("in_progress");
    expect(updated!.gh_run_id).toBe(12345);
  });

  it("lists jobs ordered by created_at desc", async () => {
    const repo = createJobRunsRepo(env.OPERATIONS_DB);
    const jobs = await repo.list(10);
    expect(jobs.length).toBeGreaterThan(0);
    // Most recent first
    if (jobs.length > 1) {
      expect(jobs[0].created_at >= jobs[1].created_at).toBe(true);
    }
  });

  it("returns stats for last 7 days", async () => {
    const repo = createJobRunsRepo(env.OPERATIONS_DB);
    const stats = await repo.stats();
    expect(stats).toHaveProperty("total");
    expect(stats).toHaveProperty("dispatched");
    expect(stats).toHaveProperty("in_progress");
    expect(stats).toHaveProperty("completed");
    expect(stats).toHaveProperty("failed");
  });
});
```

- [ ] **Step 4: Run tests to verify**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/services.test.ts`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/services/gh-client.ts server/services/job-runs.ts server/__tests__/services.test.ts
git commit -m "feat(api): add GH client and job-runs services for Phase 3"
```

---

### Task 2: Tasks Routes + Tests

**Files:**
- Create: `server/routes/tasks.ts`
- Create: `server/__tests__/tasks-routes.test.ts`
- Modify: `server/app.ts` (mount tasksRoutes)
- Modify: `vitest.server.config.ts` (add GH_ACTIONS_TIER, GH_ACTIONS_REPO bindings)

**Context:** The tasks routes allow the frontend to dispatch daily/adhoc spider runs and monitor their status. In Cloudflare mode, task dispatch means calling GitHub Actions `workflow_dispatch` API. The Python backend's `POST /api/tasks/daily` spawns a subprocess; the TS backend dispatches `DailyIngestion.yml`. The Python backend's `POST /api/tasks/adhoc` spawns `apps.cli.spider --url ...`; the TS backend dispatches `AdHocIngestion.yml` with the URL input. 

**Key mapping:**
- `POST /api/tasks/daily` → dispatches `DailyIngestion.yml`
- `POST /api/tasks/adhoc` → dispatches `AdHocIngestion.yml` (requires `url` in body)
- `GET /api/tasks` → lists jobs from `job_runs` table
- `GET /api/tasks/stats` → aggregate stats from `job_runs` table
- `GET /api/tasks/{job_id}` → get single job detail (with GH run status if available)
- `GET /api/tasks/{job_id}/logs` → returns GH Actions logs URL (not streaming)

**Auth:** All routes require auth. POST routes require admin role + CSRF. If `GH_ACTIONS_TIER` is `none` or `GH_ACTIONS_TOKEN` is not set, dispatch endpoints return 503.

**CSRF pattern:** POST/PUT/DELETE routes need `X-CSRF-Token` header matching `csrf_token` cookie. See `server/middleware/auth.ts`. Test helpers use `getCsrf()` which returns `{ token, csrfToken, csrfCookie }`.

**Tests:** Since there is no real GH_ACTIONS_TOKEN in the test environment, dispatch POST endpoints will return 503 (service unavailable). Tests verify this expected error path. GET endpoints work against the D1 `job_runs` table directly. Seed the table with test data in `beforeAll`.

- [ ] **Step 1: Update `vitest.server.config.ts`**

Add `GH_ACTIONS_TIER` and `GH_ACTIONS_REPO` to the `bindings` object:

```typescript
bindings: {
  API_SECRET_KEY: "test-secret-key-at-least-32-chars-long",
  ADMIN_USERNAME: "admin",
  ADMIN_PASSWORD_HASH: "plain:testpassword123",
  ENVIRONMENT: "test",
  GH_ACTIONS_TIER: "admin",
  GH_ACTIONS_REPO: "test-owner/test-repo",
},
```

Note: `GH_ACTIONS_TOKEN` is intentionally NOT set so dispatch tests verify the "no token" 503 path.

- [ ] **Step 2: Create `server/routes/tasks.ts`**

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { createGhClient } from "../services/gh-client";
import { createJobRunsRepo } from "../services/job-runs";

type TasksEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const tasksRoutes = new Hono<TasksEnv>();

function requireGhActions(env: Env): void {
  const tier = env.GH_ACTIONS_TIER ?? "none";
  if (tier === "none" || !env.GH_ACTIONS_TOKEN || !env.GH_ACTIONS_REPO) {
    throw new HTTPException(503, {
      message: JSON.stringify({
        error: { code: "gh_actions.unavailable", message: "GitHub Actions not configured" },
      }),
    });
  }
}

// POST /api/tasks/daily — dispatch DailyIngestion workflow
tasksRoutes.post("/daily", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  requireGhActions(c.env);

  const body = await c.req.json<{
    dry_run?: boolean;
    disable_all_filters?: boolean;
  }>().catch(() => ({}));

  const inputs: Record<string, string> = {};
  if (body.dry_run) inputs.dry_run = "true";
  if (body.disable_all_filters) inputs.disable_all_filters = "true";

  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.create("daily", "DailyIngestion.yml", inputs);

  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  await gh.dispatchWorkflow("DailyIngestion.yml", inputs);

  return c.json({ job_id: job.job_id, status: "dispatched", created_at: job.created_at }, 201);
});

// POST /api/tasks/adhoc — dispatch AdHocIngestion workflow
tasksRoutes.post("/adhoc", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  requireGhActions(c.env);

  const body = await c.req.json<{
    url: string;
    start_page?: number;
    end_page?: number;
    dry_run?: boolean;
    disable_all_filters?: boolean;
  }>();

  if (!body.url) {
    throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "validation", message: "url is required" } }),
    });
  }

  const inputs: Record<string, string> = { url: body.url };
  if (body.start_page !== undefined) inputs.start_page = String(body.start_page);
  if (body.end_page !== undefined) inputs.end_page = String(body.end_page);
  if (body.dry_run) inputs.dry_run = "true";
  if (body.disable_all_filters) inputs.disable_all_filters = "true";

  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.create("adhoc", "AdHocIngestion.yml", inputs);

  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  await gh.dispatchWorkflow("AdHocIngestion.yml", inputs);

  return c.json({ job_id: job.job_id, status: "dispatched", created_at: job.created_at }, 201);
});

// GET /api/tasks — list recent jobs
tasksRoutes.get("/", async (c) => {
  const limit = Math.max(1, Math.min(200, parseInt(c.req.query("limit") ?? "50", 10) || 50));
  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const jobs = await repo.list(limit);
  return c.json({ items: jobs });
});

// GET /api/tasks/stats — job statistics
tasksRoutes.get("/stats", async (c) => {
  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const stats = await repo.stats();
  return c.json(stats);
});

// GET /api/tasks/:job_id — single job detail
tasksRoutes.get("/:job_id", async (c) => {
  const jobId = c.req.param("job_id");
  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.get(jobId);
  if (!job) {
    throw new HTTPException(404, {
      message: JSON.stringify({ error: { code: "job.not_found" } }),
    });
  }
  return c.json(job);
});

// GET /api/tasks/:job_id/logs — redirect to GH Actions logs
tasksRoutes.get("/:job_id/logs", async (c) => {
  const jobId = c.req.param("job_id");
  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.get(jobId);
  if (!job) {
    throw new HTTPException(404, {
      message: JSON.stringify({ error: { code: "job.not_found" } }),
    });
  }
  if (!job.gh_run_id) {
    return c.json({ logs_url: null, message: "No GitHub run linked yet" });
  }

  requireGhActions(c.env);
  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  const logsUrl = await gh.getRunLogsUrl(job.gh_run_id);
  return c.json({ logs_url: logsUrl });
});
```

- [ ] **Step 3: Mount in `server/app.ts`**

Add import and route:

```typescript
import { tasksRoutes } from "./routes/tasks";
// ... after other protected routes
app.route("/api/tasks", tasksRoutes);
```

- [ ] **Step 4: Create `server/__tests__/tasks-routes.test.ts`**

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{
  token: string;
  csrfToken: string;
  csrfCookie: string;
}> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return {
    token: data.access_token,
    csrfToken: data.csrf_token,
    csrfCookie: `csrf_token=${data.csrf_token}`,
  };
}

async function seedJobRuns(db: D1Database) {
  await db
    .prepare(
      `CREATE TABLE IF NOT EXISTS job_runs (
        job_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, gh_run_id INTEGER,
        status TEXT NOT NULL DEFAULT 'dispatched', inputs TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      )`,
    )
    .run();
  await db
    .prepare("INSERT INTO job_runs (job_id, workflow, status, inputs) VALUES (?, ?, ?, ?)")
    .bind("daily-20260524-100000-abcd", "DailyIngestion.yml", "completed", '{"dry_run":"false"}')
    .run();
  await db
    .prepare(
      "INSERT INTO job_runs (job_id, workflow, status, gh_run_id, inputs) VALUES (?, ?, ?, ?, ?)",
    )
    .bind("adhoc-20260524-110000-ef01", "AdHocIngestion.yml", "in_progress", 99999, '{"url":"https://javdb.com/actors/test"}')
    .run();
}

describe("Tasks routes", () => {
  beforeAll(async () => {
    await seedJobRuns(env.OPERATIONS_DB);
  });

  it("GET /api/tasks lists jobs", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/tasks",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.items).toBeInstanceOf(Array);
    expect(data.items.length).toBeGreaterThanOrEqual(2);
  });

  it("GET /api/tasks/stats returns stats", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/tasks/stats",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data).toHaveProperty("total");
    expect(data).toHaveProperty("completed");
  });

  it("GET /api/tasks/:job_id returns single job", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/tasks/daily-20260524-100000-abcd",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.job_id).toBe("daily-20260524-100000-abcd");
    expect(data.status).toBe("completed");
  });

  it("GET /api/tasks/:job_id returns 404 for unknown job", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/tasks/nonexistent-job",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(404);
  });

  it("POST /api/tasks/daily returns 503 when GH_ACTIONS_TOKEN not set", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/tasks/daily",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({}),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("POST /api/tasks/adhoc returns 503 when GH_ACTIONS_TOKEN not set", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/tasks/adhoc",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ url: "https://javdb.com/actors/test" }),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("GET /api/tasks/:job_id/logs returns logs info", async () => {
    const token = await getToken();
    // Job without gh_run_id linked
    const res = await app.request(
      "/api/tasks/daily-20260524-100000-abcd/logs",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.logs_url).toBeNull();
  });
});
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/tasks-routes.test.ts`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/tasks.ts server/__tests__/tasks-routes.test.ts server/app.ts vitest.server.config.ts
git commit -m "feat(api): add tasks routes — daily/adhoc dispatch via GH Actions"
```

---

### Task 3: GH Actions Routes + Tests

**Files:**
- Create: `server/routes/gh-actions.ts`
- Create: `server/__tests__/gh-actions-routes.test.ts`
- Modify: `server/app.ts` (mount ghActionsRoutes)

**Context:** These routes expose the GitHub Actions API to the frontend for monitoring workflows, viewing runs, dispatching workflows, and downloading logs. Access is controlled by `GH_ACTIONS_TIER`. Since tests don't have a real GH_ACTIONS_TOKEN, most endpoints will return 503 — tests verify the tier-check and 503 error paths. Only the tier check itself can be tested with mock env.

**Tier access:**
- `none` → all endpoints return 503
- `monitor` → GET workflows, GET runs, GET runs/{id}/logs
- `edit` → monitor + GET/PUT workflows/{name}
- `admin` → edit + POST runs (dispatch)

Note: Secrets endpoints (GET/POST/DELETE /api/gh-actions/secrets) are deferred per DD6.

- [ ] **Step 1: Create `server/routes/gh-actions.ts`**

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { createGhClient } from "../services/gh-client";

type GhEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const ghActionsRoutes = new Hono<GhEnv>();

type Tier = "none" | "monitor" | "edit" | "admin";

const TIER_LEVELS: Record<Tier, number> = {
  none: 0,
  monitor: 1,
  edit: 2,
  admin: 3,
};

function checkTier(env: Env, required: Tier): void {
  const current = (env.GH_ACTIONS_TIER ?? "none") as Tier;
  if (
    TIER_LEVELS[current] < TIER_LEVELS[required] ||
    !env.GH_ACTIONS_TOKEN ||
    !env.GH_ACTIONS_REPO
  ) {
    throw new HTTPException(503, {
      message: JSON.stringify({
        error: {
          code: "gh_actions.unavailable",
          message: `Requires GH Actions tier '${required}' or higher`,
        },
      }),
    });
  }
}

function getClient(env: Env) {
  return createGhClient({
    token: env.GH_ACTIONS_TOKEN!,
    repo: env.GH_ACTIONS_REPO!,
  });
}

// GET /api/gh-actions/workflows
ghActionsRoutes.get("/workflows", async (c) => {
  checkTier(c.env, "monitor");
  const gh = getClient(c.env);
  const data = await gh.listWorkflows();
  return c.json(data);
});

// GET /api/gh-actions/runs
ghActionsRoutes.get("/runs", async (c) => {
  checkTier(c.env, "monitor");
  const workflowId = c.req.query("workflow_id");
  const perPage = parseInt(c.req.query("per_page") ?? "30", 10) || 30;
  const page = parseInt(c.req.query("page") ?? "1", 10) || 1;

  const gh = getClient(c.env);
  const data = await gh.listRuns({
    workflow_id: workflowId ? parseInt(workflowId, 10) : undefined,
    per_page: Math.min(perPage, 100),
    page,
  });
  return c.json(data);
});

// POST /api/gh-actions/runs — dispatch a workflow
ghActionsRoutes.post("/runs", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  checkTier(c.env, "admin");

  const body = await c.req.json<{
    workflow: string;
    inputs?: Record<string, string>;
    ref?: string;
  }>();

  if (!body.workflow) {
    throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "validation", message: "workflow is required" } }),
    });
  }

  const gh = getClient(c.env);
  await gh.dispatchWorkflow(body.workflow, body.inputs ?? {}, body.ref ?? "main");
  return c.json({ dispatched: true, workflow: body.workflow }, 201);
});

// GET /api/gh-actions/runs/:run_id/logs
ghActionsRoutes.get("/runs/:run_id/logs", async (c) => {
  checkTier(c.env, "monitor");
  const runId = parseInt(c.req.param("run_id"), 10);
  if (isNaN(runId)) {
    throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "validation", message: "Invalid run_id" } }),
    });
  }

  const gh = getClient(c.env);
  const logsUrl = await gh.getRunLogsUrl(runId);
  return c.json({ logs_url: logsUrl });
});

// GET /api/gh-actions/workflows/:name — get workflow YAML
ghActionsRoutes.get("/workflows/:name", async (c) => {
  checkTier(c.env, "edit");
  const name = c.req.param("name");
  const gh = getClient(c.env);
  const data = await gh.getWorkflowContent(name);
  return c.json(data);
});

// PUT /api/gh-actions/workflows/:name — update workflow YAML
ghActionsRoutes.put("/workflows/:name", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  checkTier(c.env, "admin");

  const name = c.req.param("name");
  const body = await c.req.json<{ content: string; sha: string; message?: string }>();

  if (!body.content || !body.sha) {
    throw new HTTPException(400, {
      message: JSON.stringify({
        error: { code: "validation", message: "content and sha are required" },
      }),
    });
  }

  const gh = getClient(c.env);
  await gh.updateWorkflowContent(
    name,
    body.content,
    body.sha,
    body.message ?? `Update ${name} via dashboard`,
  );
  return c.json({ updated: true });
});
```

- [ ] **Step 2: Mount in `server/app.ts`**

Add import and route:

```typescript
import { ghActionsRoutes } from "./routes/gh-actions";
// ... after other protected routes
app.route("/api/gh-actions", ghActionsRoutes);
```

- [ ] **Step 3: Create `server/__tests__/gh-actions-routes.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{
  token: string;
  csrfToken: string;
  csrfCookie: string;
}> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return {
    token: data.access_token,
    csrfToken: data.csrf_token,
    csrfCookie: `csrf_token=${data.csrf_token}`,
  };
}

describe("GH Actions routes", () => {
  // GH_ACTIONS_TIER is "admin" and GH_ACTIONS_REPO is set in test config,
  // but GH_ACTIONS_TOKEN is NOT set — so all endpoints that need the token
  // should return 503.

  it("GET /api/gh-actions/workflows returns 503 without token", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/gh-actions/workflows",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("GET /api/gh-actions/runs returns 503 without token", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/gh-actions/runs",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("POST /api/gh-actions/runs returns 503 without token", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/gh-actions/runs",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ workflow: "DailyIngestion.yml" }),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("GET /api/gh-actions/runs/:run_id/logs returns 503 without token", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/gh-actions/runs/12345/logs",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("GET /api/gh-actions/workflows/:name returns 503 without token", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/gh-actions/workflows/DailyIngestion.yml",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("PUT /api/gh-actions/workflows/:name returns 503 without token", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/gh-actions/workflows/DailyIngestion.yml",
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ content: "name: test", sha: "abc123" }),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("rejects unauthenticated requests", async () => {
    const res = await app.request("/api/gh-actions/workflows", {}, env);
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/gh-actions-routes.test.ts`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routes/gh-actions.ts server/__tests__/gh-actions-routes.test.ts server/app.ts
git commit -m "feat(api): add gh-actions routes — workflows, runs, dispatch, logs"
```

---

### Task 4: Operations Routes + Tests

**Files:**
- Create: `server/routes/operations.ts`
- Create: `server/__tests__/operations-routes.test.ts`
- Modify: `server/app.ts` (mount operationsRoutes)

**Context:** Operations routes cover rclone, qB file filter, cleanup, PikPak, and email. Most of these require direct access to external services (qBittorrent API, SMTP, PikPak API) that Workers cannot reach. Strategy:

- **Dispatch via GH Actions:** qB filter-small → `QBFileFilter.yml`, rclone run → `RcloneManager.yml`, cleanup stale-sessions → `StaleSessionCleanup.yml`
- **D1 query:** rclone last (RcloneInventory + DedupRecords from OPERATIONS_DB)
- **501 stubs:** qB torrents, PikPak queue/transfer, email test/history/resend, cleanup claim-stages

**Auth:** All routes require auth. POST routes require admin role + CSRF. GH dispatch routes also need `GH_ACTIONS_TOKEN`.

- [ ] **Step 1: Create `server/routes/operations.ts`**

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { createGhClient } from "../services/gh-client";
import { createJobRunsRepo } from "../services/job-runs";

type OpsEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const operationsRoutes = new Hono<OpsEnv>();

function requireGhActions(env: Env): void {
  const tier = env.GH_ACTIONS_TIER ?? "none";
  if (tier === "none" || !env.GH_ACTIONS_TOKEN || !env.GH_ACTIONS_REPO) {
    throw new HTTPException(503, {
      message: JSON.stringify({
        error: { code: "gh_actions.unavailable", message: "GitHub Actions not configured" },
      }),
    });
  }
}

function notAvailable(c: any, feature: string) {
  return c.json(
    {
      error: {
        code: "not_available",
        message: `${feature} is not available in Cloudflare mode`,
      },
    },
    501,
  );
}

// ── qBittorrent ──

operationsRoutes.get("/qb/torrents", async (c) => {
  return notAvailable(c, "qBittorrent torrent list");
});

operationsRoutes.post("/qb/filter-small", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  requireGhActions(c.env);

  const body = await c.req.json<{
    min_size_mb?: number;
    days?: number;
    dry_run?: boolean;
  }>().catch(() => ({}));

  const inputs: Record<string, string> = {};
  if (body.min_size_mb !== undefined) inputs.min_size_mb = String(body.min_size_mb);
  if (body.days !== undefined) inputs.days = String(body.days);
  if (body.dry_run) inputs.dry_run = "true";

  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.create("qb-filter", "QBFileFilter.yml", inputs);

  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  await gh.dispatchWorkflow("QBFileFilter.yml", inputs);

  return c.json({ job_id: job.job_id, status: "dispatched", created_at: job.created_at }, 201);
});

// ── PikPak ──

operationsRoutes.get("/pikpak/queue", async (c) => {
  return notAvailable(c, "PikPak queue");
});

operationsRoutes.post("/pikpak/transfer", async (c) => {
  return notAvailable(c, "PikPak transfer");
});

// ── Email ──

operationsRoutes.post("/email/test", async (c) => {
  return notAvailable(c, "Email test");
});

operationsRoutes.get("/email/history", async (c) => {
  return notAvailable(c, "Email history");
});

operationsRoutes.post("/email/:id/resend", async (c) => {
  return notAvailable(c, "Email resend");
});

// ── Rclone ──

operationsRoutes.get("/rclone/last", async (c) => {
  const inventoryCount = await c.env.OPERATIONS_DB.prepare(
    "SELECT COUNT(*) as count FROM RcloneInventory",
  )
    .first<{ count: number }>()
    .catch(() => ({ count: 0 }));

  const dedupCount = await c.env.OPERATIONS_DB.prepare(
    "SELECT COUNT(*) as count FROM DedupRecords",
  )
    .first<{ count: number }>()
    .catch(() => ({ count: 0 }));

  const lastDedup = await c.env.OPERATIONS_DB.prepare(
    "SELECT CreatedAt FROM DedupRecords ORDER BY CreatedAt DESC LIMIT 1",
  )
    .first<{ CreatedAt: string }>()
    .catch(() => null);

  return c.json({
    inventory_count: inventoryCount?.count ?? 0,
    dedup_count: dedupCount?.count ?? 0,
    last_dedup_at: lastDedup?.CreatedAt ?? null,
  });
});

operationsRoutes.post("/rclone/run", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  requireGhActions(c.env);

  const body = await c.req.json<{
    scan?: boolean;
    report?: boolean;
    execute?: boolean;
    root_path?: string;
    dry_run?: boolean;
    incremental?: boolean;
  }>().catch(() => ({}));

  const inputs: Record<string, string> = {};
  if (body.scan) inputs.scan = "true";
  if (body.report) inputs.report = "true";
  if (body.execute) inputs.execute = "true";
  if (body.root_path) inputs.root_path = body.root_path;
  if (body.dry_run) inputs.dry_run = "true";
  if (body.incremental) inputs.incremental = "true";

  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.create("rclone", "RcloneManager.yml", inputs);

  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  await gh.dispatchWorkflow("RcloneManager.yml", inputs);

  return c.json({ job_id: job.job_id, status: "dispatched", created_at: job.created_at }, 201);
});

// ── Cleanup ──

operationsRoutes.post("/cleanup/stale-sessions", async (c) => {
  const user = c.get("user");
  if (user.role !== "admin") throw new HTTPException(403, { message: "Admin required" });
  requireGhActions(c.env);

  const body = await c.req.json<{
    max_age_hours?: number;
    apply?: boolean;
    scope?: string;
  }>().catch(() => ({}));

  const inputs: Record<string, string> = {};
  if (body.max_age_hours !== undefined) inputs.max_age_hours = String(body.max_age_hours);
  if (body.apply) inputs.apply = "true";
  if (body.scope) inputs.scope = body.scope;

  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();
  const job = await repo.create("cleanup", "StaleSessionCleanup.yml", inputs);

  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  await gh.dispatchWorkflow("StaleSessionCleanup.yml", inputs);

  return c.json({ job_id: job.job_id, status: "dispatched", created_at: job.created_at }, 201);
});

operationsRoutes.post("/cleanup/claim-stages", async (c) => {
  return notAvailable(c, "Claim stages cleanup");
});
```

- [ ] **Step 2: Mount in `server/app.ts`**

Add import and route:

```typescript
import { operationsRoutes } from "./routes/operations";
// ... after other protected routes
app.route("/api/ops", operationsRoutes);
```

- [ ] **Step 3: Create `server/__tests__/operations-routes.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{
  token: string;
  csrfToken: string;
  csrfCookie: string;
}> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return {
    token: data.access_token,
    csrfToken: data.csrf_token,
    csrfCookie: `csrf_token=${data.csrf_token}`,
  };
}

describe("Operations routes", () => {
  // ── 501 stubs ──

  it("GET /api/ops/qb/torrents returns 501", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/ops/qb/torrents",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(501);
    const data = (await res.json()) as any;
    expect(data.error.code).toBe("not_available");
  });

  it("GET /api/ops/pikpak/queue returns 501", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/ops/pikpak/queue",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(501);
  });

  it("POST /api/ops/email/test returns 501", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/ops/email/test",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({}),
      },
      env,
    );
    expect(res.status).toBe(501);
  });

  it("GET /api/ops/email/history returns 501", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/ops/email/history",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(501);
  });

  it("POST /api/ops/cleanup/claim-stages returns 501", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/ops/cleanup/claim-stages",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({}),
      },
      env,
    );
    expect(res.status).toBe(501);
  });

  // ── GH Actions dispatch endpoints → 503 without token ──

  it("POST /api/ops/qb/filter-small returns 503 without token", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/ops/qb/filter-small",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({}),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("POST /api/ops/rclone/run returns 503 without token", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/ops/rclone/run",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ scan: true }),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  it("POST /api/ops/cleanup/stale-sessions returns 503 without token", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/ops/cleanup/stale-sessions",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({}),
      },
      env,
    );
    expect(res.status).toBe(503);
  });

  // ── Rclone last (D1 query — works without GH token) ──

  it("GET /api/ops/rclone/last returns counts", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/ops/rclone/last",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data).toHaveProperty("inventory_count");
    expect(data).toHaveProperty("dedup_count");
    expect(data).toHaveProperty("last_dedup_at");
  });

  // ── Auth checks ──

  it("rejects unauthenticated requests", async () => {
    const res = await app.request("/api/ops/qb/torrents", {}, env);
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/operations-routes.test.ts`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routes/operations.ts server/__tests__/operations-routes.test.ts server/app.ts
git commit -m "feat(api): add operations routes — rclone/qB/cleanup dispatch + stubs"
```

---

### Task 5: Full Test Suite + Final Verification

**Files:**
- No new files
- May fix any issues found during full suite run

**Context:** Run the entire test suite to verify that all Phase 3 routes integrate correctly with Phase 1 and 2 routes. Check for import errors, route conflicts, and test isolation issues.

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts`

Expected: All tests PASS (Phase 1 + Phase 2 + Phase 3 tests).

- [ ] **Step 2: Verify route listing**

Quick manual check — ensure all routes are mounted correctly by checking `server/app.ts` imports:

```
auth, capabilities, system-state, history, sessions,
config, onboarding, diagnostics, explore,
tasks, gh-actions, operations
```

That should be 12 route modules total.

- [ ] **Step 3: Fix any issues found**

If tests fail, fix the issues. Common issues:
- Table name mismatches between test seed and production code
- CSRF token handling on POST routes
- D1 table not created before query (use `ensureTable()` or seed in test)

- [ ] **Step 4: Commit if fixes were needed**

```bash
git add -A
git commit -m "fix(api): resolve Phase 3 integration issues"
```

---

### Task 6: Deploy + Smoke Test

**Files:**
- No file changes

**Context:** Deploy the updated Worker to Cloudflare and run manual smoke tests against the production URL.

- [ ] **Step 1: Build**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run build`

Expected: Build succeeds without errors.

- [ ] **Step 2: Deploy**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx wrangler deploy`

Expected: Deploy succeeds. Worker URL: `https://javdb-autospider-web.wuengineer.workers.dev`

- [ ] **Step 3: Smoke test — tasks endpoints**

```bash
# Get auth token
TOKEN=$(curl -s -X POST https://javdb-autospider-web.wuengineer.workers.dev/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}' | jq -r .access_token)

# List tasks (should return empty or existing jobs)
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/tasks \
  -H "Authorization: Bearer $TOKEN" | jq .

# Task stats
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/tasks/stats \
  -H "Authorization: Bearer $TOKEN" | jq .
```

- [ ] **Step 4: Smoke test — operations endpoints**

```bash
# Rclone last (D1 query)
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/ops/rclone/last \
  -H "Authorization: Bearer $TOKEN" | jq .

# qB torrents (should return 501)
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/ops/qb/torrents \
  -H "Authorization: Bearer $TOKEN" | jq .
```

- [ ] **Step 5: Smoke test — GH Actions endpoints**

```bash
# Workflows (depends on GH_ACTIONS_TOKEN being set in production)
curl -s https://javdb-autospider-web.wuengineer.workers.dev/api/gh-actions/workflows \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Expected: If `GH_ACTIONS_TOKEN` is configured, returns workflow list. If not, returns 503.
