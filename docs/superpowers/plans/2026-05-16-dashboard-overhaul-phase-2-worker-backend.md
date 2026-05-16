# Dashboard Overhaul — Phase 2: Worker Backend Infrastructure

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build all server-side persistence and write paths that Phase 3 (UI) and Phase 4 (history drill-downs) will consume. No user-visible change after Phase 2 — the dashboard renders identically because the UI still uses the old fetch shape. **Phase 2 is verified entirely via curl + tests.**

**Architecture:** New `MetricsState` Durable Object (`metrics_snapshots` table, 5s-bucket JSON snapshot, idle suppression). Existing DOs extended with history tables: `RunnerRegistry` adds `proxies_seen` + `signals_event_log` + `runners_event_log`; `ConfigState` adds `config_audit_log`; `GlobalLoginState` adds `login_event_log`. Cloudflare Cron Trigger fires every 1 min and writes a snapshot. `/ops/snapshot` writes another snapshot via `ctx.waitUntil`. New `GET /metrics/range`, `GET /signals/history`, `GET /runners/history`, `GET /login/history`, `GET /config/history` endpoints.

**Tech Stack:** TypeScript (Cloudflare Workers + Durable Objects), `vitest-pool-workers`, SQLite (DO-backed), `wrangler.toml` migrations.

**Reference docs:** [ADR-002](../../ai/adr/ADR-002-observability-data-storage-topology.md), [ADR-003](../../ai/adr/ADR-003-metrics-pipeline.md), [ADR-004](../../ai/adr/ADR-004-proxy-discovery-via-runner-pool-upload.md), [CONTEXT.md](../../../CONTEXT.md)

**Prerequisite:** Phase 1 deployed to autospider (runner sends `proxy_pool` on register). Old runners that don't send the field still work — Phase 2 will simply not populate `proxies_seen` from them.

---

## File Structure

**New files:**
- `JAVDB_AutoSpider_Proxycoordinator/src/metrics_state.ts` — new `MetricsState` DO class
- `JAVDB_AutoSpider_Proxycoordinator/src/event_log_helpers.ts` — shared `pruneLogTable()` helper used by every history-bearing DO
- `JAVDB_AutoSpider_Proxycoordinator/test/metrics_state.test.ts`
- `JAVDB_AutoSpider_Proxycoordinator/test/proxies_seen.test.ts`
- `JAVDB_AutoSpider_Proxycoordinator/test/signals_event_log.test.ts`
- `JAVDB_AutoSpider_Proxycoordinator/test/runners_event_log.test.ts`
- `JAVDB_AutoSpider_Proxycoordinator/test/login_event_log.test.ts`
- `JAVDB_AutoSpider_Proxycoordinator/test/config_audit_log.test.ts`
- `JAVDB_AutoSpider_Proxycoordinator/test/scheduled_handler.test.ts`

**Modified files:**
- `JAVDB_AutoSpider_Proxycoordinator/wrangler.toml` — add `METRICS_STATE_DO` binding + `[migrations] tag = "v6"` + cron trigger
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` — register new DO export, wire `scheduled` handler, write to MetricsState in `/ops/snapshot`, route `GET /metrics/range` + 4 history endpoints, auto-fetch `proxies_seen` when query is empty
- `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts` — add `proxies_seen`, `signals_event_log`, `runners_event_log` schemas; write events at lifecycle points; expose read APIs
- `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts` — add `config_audit_log` schema; write on PATCH; expose read API
- `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts` — add `login_event_log` schema; write on attempt/publish/invalidate/lease; expose read API
- `JAVDB_AutoSpider_Proxycoordinator/src/types.ts` — add `RegisterRunnerRequest.proxy_pool` field, history payload types

---

## Task 1: `wrangler.toml` — declare `MetricsState` DO + migration v6 + cron trigger

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/wrangler.toml`

- [ ] **Step 1: Add the new DO binding**

After the existing `WORK_DISTRIBUTOR_DO` binding (around line 56-58), append:

```toml
# W5.7 / ADR-003 — singleton MetricsState DO. Stores periodic
# JSON snapshots of the full /ops/snapshot payload for dashboard
# time-series charts and history drill-downs. Addressed by
# `idFromName("global-metrics")`. See src/metrics_state.ts.
[[durable_objects.bindings]]
name = "METRICS_STATE_DO"
class_name = "MetricsState"
```

- [ ] **Step 2: Add the migration v6**

After the existing `[[migrations]] tag = "v5"` block (around line 90-92), append:

```toml
# v6 — W5.7 / ADR-003 MetricsState singleton. Stores time-series
# JSON snapshots of /ops/snapshot for dashboard charts and history.
[[migrations]]
tag = "v6"
new_sqlite_classes = ["MetricsState"]
```

- [ ] **Step 3: Add the cron trigger**

After the `[dev]` block, append:

```toml
# W5.7 / ADR-003 — every 1 min, the scheduled handler pulls the
# current /ops/snapshot payload and writes it to MetricsState DO
# (subject to idle suppression). 1 min is the minimum interval
# Cloudflare Cron supports; dashboard 5s polling provides finer
# resolution when an operator is actively viewing.
[triggers]
crons = ["* * * * *"]
```

- [ ] **Step 4: Add Phase 2 tuning vars**

After `WORKER_RATE_LIMIT_PER_MIN = "1000"` in the `[vars]` block, append:

```toml
# W5.7 / ADR-003 — MetricsState retention. Snapshots older than this
# are purged on every GC alarm. Default 30 days. Set to "0" to disable
# retention sweep entirely (debug only).
METRICS_RETENTION_DAYS = "30"
# Hard cap on row count regardless of age. Defence against
# misconfigured idle suppression. Default 100k.
METRICS_MAX_ROWS = "100000"

# W5.7 / ADR-002 — event-log retention per kind.
SIGNALS_EVENT_LOG_RETENTION_DAYS = "90"
RUNNERS_EVENT_LOG_RETENTION_DAYS = "90"
LOGIN_EVENT_LOG_RETENTION_DAYS = "30"
CONFIG_AUDIT_LOG_RETENTION_DAYS = "365"
```

- [ ] **Step 5: Verify wrangler config parses**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler deploy --dry-run --outdir /tmp/wrangler-check 2>&1 | tail -10`
Expected: No fatal config-parse errors. (Build errors from missing `MetricsState` class are expected and fine — we add the class next.)

- [ ] **Step 6: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add wrangler.toml
git commit -m "$(cat <<'EOF'
chore(worker): declare MetricsState DO + migration v6 + cron trigger (Phase 2, ADR-003)
EOF
)"
```

---

## Task 2: Shared `pruneLogTable()` helper for retention sweeps

**Files:**
- Create: `JAVDB_AutoSpider_Proxycoordinator/src/event_log_helpers.ts`
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/event_log_helpers.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/event_log_helpers.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import { pruneLogTable } from "../src/event_log_helpers";

describe("pruneLogTable", () => {
  // We exercise pruneLogTable via the existing GlobalLoginState DO's storage
  // sql binding to avoid building a throwaway DO just for this helper.
  it("deletes rows older than retentionMs", async () => {
    const id = env.GLOBAL_LOGIN_STATE_DO.idFromName("prune-test-1");
    const stub = env.GLOBAL_LOGIN_STATE_DO.get(id);

    await runInDurableObject(stub, async (_instance, state) => {
      const sql = state.storage.sql;
      sql.exec(
        "CREATE TABLE IF NOT EXISTS test_log (ts INTEGER PRIMARY KEY, msg TEXT)",
      );
      sql.exec("DELETE FROM test_log");
      sql.exec("INSERT INTO test_log VALUES (1000, 'old')");
      sql.exec("INSERT INTO test_log VALUES (5000, 'new')");

      // Retention = 2000ms. Rows where ts < now - 2000 = 5000 - 2000 = 3000
      // should be deleted. So row at ts=1000 is dropped, row at ts=5000 stays.
      pruneLogTable(sql, "test_log", 2000, 100, 5000);

      const remaining = Array.from(sql.exec<{ ts: number }>("SELECT ts FROM test_log ORDER BY ts"));
      expect(remaining.map((r) => r.ts)).toEqual([5000]);
    });
  });

  it("enforces maxRows hard cap by dropping oldest", async () => {
    const id = env.GLOBAL_LOGIN_STATE_DO.idFromName("prune-test-2");
    const stub = env.GLOBAL_LOGIN_STATE_DO.get(id);

    await runInDurableObject(stub, async (_instance, state) => {
      const sql = state.storage.sql;
      sql.exec("CREATE TABLE IF NOT EXISTS test_log (ts INTEGER PRIMARY KEY, msg TEXT)");
      sql.exec("DELETE FROM test_log");
      for (let i = 1; i <= 10; i++) {
        sql.exec("INSERT INTO test_log VALUES (?, ?)", i * 1000, `r${i}`);
      }

      // No age-based prune (retention very large); cap = 3 rows. Should
      // keep the 3 newest (ts=8000, 9000, 10000).
      pruneLogTable(sql, "test_log", 100_000_000, 3, 10_000);

      const remaining = Array.from(sql.exec<{ ts: number }>("SELECT ts FROM test_log ORDER BY ts"));
      expect(remaining.map((r) => r.ts)).toEqual([8000, 9000, 10000]);
    });
  });

  it("retentionMs=0 disables age-based sweep", async () => {
    const id = env.GLOBAL_LOGIN_STATE_DO.idFromName("prune-test-3");
    const stub = env.GLOBAL_LOGIN_STATE_DO.get(id);

    await runInDurableObject(stub, async (_instance, state) => {
      const sql = state.storage.sql;
      sql.exec("CREATE TABLE IF NOT EXISTS test_log (ts INTEGER PRIMARY KEY, msg TEXT)");
      sql.exec("DELETE FROM test_log");
      sql.exec("INSERT INTO test_log VALUES (1, 'ancient')");

      pruneLogTable(sql, "test_log", 0, 100, 1_000_000_000);

      const remaining = Array.from(sql.exec<{ ts: number }>("SELECT ts FROM test_log"));
      expect(remaining).toHaveLength(1);
    });
  });
});
```

- [ ] **Step 2: Verify test fails (helper does not exist)**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/event_log_helpers.test.ts --run`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the helper**

Create `JAVDB_AutoSpider_Proxycoordinator/src/event_log_helpers.ts`:

```typescript
/**
 * Phase 2 / ADR-002 — shared retention sweep for the five history tables.
 *
 * Each history-bearing DO calls this helper inside its GC alarm to drop
 * rows older than `retentionMs` and to enforce a hard `maxRows` ceiling
 * (defence-in-depth against unbounded growth if retention is misconfigured).
 *
 * Strategy:
 *   1. Age sweep — DELETE WHERE ts < (now - retentionMs).
 *      Skipped when retentionMs <= 0 (debug / disable mode).
 *   2. Row-count sweep — if remaining rows > maxRows, drop the oldest
 *      (rowcount - maxRows) rows in one DELETE.
 *
 * Pass the wall-clock `now` so tests can control timing without depending
 * on `Date.now()`.
 *
 * The `tableName` argument is interpolated directly into SQL; callers must
 * pass a hard-coded literal, NEVER user input.
 */
export function pruneLogTable(
  sql: SqlStorage,
  tableName: string,
  retentionMs: number,
  maxRows: number,
  nowMs: number,
): void {
  if (retentionMs > 0) {
    const cutoff = nowMs - retentionMs;
    sql.exec(`DELETE FROM ${tableName} WHERE ts < ?`, cutoff);
  }
  if (maxRows > 0) {
    const countRow = sql.exec<{ n: number }>(
      `SELECT COUNT(*) AS n FROM ${tableName}`,
    ).one();
    const excess = countRow.n - maxRows;
    if (excess > 0) {
      sql.exec(
        `DELETE FROM ${tableName} WHERE ts IN (
           SELECT ts FROM ${tableName} ORDER BY ts ASC LIMIT ?
         )`,
        excess,
      );
    }
  }
}
```

- [ ] **Step 4: Verify all 3 tests pass**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/event_log_helpers.test.ts --run`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/event_log_helpers.ts test/event_log_helpers.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): pruneLogTable helper for history-table retention (Phase 2)
EOF
)"
```

---

## Task 3: `MetricsState` DO — schema, write path, idle suppression, range read

**Files:**
- Create: `JAVDB_AutoSpider_Proxycoordinator/src/metrics_state.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` (add `export { MetricsState }`)
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/metrics_state.test.ts`

- [ ] **Step 1: Write the failing tests covering all critical behaviours**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/metrics_state.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import type { MetricsState } from "../src/metrics_state";

const FRESH_ID = (n: number) => `metrics-test-${n}-${Date.now()}-${Math.random()}`;

function freshStub(): DurableObjectStub {
  const id = env.METRICS_STATE_DO.idFromName(FRESH_ID(0));
  return env.METRICS_STATE_DO.get(id);
}

describe("MetricsState", () => {
  describe("recordSnapshot", () => {
    it("writes a row when state is active", async () => {
      const stub = freshStub();
      const activePayload = {
        runners: { active_runners: [{ holder_id: "h1" }] },
        signals: { active_signals: [] },
        proxies: [],
      };
      const r = await stub.fetch("https://do/do/metrics/record", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ts: 10_000,
          payload: activePayload,
          source: "cron",
        }),
      });
      expect(r.status).toBe(200);
      const queryR = await stub.fetch("https://do/do/metrics/range?from=0&to=20000");
      const { rows } = (await queryR.json()) as any;
      expect(rows).toHaveLength(1);
      expect(rows[0].ts).toBe(10_000);
      expect(rows[0].source).toBe("cron");
    });

    it("skips the write when state is idle", async () => {
      const stub = freshStub();
      const idlePayload = {
        runners: { active_runners: [] },
        signals: { active_signals: [] },
        proxies: [],
        work: { queued: 0, in_flight: 0 },
      };
      // First write — there is no "previous" state so idle skip applies.
      const r = await stub.fetch("https://do/do/metrics/record", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ts: 10_000, payload: idlePayload, source: "cron" }),
      });
      const { skipped } = (await r.json()) as any;
      expect(skipped).toBe(true);
      const queryR = await stub.fetch("https://do/do/metrics/range?from=0&to=20000");
      const { rows } = (await queryR.json()) as any;
      expect(rows).toHaveLength(0);
    });

    it("writes a transition marker on active→idle boundary", async () => {
      const stub = freshStub();
      const active = {
        runners: { active_runners: [{ holder_id: "h1" }] },
        signals: { active_signals: [] },
        proxies: [],
      };
      const idle = {
        runners: { active_runners: [] },
        signals: { active_signals: [] },
        proxies: [],
        work: { queued: 0, in_flight: 0 },
      };
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST", body: JSON.stringify({ ts: 10_000, payload: active, source: "cron" }),
        headers: { "content-type": "application/json" },
      });
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST", body: JSON.stringify({ ts: 70_000, payload: idle, source: "cron" }),
        headers: { "content-type": "application/json" },
      });
      const queryR = await stub.fetch("https://do/do/metrics/range?from=0&to=120000");
      const { rows } = (await queryR.json()) as any;
      // First write (active) + transition marker on entering idle.
      expect(rows.map((r: any) => r.ts)).toEqual([10_000, 70_000]);
      expect(rows[1].is_transition_marker).toBe(true);
    });

    it("dedupes writes within the same 5s bucket via INSERT OR REPLACE", async () => {
      const stub = freshStub();
      const active = {
        runners: { active_runners: [{ holder_id: "h1" }] },
        signals: { active_signals: [] },
        proxies: [],
      };
      // Two writes at ts=10_000 and ts=12_500 — same 5s bucket (10_000).
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST", body: JSON.stringify({ ts: 10_000, payload: active, source: "cron" }),
        headers: { "content-type": "application/json" },
      });
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST", body: JSON.stringify({ ts: 12_500, payload: active, source: "dashboard" }),
        headers: { "content-type": "application/json" },
      });
      const queryR = await stub.fetch("https://do/do/metrics/range?from=0&to=20000");
      const { rows } = (await queryR.json()) as any;
      expect(rows).toHaveLength(1);
      expect(rows[0].ts).toBe(10_000); // bucketed
      expect(rows[0].source).toBe("dashboard"); // later write wins
    });

    it("writes an hourly heartbeat anchor even when idle", async () => {
      const stub = freshStub();
      const idle = {
        runners: { active_runners: [] },
        signals: { active_signals: [] },
        proxies: [],
        work: { queued: 0, in_flight: 0 },
      };
      // 12:00:00.000 UTC is the top-of-hour. ts = 3600_000 (some hour boundary).
      const TOP_OF_HOUR = 3600_000;
      const r = await stub.fetch("https://do/do/metrics/record", {
        method: "POST", body: JSON.stringify({ ts: TOP_OF_HOUR, payload: idle, source: "cron" }),
        headers: { "content-type": "application/json" },
      });
      const { skipped } = (await r.json()) as any;
      expect(skipped).toBeFalsy();
      const queryR = await stub.fetch("https://do/do/metrics/range?from=0&to=7200000");
      const { rows } = (await queryR.json()) as any;
      expect(rows).toHaveLength(1);
      expect(rows[0].is_heartbeat_anchor).toBe(true);
    });
  });

  describe("range query", () => {
    it("returns rows within [from, to] in ascending ts order", async () => {
      const stub = freshStub();
      const active = {
        runners: { active_runners: [{ holder_id: "h1" }] },
        signals: { active_signals: [] },
        proxies: [],
      };
      for (const ts of [60_000, 120_000, 180_000]) {
        await stub.fetch("https://do/do/metrics/record", {
          method: "POST",
          body: JSON.stringify({ ts, payload: active, source: "cron" }),
          headers: { "content-type": "application/json" },
        });
      }
      const queryR = await stub.fetch(
        "https://do/do/metrics/range?from=100000&to=150000",
      );
      const { rows } = (await queryR.json()) as any;
      expect(rows.map((r: any) => r.ts)).toEqual([120_000]);
    });
  });

  describe("retention sweep", () => {
    it("drops rows older than METRICS_RETENTION_DAYS on prune", async () => {
      const stub = freshStub();
      const active = {
        runners: { active_runners: [{ holder_id: "h1" }] },
        signals: { active_signals: [] },
        proxies: [],
      };
      // Insert at ts = day -40 and day -10 (relative to "now" = day 0).
      const ONE_DAY_MS = 86_400_000;
      const NOW = ONE_DAY_MS * 100; // pick some future "now"
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST",
        body: JSON.stringify({ ts: NOW - 40 * ONE_DAY_MS, payload: active, source: "cron" }),
        headers: { "content-type": "application/json" },
      });
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST",
        body: JSON.stringify({ ts: NOW - 10 * ONE_DAY_MS, payload: active, source: "cron" }),
        headers: { "content-type": "application/json" },
      });

      // Trigger prune with retention = 30 days, now = NOW.
      const r = await stub.fetch("https://do/do/metrics/prune", {
        method: "POST",
        body: JSON.stringify({ now_ms: NOW, retention_days: 30, max_rows: 1000 }),
        headers: { "content-type": "application/json" },
      });
      expect(r.status).toBe(200);

      const queryR = await stub.fetch(`https://do/do/metrics/range?from=0&to=${NOW}`);
      const { rows } = (await queryR.json()) as any;
      // The -40d row should be gone; -10d row remains.
      expect(rows).toHaveLength(1);
      expect(rows[0].ts).toBe(NOW - 10 * ONE_DAY_MS);
    });
  });
});
```

- [ ] **Step 2: Verify all tests fail (DO does not exist yet)**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/metrics_state.test.ts --run`
Expected: every test fails — `METRICS_STATE_DO` binding undefined.

- [ ] **Step 3: Implement `MetricsState` DO**

Create `JAVDB_AutoSpider_Proxycoordinator/src/metrics_state.ts`:

```typescript
import { pruneLogTable } from "./event_log_helpers";
import { Env } from "./types";

/**
 * Phase 2 / ADR-003 — MetricsState Durable Object.
 *
 * Persists time-series snapshots of the /ops/snapshot payload for
 * dashboard charts and history drill-downs.
 *
 * Schema:
 *   metrics_snapshots(
 *     ts INTEGER PRIMARY KEY,            -- 5s bucket: floor(write_ts_ms / 5000) * 5000
 *     payload TEXT NOT NULL,             -- JSON of /ops/snapshot payload
 *     source TEXT NOT NULL,              -- 'cron' | 'dashboard'
 *     is_transition_marker INTEGER DEFAULT 0,  -- 1 = active→idle boundary write
 *     is_heartbeat_anchor INTEGER DEFAULT 0    -- 1 = top-of-hour anchor (even if idle)
 *   );
 *
 * Idle suppression rules (see ADR-003):
 *   - active state → always write (with is_transition_marker=0)
 *   - active → idle (first idle tick after active): write transition marker, then track idle
 *   - idle → idle (consecutive idle): skip, UNLESS this is the top-of-the-hour (write heartbeat anchor)
 *   - idle → active: write, clear idle tracking
 *
 * 5-second bucket primary key plus INSERT OR REPLACE means cron 1-min and
 * dashboard 5-sec writes naturally deduplicate when they coincide.
 *
 * `last_state` is held in DO storage `kv.put("last_state", ...)` so it
 * survives evictions.
 */

const BUCKET_MS = 5_000;
const HOUR_MS = 3_600_000;

interface RecordRequest {
  ts: number; // wall-clock ms when /ops/snapshot was assembled
  payload: Record<string, unknown>;
  source: "cron" | "dashboard";
}

interface PersistedLastState {
  was_active: boolean;
  last_ts_ms: number; // last write's bucket key
}

function bucketKey(ts: number): number {
  return Math.floor(ts / BUCKET_MS) * BUCKET_MS;
}

function isPayloadActive(payload: Record<string, unknown>): boolean {
  const runners = (payload as any).runners?.active_runners ?? [];
  const signals = (payload as any).signals?.active_signals ?? [];
  const work = (payload as any).work ?? {};
  if (Array.isArray(runners) && runners.length > 0) return false === false ? true : false;
  // Active = at least one of: runners, signals, queued work, in-flight work.
  if (Array.isArray(runners) && runners.length > 0) return true;
  if (Array.isArray(signals) && signals.length > 0) return true;
  if (typeof work.queued === "number" && work.queued > 0) return true;
  if (typeof work.in_flight === "number" && work.in_flight > 0) return true;
  return false;
}

function isHourAnchor(ts: number): boolean {
  return ts % HOUR_MS < BUCKET_MS;
}

export class MetricsState implements DurableObject {
  private storage: DurableObjectStorage;
  private sql: SqlStorage;
  private env: Env;

  constructor(state: DurableObjectState, env: Env) {
    this.storage = state.storage;
    this.sql = state.storage.sql;
    this.env = env;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS metrics_snapshots (
        ts INTEGER PRIMARY KEY,
        payload TEXT NOT NULL,
        source TEXT NOT NULL,
        is_transition_marker INTEGER DEFAULT 0,
        is_heartbeat_anchor INTEGER DEFAULT 0
      );
    `);
    this.sql.exec(`
      CREATE INDEX IF NOT EXISTS idx_metrics_snapshots_source
      ON metrics_snapshots(source, ts);
    `);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/do/metrics/record" && request.method === "POST") {
      return this.handleRecord(await request.json() as RecordRequest);
    }
    if (url.pathname === "/do/metrics/range" && request.method === "GET") {
      const from = parseInt(url.searchParams.get("from") ?? "0", 10);
      const to = parseInt(url.searchParams.get("to") ?? `${Date.now()}`, 10);
      return this.handleRange(from, to);
    }
    if (url.pathname === "/do/metrics/prune" && request.method === "POST") {
      const body = await request.json() as { now_ms?: number; retention_days?: number; max_rows?: number };
      const now = body.now_ms ?? Date.now();
      const retentionDays = body.retention_days ?? parseInt(this.env.METRICS_RETENTION_DAYS ?? "30", 10);
      const maxRows = body.max_rows ?? parseInt(this.env.METRICS_MAX_ROWS ?? "100000", 10);
      pruneLogTable(this.sql, "metrics_snapshots", retentionDays * 86_400_000, maxRows, now);
      return new Response(JSON.stringify({ pruned: true }), {
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("not found", { status: 404 });
  }

  private async handleRecord(req: RecordRequest): Promise<Response> {
    const bucket = bucketKey(req.ts);
    const active = isPayloadActive(req.payload);
    const anchor = isHourAnchor(req.ts);
    const lastState = (await this.storage.get<PersistedLastState>("last_state")) ?? {
      was_active: false,
      last_ts_ms: 0,
    };

    // Idle skip rule (see ADR-003).
    let shouldWrite = false;
    let isTransition = false;
    let isAnchor = false;

    if (active) {
      shouldWrite = true; // always write when active
    } else if (lastState.was_active) {
      shouldWrite = true; // active → idle transition marker
      isTransition = true;
    } else if (anchor) {
      shouldWrite = true; // hourly heartbeat anchor during prolonged idle
      isAnchor = true;
    }
    // else: idle → idle, no anchor → skip

    if (!shouldWrite) {
      return new Response(JSON.stringify({ skipped: true }), {
        headers: { "content-type": "application/json" },
      });
    }

    this.sql.exec(
      `INSERT OR REPLACE INTO metrics_snapshots
       (ts, payload, source, is_transition_marker, is_heartbeat_anchor)
       VALUES (?, ?, ?, ?, ?)`,
      bucket,
      JSON.stringify(req.payload),
      req.source,
      isTransition ? 1 : 0,
      isAnchor ? 1 : 0,
    );

    await this.storage.put<PersistedLastState>("last_state", {
      was_active: active,
      last_ts_ms: bucket,
    });

    return new Response(JSON.stringify({ skipped: false, bucket }), {
      headers: { "content-type": "application/json" },
    });
  }

  private async handleRange(from: number, to: number): Promise<Response> {
    const rows = Array.from(
      this.sql.exec<{
        ts: number;
        payload: string;
        source: string;
        is_transition_marker: number;
        is_heartbeat_anchor: number;
      }>(
        `SELECT ts, payload, source, is_transition_marker, is_heartbeat_anchor
         FROM metrics_snapshots
         WHERE ts >= ? AND ts <= ?
         ORDER BY ts ASC`,
        from,
        to,
      ),
    ).map((r) => ({
      ts: r.ts,
      payload: JSON.parse(r.payload),
      source: r.source,
      is_transition_marker: Boolean(r.is_transition_marker),
      is_heartbeat_anchor: Boolean(r.is_heartbeat_anchor),
    }));
    return new Response(JSON.stringify({ rows }), {
      headers: { "content-type": "application/json" },
    });
  }
}
```

- [ ] **Step 4: Re-export from index.ts**

In `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`, find the existing `export { ... }` block at the top (around line 3-8) and add:

```typescript
export { MetricsState } from "./metrics_state";
```

- [ ] **Step 5: Update `Env` interface to include the new binding**

Find `JAVDB_AutoSpider_Proxycoordinator/src/types.ts` `Env` interface and add (sort alphabetically with existing DO bindings):

```typescript
  METRICS_STATE_DO?: DurableObjectNamespace;
  METRICS_RETENTION_DAYS?: string;
  METRICS_MAX_ROWS?: string;
  SIGNALS_EVENT_LOG_RETENTION_DAYS?: string;
  RUNNERS_EVENT_LOG_RETENTION_DAYS?: string;
  LOGIN_EVENT_LOG_RETENTION_DAYS?: string;
  CONFIG_AUDIT_LOG_RETENTION_DAYS?: string;
```

- [ ] **Step 6: Run all the MetricsState tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/metrics_state.test.ts --run`
Expected: 7 passed (all 7 sub-tests).

> **Note:** There's an intentional dead-code fragment in `isPayloadActive` (the duplicate runners check) added by Step 3 to highlight an easy bug — collapse it before commit:
>
> ```typescript
> function isPayloadActive(payload: Record<string, unknown>): boolean {
>   const runners = (payload as any).runners?.active_runners ?? [];
>   const signals = (payload as any).signals?.active_signals ?? [];
>   const work = (payload as any).work ?? {};
>   if (Array.isArray(runners) && runners.length > 0) return true;
>   if (Array.isArray(signals) && signals.length > 0) return true;
>   if (typeof work.queued === "number" && work.queued > 0) return true;
>   if (typeof work.in_flight === "number" && work.in_flight > 0) return true;
>   return false;
> }
> ```

- [ ] **Step 7: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/metrics_state.ts src/index.ts src/types.ts test/metrics_state.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): MetricsState DO with idle suppression + 5s bucket dedup (Phase 2, ADR-003)

5-second bucket primary key + INSERT OR REPLACE for natural Cron/Dashboard
write dedup. Idle suppression with active->idle transition markers and
hourly heartbeat anchors. retention prune via shared pruneLogTable helper.
EOF
)"
```

---

## Task 4: `RunnerRegistry` DO — add `proxies_seen` table + write on register

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts` (schema, register handler)
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/types.ts` (`RegisterRunnerRequest.proxy_pool`)
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/proxies_seen.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/proxies_seen.test.ts
import { describe, it, expect } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";

describe("proxies_seen table (Phase 2 / ADR-004)", () => {
  function registerStub() {
    const id = env.RUNNER_REGISTRY_DO.idFromName("runners");
    return env.RUNNER_REGISTRY_DO.get(id);
  }

  it("populates proxies_seen from proxy_pool on register", async () => {
    const stub = registerStub();
    await stub.fetch("https://do/do/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        holder_id: "holder-A",
        proxy_pool: [
          { id: "P-A", name: "P-A" },
          { id: "P-B", name: "P-B" },
        ],
      }),
    });

    await runInDurableObject(stub, async (_inst, state) => {
      const rows = Array.from(
        state.storage.sql.exec<{ id: string; name: string }>(
          "SELECT id, name FROM proxies_seen ORDER BY id",
        ),
      );
      expect(rows).toEqual([
        { id: "P-A", name: "P-A" },
        { id: "P-B", name: "P-B" },
      ]);
    });
  });

  it("updates last_seen_ms on repeat register", async () => {
    const stub = registerStub();
    await stub.fetch("https://do/do/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        holder_id: "holder-B",
        proxy_pool: [{ id: "P-Refresh", name: "P-Refresh" }],
      }),
    });
    // Read t1
    let firstSeen: number = 0;
    await runInDurableObject(stub, async (_inst, state) => {
      const row = state.storage.sql.exec<{ last_seen_ms: number }>(
        "SELECT last_seen_ms FROM proxies_seen WHERE id='P-Refresh'",
      ).one();
      firstSeen = row.last_seen_ms;
    });

    // Wait a beat to guarantee a different now.
    await new Promise((r) => setTimeout(r, 10));

    await stub.fetch("https://do/do/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        holder_id: "holder-B",
        proxy_pool: [{ id: "P-Refresh", name: "P-Refresh" }],
      }),
    });

    await runInDurableObject(stub, async (_inst, state) => {
      const row = state.storage.sql.exec<{ last_seen_ms: number; first_seen_ms: number }>(
        "SELECT last_seen_ms, first_seen_ms FROM proxies_seen WHERE id='P-Refresh'",
      ).one();
      expect(row.last_seen_ms).toBeGreaterThan(firstSeen);
      expect(row.first_seen_ms).toBeLessThanOrEqual(firstSeen);
    });
  });

  it("tolerates missing proxy_pool field on register (backward compat)", async () => {
    const stub = registerStub();
    const r = await stub.fetch("https://do/do/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ holder_id: "holder-old" }),
    });
    expect(r.status).toBe(200);
    // proxies_seen should still have proxies from earlier tests
    // (the table is shared across runs of register on the same singleton DO),
    // but the register itself MUST not throw.
  });
});
```

- [ ] **Step 2: Verify the test fails**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/proxies_seen.test.ts --run`
Expected: FAIL — table `proxies_seen` does not exist.

- [ ] **Step 3: Add `proxy_pool` to `RegisterRunnerRequest` type**

In `JAVDB_AutoSpider_Proxycoordinator/src/types.ts`, find the `RegisterRunnerRequest` interface and add:

```typescript
  /** Phase 2 / ADR-004 — full PROXY_POOL summary uploaded by the
   *  Python runner. Workers store this in `proxies_seen` so the
   *  dashboard can enumerate all configured proxies (including idle
   *  backup) without each operator passing ?proxy_ids=... manually.
   *  Items contain ONLY `id` and `name` — no URLs / no credentials. */
  proxy_pool?: Array<{ id: string; name: string }>;
```

- [ ] **Step 4: Add the schema + register-handler logic in `RunnerRegistry`**

In `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`, find the constructor (where SQL DDL lives) and append after the existing `CREATE TABLE` statements:

```typescript
    // Phase 2 / ADR-004 — proxies_seen: Worker-side proxy name register
    // populated from runner register payloads. Dashboard reads this to
    // enumerate all configured proxies (active + idle).
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS proxies_seen (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        first_seen_ms INTEGER NOT NULL,
        last_seen_ms INTEGER NOT NULL
      );
    `);
```

In the register handler (around line 160-219), after `data.runners[holderId] = info;` and before `await this.persistState(data);`, add:

```typescript
    // Phase 2 / ADR-004 — populate proxies_seen from upload
    const pool = (body as any).proxy_pool;
    if (Array.isArray(pool)) {
      for (const entry of pool) {
        if (
          entry &&
          typeof entry.id === "string" &&
          typeof entry.name === "string" &&
          entry.id.length > 0 &&
          entry.name.length > 0
        ) {
          this.sql.exec(
            `INSERT INTO proxies_seen (id, name, first_seen_ms, last_seen_ms)
             VALUES (?, ?, ?, ?)
             ON CONFLICT(id) DO UPDATE SET
               name = excluded.name,
               last_seen_ms = excluded.last_seen_ms`,
            entry.id.slice(0, 256),
            entry.name.slice(0, 256),
            now,
            now,
          );
        }
      }
    }
```

- [ ] **Step 5: Add a read endpoint for `proxies_seen`**

In `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`, find the fetch handler's route dispatch and add a case:

```typescript
        if (url.pathname === "/do/proxies_seen" && request.method === "GET") {
          const rows = Array.from(
            this.sql.exec<{
              id: string;
              name: string;
              first_seen_ms: number;
              last_seen_ms: number;
            }>(
              "SELECT id, name, first_seen_ms, last_seen_ms FROM proxies_seen ORDER BY name",
            ),
          );
          return new Response(JSON.stringify({ proxies: rows }), {
            headers: { "content-type": "application/json" },
          });
        }
        if (url.pathname === "/do/proxies_seen/delete" && request.method === "POST") {
          const body = (await request.json()) as { id?: string };
          if (typeof body.id !== "string" || !body.id) {
            return new Response(JSON.stringify({ error: "missing id" }), { status: 400 });
          }
          this.sql.exec("DELETE FROM proxies_seen WHERE id = ?", body.id);
          return new Response(JSON.stringify({ deleted: true }), {
            headers: { "content-type": "application/json" },
          });
        }
```

- [ ] **Step 6: Verify the proxies_seen tests pass**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/proxies_seen.test.ts --run`
Expected: 3 passed.

- [ ] **Step 7: Run the existing `runner_registry.test.ts` to confirm no regression**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/runner_registry.test.ts --run`
Expected: all pre-existing tests still pass.

- [ ] **Step 8: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/runner_registry.ts src/types.ts test/proxies_seen.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): proxies_seen table + register-time persistence (Phase 2, ADR-004)

Runner's uploaded PROXY_POOL summary is now persisted in RunnerRegistry
DO. Backward compat: missing proxy_pool field on register is a no-op.
GET /do/proxies_seen lists the full pool; POST /do/proxies_seen/delete
removes a specific entry (ops-driven cleanup of decommissioned proxies).
EOF
)"
```

---

## Task 5: `signals_event_log` + `runners_event_log` in `RunnerRegistry` DO

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/signals_event_log.test.ts`, `test/runners_event_log.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `JAVDB_AutoSpider_Proxycoordinator/test/signals_event_log.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";

describe("signals_event_log (Phase 2 / ADR-002)", () => {
  const stub = () => env.RUNNER_REGISTRY_DO.get(env.RUNNER_REGISTRY_DO.idFromName("runners"));

  it("logs a 'create' event when a signal is posted", async () => {
    const r = await stub().fetch("https://do/do/signal", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "throttle_global",
        ttl_ms: 60_000,
        factor: 1.5,
        reason: "test cool-down",
      }),
    });
    expect(r.status).toBe(200);

    await runInDurableObject(stub(), async (_inst, state) => {
      const rows = Array.from(state.storage.sql.exec<{ event_kind: string; signal_kind: string }>(
        "SELECT event_kind, signal_kind FROM signals_event_log WHERE event_kind='create' ORDER BY ts DESC LIMIT 1",
      ));
      expect(rows[0]).toMatchObject({ event_kind: "create", signal_kind: "throttle_global" });
    });
  });

  it("queries via /do/signals/history", async () => {
    await stub().fetch("https://do/do/signal", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind: "pause_all", ttl_ms: 5000, reason: "history-test" }),
    });
    const q = await stub().fetch("https://do/do/signals/history?from=0&to=" + (Date.now() + 1_000_000));
    expect(q.status).toBe(200);
    const { rows } = (await q.json()) as any;
    expect(rows.length).toBeGreaterThan(0);
    const recent = rows.find((r: any) => r.signal_kind === "pause_all" && (r.payload_json ?? "").includes("history-test"));
    expect(recent).toBeDefined();
  });
});
```

Create `JAVDB_AutoSpider_Proxycoordinator/test/runners_event_log.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";

describe("runners_event_log (Phase 2 / ADR-002)", () => {
  const stub = () => env.RUNNER_REGISTRY_DO.get(env.RUNNER_REGISTRY_DO.idFromName("runners"));

  it("logs 'register' and 'unregister' events", async () => {
    await stub().fetch("https://do/do/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        holder_id: "rlog-A",
        workflow_run_id: "run-1",
        workflow_name: "DailyIngestion",
        proxy_pool_hash: "deadbeef",
      }),
    });
    await stub().fetch("https://do/do/unregister", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ holder_id: "rlog-A" }),
    });

    await runInDurableObject(stub(), async (_inst, state) => {
      const rows = Array.from(state.storage.sql.exec<{ event_kind: string; final_status: string | null }>(
        "SELECT event_kind, final_status FROM runners_event_log WHERE holder_id='rlog-A' ORDER BY ts",
      ));
      expect(rows.map(r => r.event_kind)).toEqual(["register", "unregister"]);
      expect(rows[1].final_status).toBe("completed");
    });
  });

  it("queries via /do/runners/history with holder_id filter", async () => {
    await stub().fetch("https://do/do/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        holder_id: "rlog-filter",
        workflow_run_id: "run-2",
        workflow_name: "AdHoc",
      }),
    });
    const q = await stub().fetch(
      "https://do/do/runners/history?from=0&to=" + (Date.now() + 1_000_000) + "&holder_id=rlog-filter",
    );
    const { rows } = (await q.json()) as any;
    expect(rows.length).toBe(1);
    expect(rows[0].holder_id).toBe("rlog-filter");
  });
});
```

- [ ] **Step 2: Verify both test files fail**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/signals_event_log.test.ts test/runners_event_log.test.ts --run`
Expected: all fail — tables don't exist yet.

- [ ] **Step 3: Add schemas in `RunnerRegistry` constructor**

In `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`, in the constructor where you added `proxies_seen` (Task 4 step 4), append more `CREATE TABLE IF NOT EXISTS`:

```typescript
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS signals_event_log (
        ts INTEGER NOT NULL,
        event_kind TEXT NOT NULL,
        signal_id TEXT NOT NULL,
        signal_kind TEXT NOT NULL,
        payload_json TEXT,
        PRIMARY KEY (ts, signal_id)
      );
    `);
    this.sql.exec(`CREATE INDEX IF NOT EXISTS idx_signals_event_log_kind ON signals_event_log(signal_kind, ts);`);

    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS runners_event_log (
        ts INTEGER NOT NULL,
        event_kind TEXT NOT NULL,
        holder_id TEXT NOT NULL,
        workflow_run_id TEXT,
        workflow_name TEXT,
        proxy_pool_hash TEXT,
        final_status TEXT,
        PRIMARY KEY (ts, holder_id, event_kind)
      );
    `);
    this.sql.exec(`CREATE INDEX IF NOT EXISTS idx_runners_event_log_holder ON runners_event_log(holder_id, ts);`);
```

- [ ] **Step 4: Write events at lifecycle points**

In the register handler (where you already added `proxies_seen` writes), add immediately before/around the `data.runners[holderId] = info;` line:

```typescript
    // Phase 2 / ADR-002 — runners_event_log register event
    this.sql.exec(
      `INSERT OR IGNORE INTO runners_event_log
       (ts, event_kind, holder_id, workflow_run_id, workflow_name, proxy_pool_hash)
       VALUES (?, 'register', ?, ?, ?, ?)`,
      now,
      holderId,
      info.workflow_run_id,
      info.workflow_name,
      info.proxy_pool_hash,
    );
```

In the unregister handler, before deleting the runner record, add:

```typescript
    // Phase 2 / ADR-002 — runners_event_log unregister event
    this.sql.exec(
      `INSERT OR IGNORE INTO runners_event_log
       (ts, event_kind, holder_id, workflow_run_id, workflow_name, final_status)
       VALUES (?, 'unregister', ?, ?, ?, 'completed')`,
      Date.now(),
      holderId,
      existing?.workflow_run_id ?? "",
      existing?.workflow_name ?? "",
    );
```

In the stale-runner pruning (GC alarm), wherever an evicted entry is removed, before removing add:

```typescript
    this.sql.exec(
      `INSERT OR IGNORE INTO runners_event_log
       (ts, event_kind, holder_id, workflow_run_id, workflow_name, final_status)
       VALUES (?, 'crashed', ?, ?, ?, 'crashed')`,
      Date.now(), info.holder_id, info.workflow_run_id, info.workflow_name,
    );
```

In the signal POST handler (`/do/signal`), after successful creation, log:

```typescript
    this.sql.exec(
      `INSERT OR REPLACE INTO signals_event_log
       (ts, event_kind, signal_id, signal_kind, payload_json)
       VALUES (?, 'create', ?, ?, ?)`,
      now,
      created.id,
      created.kind,
      JSON.stringify({ factor: created.factor, proxy_id: created.proxy_id, reason: created.reason, expires_at_ms: created.expires_at_ms }),
    );
```

In the GC alarm's signal pruning loop (where expired signals get removed), log `auto_expire`:

```typescript
    this.sql.exec(
      `INSERT OR IGNORE INTO signals_event_log (ts, event_kind, signal_id, signal_kind, payload_json) VALUES (?, 'auto_expire', ?, ?, ?)`,
      Date.now(), expired.id, expired.kind, null,
    );
```

In the signal `resume` handler (where other signals get cleared by an explicit `resume` signal), log `explicit_revoke` for each cleared signal.

- [ ] **Step 5: Add the read endpoints**

In the fetch handler dispatch, add cases:

```typescript
        if (url.pathname === "/do/signals/history" && request.method === "GET") {
          const from = parseInt(url.searchParams.get("from") ?? "0", 10);
          const to = parseInt(url.searchParams.get("to") ?? `${Date.now()}`, 10);
          const rows = Array.from(this.sql.exec<{
            ts: number; event_kind: string; signal_id: string; signal_kind: string; payload_json: string | null;
          }>(
            "SELECT ts, event_kind, signal_id, signal_kind, payload_json FROM signals_event_log WHERE ts >= ? AND ts <= ? ORDER BY ts DESC",
            from, to,
          ));
          return new Response(JSON.stringify({ rows }), { headers: { "content-type": "application/json" } });
        }
        if (url.pathname === "/do/runners/history" && request.method === "GET") {
          const from = parseInt(url.searchParams.get("from") ?? "0", 10);
          const to = parseInt(url.searchParams.get("to") ?? `${Date.now()}`, 10);
          const holder = url.searchParams.get("holder_id");
          const baseQuery = "SELECT ts, event_kind, holder_id, workflow_run_id, workflow_name, proxy_pool_hash, final_status FROM runners_event_log WHERE ts >= ? AND ts <= ?";
          const rows = holder
            ? Array.from(this.sql.exec(baseQuery + " AND holder_id = ? ORDER BY ts DESC", from, to, holder))
            : Array.from(this.sql.exec(baseQuery + " ORDER BY ts DESC", from, to));
          return new Response(JSON.stringify({ rows }), { headers: { "content-type": "application/json" } });
        }
```

- [ ] **Step 6: Run both tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/signals_event_log.test.ts test/runners_event_log.test.ts --run`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/runner_registry.ts test/signals_event_log.test.ts test/runners_event_log.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): signals_event_log + runners_event_log in RunnerRegistry DO (Phase 2, ADR-002)

Logs signal create/auto_expire/explicit_revoke and runner
register/unregister/crashed lifecycle events. New /do/signals/history
and /do/runners/history GET endpoints with optional holder_id filter.
EOF
)"
```

---

## Task 6: `config_audit_log` in `ConfigState` DO

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts`
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/config_audit_log.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/config_audit_log.test.ts
import { describe, it, expect } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";

describe("config_audit_log (Phase 2 / ADR-002)", () => {
  const stub = () => env.CONFIG_STATE_DO.get(env.CONFIG_STATE_DO.idFromName("global-config"));

  it("records old/new values + actor on PATCH", async () => {
    // First PATCH
    await stub().fetch("https://do/do/patch", {
      method: "POST",
      headers: { "content-type": "application/json", "x-actor": "operator-test", "x-actor-kind": "operator" },
      body: JSON.stringify({ key: "SHORT_MAX", value: "5", reason: "loosen for promo run" }),
    });
    // Second PATCH (now there is an old_value)
    await stub().fetch("https://do/do/patch", {
      method: "POST",
      headers: { "content-type": "application/json", "x-actor": "operator-test", "x-actor-kind": "operator" },
      body: JSON.stringify({ key: "SHORT_MAX", value: "3", reason: "back to default" }),
    });

    await runInDurableObject(stub(), async (_inst, state) => {
      const rows = Array.from(state.storage.sql.exec<{
        key: string; old_value: string | null; new_value: string; actor: string; actor_kind: string; reason: string;
      }>(
        "SELECT key, old_value, new_value, actor, actor_kind, reason FROM config_audit_log WHERE key='SHORT_MAX' ORDER BY ts",
      ));
      expect(rows).toHaveLength(2);
      expect(rows[0]).toMatchObject({ old_value: null, new_value: "5", actor_kind: "operator" });
      expect(rows[1]).toMatchObject({ old_value: "5", new_value: "3" });
    });
  });

  it("exposes /do/config/history", async () => {
    const q = await stub().fetch(
      "https://do/do/config/history?from=0&to=" + (Date.now() + 1_000_000),
    );
    const { rows } = (await q.json()) as any;
    expect(Array.isArray(rows)).toBe(true);
  });
});
```

- [ ] **Step 2: Verify the test fails**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/config_audit_log.test.ts --run`
Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Add schema, write on PATCH, and read endpoint**

In `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts`, in the constructor:

```typescript
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS config_audit_log (
        ts INTEGER NOT NULL,
        key TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        actor TEXT,
        actor_kind TEXT NOT NULL,
        reason TEXT,
        PRIMARY KEY (ts, key)
      );
    `);
```

In the PATCH handler, after reading `current[key]` and before writing the new value, capture `old_value`. After the write, append:

```typescript
    const actor = (request.headers.get("x-actor") ?? "anonymous").slice(0, 100);
    const actorKind = (request.headers.get("x-actor-kind") ?? "system") === "operator" ? "operator" : "system";
    this.sql.exec(
      `INSERT OR REPLACE INTO config_audit_log (ts, key, old_value, new_value, actor, actor_kind, reason) VALUES (?, ?, ?, ?, ?, ?, ?)`,
      Date.now(),
      key,
      old_value,
      typeof newValue === "string" ? newValue : JSON.stringify(newValue),
      actor,
      actorKind,
      (body.reason ?? "").slice(0, 500),
    );
```

(Adjust to match the actual variable names in `config_state.ts`; the structure is the same — capture before, log after.)

In the fetch dispatch, add:

```typescript
        if (url.pathname === "/do/config/history" && request.method === "GET") {
          const from = parseInt(url.searchParams.get("from") ?? "0", 10);
          const to = parseInt(url.searchParams.get("to") ?? `${Date.now()}`, 10);
          const key = url.searchParams.get("key");
          const query = key
            ? this.sql.exec("SELECT * FROM config_audit_log WHERE ts >= ? AND ts <= ? AND key = ? ORDER BY ts DESC", from, to, key)
            : this.sql.exec("SELECT * FROM config_audit_log WHERE ts >= ? AND ts <= ? ORDER BY ts DESC", from, to);
          return new Response(JSON.stringify({ rows: Array.from(query) }), { headers: { "content-type": "application/json" } });
        }
```

- [ ] **Step 4: Run the test**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/config_audit_log.test.ts --run`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/config_state.ts test/config_audit_log.test.ts
git commit -m "feat(worker): config_audit_log in ConfigState DO (Phase 2, ADR-002)"
```

---

## Task 7: `login_event_log` in `GlobalLoginState` DO

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts`
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/login_event_log.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/login_event_log.test.ts
import { describe, it, expect } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";

describe("login_event_log (Phase 2 / ADR-002)", () => {
  const stub = () => env.GLOBAL_LOGIN_STATE_DO.get(env.GLOBAL_LOGIN_STATE_DO.idFromName("global"));

  it("logs record_attempt as an 'attempt' event with holder_id and outcome", async () => {
    await stub().fetch("https://do/do/login_state/record_attempt", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ proxy_id: "P-1", success: false, holder_id: "h-login-1" }),
    });

    await runInDurableObject(stub(), async (_inst, state) => {
      const rows = Array.from(state.storage.sql.exec<{ event_kind: string; outcome: string; holder_id: string }>(
        "SELECT event_kind, outcome, holder_id FROM login_event_log WHERE holder_id='h-login-1'",
      ));
      expect(rows[0]).toMatchObject({ event_kind: "attempt", outcome: "failure", holder_id: "h-login-1" });
    });
  });

  it("exposes /do/login/history", async () => {
    const q = await stub().fetch(
      "https://do/do/login/history?from=0&to=" + (Date.now() + 1_000_000),
    );
    expect(q.status).toBe(200);
    const { rows } = (await q.json()) as any;
    expect(Array.isArray(rows)).toBe(true);
  });
});
```

- [ ] **Step 2: Verify the test fails**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/login_event_log.test.ts --run`
Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Add schema, write at each lifecycle point, expose read**

In `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts` constructor:

```typescript
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS login_event_log (
        ts INTEGER NOT NULL,
        event_kind TEXT NOT NULL,
        holder_id TEXT,
        outcome TEXT,
        cookie_version INTEGER,
        detail TEXT,
        PRIMARY KEY (ts, event_kind, COALESCE(holder_id, ''))
      );
    `);
    this.sql.exec(`CREATE INDEX IF NOT EXISTS idx_login_event_log_holder ON login_event_log(holder_id, ts);`);
```

In the `record_attempt` handler, after appending to the rolling buffer:

```typescript
    const outcome = body.success ? "success" : "failure";
    this.sql.exec(
      `INSERT OR REPLACE INTO login_event_log (ts, event_kind, holder_id, outcome, detail) VALUES (?, 'attempt', ?, ?, ?)`,
      Date.now(),
      (body.holder_id ?? null) as string | null,
      outcome,
      (body.detail ?? null) as string | null,
    );
```

Similarly in `publish` (log `publish` with `cookie_version`), `invalidate` (log `invalidate`), `acquire_lease` (log `lease_acquire`), `release_lease` (log `lease_release`).

In fetch dispatch:

```typescript
        if (url.pathname === "/do/login/history" && request.method === "GET") {
          const from = parseInt(url.searchParams.get("from") ?? "0", 10);
          const to = parseInt(url.searchParams.get("to") ?? `${Date.now()}`, 10);
          const holder = url.searchParams.get("holder_id");
          const q = holder
            ? this.sql.exec("SELECT * FROM login_event_log WHERE ts >= ? AND ts <= ? AND holder_id = ? ORDER BY ts DESC", from, to, holder)
            : this.sql.exec("SELECT * FROM login_event_log WHERE ts >= ? AND ts <= ? ORDER BY ts DESC", from, to);
          return new Response(JSON.stringify({ rows: Array.from(q) }), { headers: { "content-type": "application/json" } });
        }
```

- [ ] **Step 4: Run the test**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/login_event_log.test.ts --run`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/global_login_state.ts test/login_event_log.test.ts
git commit -m "feat(worker): login_event_log in GlobalLoginState DO (Phase 2, ADR-002)"
```

---

## Task 8: GC alarm — wire retention sweeps in every history-bearing DO

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts` (GC alarm)
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts` (GC alarm)
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts` (GC alarm)
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/metrics_state.ts` (already has alarm — add prune call)

- [ ] **Step 1: Add prune call in `RunnerRegistry.alarm()`**

Find the existing alarm handler in `runner_registry.ts` and append (importing `pruneLogTable` at top):

```typescript
import { pruneLogTable } from "./event_log_helpers";
// ...
async alarm() {
  // ... existing GC logic ...
  const now = Date.now();
  const signalsRetention = parseInt(this.env.SIGNALS_EVENT_LOG_RETENTION_DAYS ?? "90", 10) * 86_400_000;
  const runnersRetention = parseInt(this.env.RUNNERS_EVENT_LOG_RETENTION_DAYS ?? "90", 10) * 86_400_000;
  pruneLogTable(this.sql, "signals_event_log", signalsRetention, 100_000, now);
  pruneLogTable(this.sql, "runners_event_log", runnersRetention, 100_000, now);
}
```

- [ ] **Step 2: Add prune call in `ConfigState.alarm()` (create alarm if missing)**

If `ConfigState` has no `alarm()` method, add one that calls itself on a schedule (e.g., daily). Inside it:

```typescript
const retention = parseInt(this.env.CONFIG_AUDIT_LOG_RETENTION_DAYS ?? "365", 10) * 86_400_000;
pruneLogTable(this.sql, "config_audit_log", retention, 100_000, Date.now());
this.storage.setAlarm(Date.now() + 86_400_000); // re-arm daily
```

- [ ] **Step 3: Add prune call in `GlobalLoginState.alarm()`**

Same pattern with `LOGIN_EVENT_LOG_RETENTION_DAYS`.

- [ ] **Step 4: Add prune call in `MetricsState.alarm()`**

Use `METRICS_RETENTION_DAYS` and `METRICS_MAX_ROWS`.

- [ ] **Step 5: Write a sanity test that prune doesn't break normal operations**

Append to `test/event_log_helpers.test.ts` (or a new file):

```typescript
it("does not error when called on an empty table", async () => {
  const id = env.GLOBAL_LOGIN_STATE_DO.idFromName("prune-empty");
  const stub = env.GLOBAL_LOGIN_STATE_DO.get(id);
  await runInDurableObject(stub, async (_inst, state) => {
    state.storage.sql.exec("CREATE TABLE IF NOT EXISTS empty_log (ts INTEGER PRIMARY KEY)");
    state.storage.sql.exec("DELETE FROM empty_log");
    pruneLogTable(state.storage.sql, "empty_log", 1000, 100, 5000);
    const count = state.storage.sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM empty_log").one().n;
    expect(count).toBe(0);
  });
});
```

- [ ] **Step 6: Run all tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/runner_registry.ts src/config_state.ts src/global_login_state.ts src/metrics_state.ts test/event_log_helpers.test.ts
git commit -m "feat(worker): wire retention sweeps in every history-bearing DO (Phase 2, ADR-002)"
```

---

## Task 9: Cron `scheduled` handler — writes a snapshot to MetricsState every minute

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/scheduled_handler.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/scheduled_handler.test.ts
import { describe, it, expect } from "vitest";
import { env, runInDurableObject } from "cloudflare:test";
import worker from "../src/index";

describe("Cron scheduled handler (Phase 2 / ADR-003)", () => {
  it("calls MetricsState.recordSnapshot once with source='cron'", async () => {
    // Register at least one runner so the snapshot is 'active' and gets written.
    await env.RUNNER_REGISTRY_DO.get(env.RUNNER_REGISTRY_DO.idFromName("runners")).fetch(
      "https://do/do/register",
      { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ holder_id: "cron-test-holder" }) },
    );

    // Fire the cron event manually.
    const event = { scheduledTime: Date.now(), cron: "* * * * *" } as ScheduledEvent;
    await worker.scheduled?.(event, env, { waitUntil: () => {}, passThroughOnException: () => {} } as any);

    const stub = env.METRICS_STATE_DO.get(env.METRICS_STATE_DO.idFromName("global-metrics"));
    const r = await stub.fetch("https://do/do/metrics/range?from=0&to=" + (Date.now() + 10_000));
    const { rows } = (await r.json()) as any;
    const cronRow = rows.find((r: any) => r.source === "cron");
    expect(cronRow).toBeDefined();
    expect(cronRow.payload.runners.active_runners.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Verify the test fails (no scheduled handler yet)**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/scheduled_handler.test.ts --run`
Expected: FAIL — `worker.scheduled` undefined.

- [ ] **Step 3: Add the scheduled handler in index.ts**

In `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`, in the default export object, alongside `fetch`, add:

```typescript
  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Phase 2 / ADR-003 — Cron 1-min tick. Pull current snapshot, write
    // to MetricsState DO (subject to idle suppression in the DO itself).
    if (!env.METRICS_STATE_DO) return;
    try {
      // Reuse the same aggregation used by /ops/snapshot. Pass a synthetic
      // URL since aggregateOpsSnapshot expects one for the proxy_ids query
      // param (which we leave empty so it auto-discovers from proxies_seen
      // — see Task 10).
      const fakeUrl = new URL("https://internal/ops/snapshot");
      const snapResp = await aggregateOpsSnapshot(env, fakeUrl);
      const payload = await snapResp.json();
      const stub = env.METRICS_STATE_DO.get(env.METRICS_STATE_DO.idFromName("global-metrics"));
      await stub.fetch("https://do/do/metrics/record", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ts: Date.now(),
          payload,
          source: "cron",
        }),
      });
    } catch (err) {
      console.error("scheduled handler error", { error: err instanceof Error ? err.message : String(err) });
    }
  },
```

- [ ] **Step 4: Run the test**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/scheduled_handler.test.ts --run`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/index.ts test/scheduled_handler.test.ts
git commit -m "feat(worker): Cron scheduled handler writes 1-min snapshots (Phase 2, ADR-003)"
```

---

## Task 10: `/ops/snapshot` — auto-fetch `proxies_seen` + write snapshot via `waitUntil`

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts:875-907` (`aggregateOpsSnapshot`)

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/ops_snapshot_phase2.test.ts
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import worker from "../src/index";

const auth = { authorization: `Bearer ${env.PROXY_COORDINATOR_TOKEN}` };

describe("/ops/snapshot Phase 2 enhancements", () => {
  it("auto-enumerates proxies from proxies_seen when no ?proxy_ids", async () => {
    // Seed proxies_seen by registering a runner with proxy_pool.
    await fetchWorker("/register", "POST", { holder_id: "seen-test", proxy_pool: [{ id: "Auto-1", name: "Auto-1" }] });

    const r = await fetchWorker("/ops/snapshot", "GET", null);
    expect(r.status).toBe(200);
    const data: any = await r.json();
    const ids = data.proxies.map((p: any) => p.proxy_id);
    expect(ids).toContain("Auto-1");
  });

  it("still honours ?proxy_ids=... when provided", async () => {
    const r = await fetchWorker("/ops/snapshot?proxy_ids=Explicit-1", "GET", null);
    expect(r.status).toBe(200);
    const data: any = await r.json();
    expect(data.proxies.length).toBe(1);
    expect(data.proxies[0].proxy_id).toBe("Explicit-1");
  });

  it("writes a snapshot to MetricsState with source=dashboard", async () => {
    await fetchWorker("/ops/snapshot", "GET", null);
    // The waitUntil promise has to complete before we can read.
    await new Promise((r) => setTimeout(r, 200));

    const stub = env.METRICS_STATE_DO.get(env.METRICS_STATE_DO.idFromName("global-metrics"));
    const q = await stub.fetch("https://do/do/metrics/range?from=0&to=" + (Date.now() + 10_000));
    const { rows } = (await q.json()) as any;
    const dashRow = rows.find((r: any) => r.source === "dashboard");
    expect(dashRow).toBeDefined();
  });
});

async function fetchWorker(path: string, method: string, body: unknown) {
  return worker.fetch(
    new Request(`https://worker.test${path}`, {
      method,
      headers: { ...auth, "content-type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }),
    env,
    { waitUntil: (p: Promise<unknown>) => p.then(() => {}, () => {}), passThroughOnException: () => {} } as any,
  );
}
```

- [ ] **Step 2: Verify tests fail**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/ops_snapshot_phase2.test.ts --run`
Expected: 3 failures (auto-discovery missing, metrics write missing).

- [ ] **Step 3: Modify `aggregateOpsSnapshot` to auto-fetch `proxies_seen`**

In `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`, find `aggregateOpsSnapshot` (line ~875). Replace the proxy_ids parsing block with:

```typescript
async function aggregateOpsSnapshot(env: Env, url: URL): Promise<Response> {
  const rawIds = (url.searchParams.get("proxy_ids") ?? "").trim();
  let proxyIds: string[] = [];
  if (rawIds) {
    // Backward compat: caller explicitly listed proxy IDs.
    proxyIds = rawIds
      .split(",")
      .map((s) => normalizeProxyId(s.trim()))
      .filter((s) => s !== "")
      .slice(0, 32);
  } else {
    // Phase 2 / ADR-004 — auto-enumerate from proxies_seen.
    proxyIds = await fetchSeenProxyIds(env);
  }

  // ... rest unchanged ...
}

async function fetchSeenProxyIds(env: Env): Promise<string[]> {
  if (!env.RUNNER_REGISTRY_DO) return [];
  try {
    const id = env.RUNNER_REGISTRY_DO.idFromName("runners");
    const stub = env.RUNNER_REGISTRY_DO.get(id);
    const r = await stub.fetch("https://do/do/proxies_seen", { method: "GET" });
    if (r.status !== 200) return [];
    const data = await r.json() as { proxies?: Array<{ id: string; name: string; last_seen_ms: number }> };
    return (data.proxies ?? []).map((p) => p.id).slice(0, 100);
  } catch {
    return [];
  }
}
```

- [ ] **Step 4: Wrap `/ops/snapshot` to write a dashboard snapshot via `waitUntil`**

In `index.ts` `fetch` handler, find the `/ops/snapshot` case and modify:

```typescript
        case "/ops/snapshot": {
          const snapResp = await aggregateOpsSnapshot(env, url);
          // Phase 2 / ADR-003 — also persist for time-series. Source
          // tag distinguishes from cron-driven writes. Fire-and-forget
          // via waitUntil so the response is not blocked.
          if (env.METRICS_STATE_DO) {
            const cloned = snapResp.clone();
            _ctx.waitUntil((async () => {
              try {
                const payload = await cloned.json();
                const stub = env.METRICS_STATE_DO!.get(
                  env.METRICS_STATE_DO!.idFromName("global-metrics"),
                );
                await stub.fetch("https://do/do/metrics/record", {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify({ ts: Date.now(), payload, source: "dashboard" }),
                });
              } catch (err) {
                console.warn("dashboard metrics write failed", { error: err instanceof Error ? err.message : String(err) });
              }
            })());
          }
          return snapResp;
        }
```

- [ ] **Step 5: Run the test**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/ops_snapshot_phase2.test.ts --run`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/index.ts test/ops_snapshot_phase2.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): /ops/snapshot auto-discovers proxies + writes snapshot (Phase 2)

Empty ?proxy_ids now auto-enumerates from proxies_seen (ADR-004).
Every /ops/snapshot call also persists the payload to MetricsState
with source='dashboard' via ctx.waitUntil (ADR-003 hybrid writer).
EOF
)"
```

---

## Task 11: Top-level routes for history GETs (mounted in main `fetch`)

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/history_routes.test.ts
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import worker from "../src/index";

const auth = { authorization: `Bearer ${env.PROXY_COORDINATOR_TOKEN}` };
const fetchWorker = (path: string) =>
  worker.fetch(
    new Request(`https://worker.test${path}`, { method: "GET", headers: auth }),
    env,
    { waitUntil: () => {}, passThroughOnException: () => {} } as any,
  );

describe("top-level history GET routes (Phase 2)", () => {
  it("GET /signals/history returns 200 with rows array", async () => {
    const r = await fetchWorker("/signals/history?from=0&to=" + (Date.now() + 1_000_000));
    expect(r.status).toBe(200);
    const data: any = await r.json();
    expect(Array.isArray(data.rows)).toBe(true);
  });
  it("GET /runners/history returns 200", async () => {
    const r = await fetchWorker("/runners/history?from=0&to=" + (Date.now() + 1_000_000));
    expect(r.status).toBe(200);
  });
  it("GET /login/history returns 200", async () => {
    const r = await fetchWorker("/login/history?from=0&to=" + (Date.now() + 1_000_000));
    expect(r.status).toBe(200);
  });
  it("GET /config/history returns 200", async () => {
    const r = await fetchWorker("/config/history?from=0&to=" + (Date.now() + 1_000_000));
    expect(r.status).toBe(200);
  });
  it("GET /metrics/range returns 200", async () => {
    const r = await fetchWorker("/metrics/range?from=0&to=" + (Date.now() + 1_000_000));
    expect(r.status).toBe(200);
  });
});
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/history_routes.test.ts --run`
Expected: 404 errors on all 5.

- [ ] **Step 3: Add the routes**

In `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`, add to `GET_ALLOWED_PATHS`:

```typescript
const GET_ALLOWED_PATHS = new Set<string>([
  // ... existing paths ...
  "/signals/history",
  "/runners/history",
  "/login/history",
  "/config/history",
  "/metrics/range",
]);
```

Add to `COOKIE_AUTH_PATHS`:

```typescript
const COOKIE_AUTH_PATHS = new Set<string>([
  // ... existing paths ...
  "/signals/history",
  "/runners/history",
  "/login/history",
  "/config/history",
  "/metrics/range",
]);
```

In the switch dispatch, add cases:

```typescript
        case "/signals/history":
          return await forwardToRunnerRegistryDo(env, "/do/signals/history?" + url.searchParams.toString(), "GET", null);
        case "/runners/history":
          return await forwardToRunnerRegistryDo(env, "/do/runners/history?" + url.searchParams.toString(), "GET", null);
        case "/login/history":
          return await forwardToGlobalLoginStateDo(env, "/do/login/history?" + url.searchParams.toString(), "GET", null);
        case "/config/history":
          return await forwardToConfigStateDo(env, "/do/config/history?" + url.searchParams.toString(), "GET", null);
        case "/metrics/range": {
          if (!env.METRICS_STATE_DO) {
            return jsonResponse({ error: "metrics_state binding not configured" }, 503);
          }
          const id = env.METRICS_STATE_DO.idFromName("global-metrics");
          const stub = env.METRICS_STATE_DO.get(id);
          const r = await stub.fetch("https://do/do/metrics/range?" + url.searchParams.toString(), { method: "GET" });
          return new Response(await r.text(), { status: r.status, headers: r.headers });
        }
```

- [ ] **Step 4: Run the tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/history_routes.test.ts --run`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/index.ts test/history_routes.test.ts
git commit -m "feat(worker): top-level history GET routes for dashboard (Phase 2)"
```

---

## Task 12: Phase 2 verification — full suite + deploy dry run

**Files:** (none modified)

- [ ] **Step 1: Run full vitest suite**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all tests pass.

- [ ] **Step 2: Confirm deploy dry-run succeeds**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler deploy --dry-run --outdir /tmp/wrangler-phase2 2>&1 | tail -20`
Expected: "Built successfully" — no migration errors, no missing classes.

- [ ] **Step 3: Check migration tag ordering**

Run: `grep -A1 'tag = "v' wrangler.toml`
Expected output shows tags in sequential order v1 → v6, no duplicates, no out-of-order.

- [ ] **Step 4: Phase 2 handoff note**

Phase 2 is functionally complete. Dashboard UI still renders the old way because we haven't touched the HTML/JS yet — that's Phase 3. The new endpoints (`/signals/history`, etc.) are reachable but unused.

Deploy Phase 2 to production before starting Phase 3 — that gives the new tables ~24h to fill with real cron-driven snapshots so Phase 3 UI work has real data to render.

See `docs/superpowers/plans/2026-05-16-dashboard-overhaul-phase-3-dashboard-ui.md`.

---

## Self-Review Checklist

- ✅ MetricsState DO: schema + write + range + prune all covered by tests
- ✅ 4 history tables in 3 existing DOs, all with retention sweeps wired in alarms
- ✅ Cron scheduled handler tested
- ✅ /ops/snapshot dual-write tested
- ✅ Auto-proxy-discovery from `proxies_seen` tested
- ✅ All routes return 200 (basic smoke tests)
- ✅ No new TS files outside of `src/metrics_state.ts` and `src/event_log_helpers.ts`
- ✅ Backward compat: missing `proxy_pool` on register doesn't break anything
- ✅ Phase 2 produces NO user-visible change (dashboard unchanged)
