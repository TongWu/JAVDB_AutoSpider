# Web Backend Operational Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the web UI's operational surface: register 5 workflow schemas, expose all rollback parameters, replace onboarding 501 stubs with GH Actions dispatch, unify cursor encoding, and eliminate per-request `ensureTable()` overhead.

**Architecture:** All changes are in the `JAVDB_AutoSpider_Web/` directory. The workflow registry is a static TypeScript module — no database schema changes. Cursor unification extracts a shared module used by `sessions.ts` and `history.ts`. The init middleware replaces 9 scattered `ensureTable()` calls with a single cold-start initialization.

**Tech Stack:** TypeScript, Hono, Cloudflare Workers, D1, Vitest + `@cloudflare/vitest-pool-workers`

**Related:** [ADR-020](ADR-020-web-operational-polish.md), [IMP-ADR018-01](../ADR-018-Web-Security-Hardening/IMP-ADR018-01-security-hardening.md), [IMP-ADR019-01](../ADR-019-Web-Feature-Parity/IMP-ADR019-01-feature-parity.md)

**Dependency:** This plan assumes IMP-ADR018-01 and IMP-ADR019-01 have been implemented first. The `AUTH_KV` binding, async `findUser()`, and config alias map are prerequisites.

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `server/services/workflow-registry.ts` | Static workflow parameter schemas for 5 workflows |
| Create | `server/services/cursor.ts` | Shared cursor encode/decode (JSON base64) |
| Create | `server/services/table-init.ts` | `initializeTables()` — batched `CREATE TABLE IF NOT EXISTS` |
| Modify | `server/routes/gh-actions.ts` | Add `GET /workflows/:name/schema`, validate inputs on dispatch |
| Modify | `server/routes/sessions.ts` | Full rollback parameter forwarding, remove inline cursor functions |
| Modify | `server/routes/onboarding.ts` | Replace 501 stubs with GH Actions dispatch |
| Modify | `server/routes/history.ts` | Replace inline cursor with shared `cursor.ts` |
| Modify | `server/routes/operations.ts` | Remove `ensureTable()` calls |
| Modify | `server/routes/tasks.ts` | Remove `ensureTable()` calls |
| Modify | `server/app.ts` | Add init middleware for table initialization |
| Create | `server/__tests__/workflow-registry.test.ts` | Schema completeness tests |
| Create | `server/__tests__/cursor.test.ts` | Cursor encode/decode tests |
| Create | `server/__tests__/table-init.test.ts` | Init middleware tests |
| Modify | `server/__tests__/sessions-routes.test.ts` | Rollback parameter tests |
| Modify | `server/__tests__/onboarding-routes.test.ts` | Onboarding dispatch tests |

---

## Task 1: Shared Cursor Module

**Files:**
- Create: `JAVDB_AutoSpider_Web/server/services/cursor.ts`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/cursor.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/cursor.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { cursorEncode, cursorDecode } from "../services/cursor";

describe("cursor", () => {
  it("round-trips a numeric id", () => {
    const encoded = cursorEncode({ id: 42 });
    const decoded = cursorDecode<{ id: number }>(encoded);
    expect(decoded.id).toBe(42);
  });

  it("round-trips a string id", () => {
    const encoded = cursorEncode({ sid: "20260524T120000Z-0001-0001" });
    const decoded = cursorDecode<{ sid: string }>(encoded);
    expect(decoded.sid).toBe("20260524T120000Z-0001-0001");
  });

  it("round-trips multiple fields", () => {
    const encoded = cursorEncode({ id: 100, sort: "desc" });
    const decoded = cursorDecode<{ id: number; sort: string }>(encoded);
    expect(decoded.id).toBe(100);
    expect(decoded.sort).toBe("desc");
  });

  it("throws on malformed cursor", () => {
    expect(() => cursorDecode("not-base64-json!!!")).toThrow();
  });

  it("throws on non-JSON base64", () => {
    const plainNumeric = btoa("12345");
    expect(() => cursorDecode(plainNumeric)).toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/cursor.test.ts`
Expected: FAIL — module `../services/cursor` does not exist.

- [ ] **Step 3: Implement cursor.ts**

Create `server/services/cursor.ts`:

```typescript
import { HTTPException } from "hono/http-exception";

export function cursorEncode(values: Record<string, unknown>): string {
  return btoa(JSON.stringify(values));
}

export function cursorDecode<T = Record<string, unknown>>(cursor: string): T {
  try {
    const decoded = atob(cursor);
    const parsed = JSON.parse(decoded);
    if (typeof parsed !== "object" || parsed === null) {
      throw new Error("not an object");
    }
    return parsed as T;
  } catch {
    throw new HTTPException(400, {
      message: JSON.stringify({
        error: {
          code: "invalid_cursor",
          message: "Invalid cursor format. Please reload the page.",
        },
      }),
    });
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/cursor.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/cursor.ts server/__tests__/cursor.test.ts
git commit -m "feat(cursor): add shared cursor encode/decode module"
```

---

## Task 2: Migrate sessions.ts and history.ts to Shared Cursor

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/sessions.ts:1-49`
- Modify: `JAVDB_AutoSpider_Web/server/routes/history.ts:1-23`

- [ ] **Step 1: Update sessions.ts**

In `server/routes/sessions.ts`, replace the import section and remove inline cursor functions.

Add import:

```typescript
import { cursorEncode, cursorDecode } from "../services/cursor";
```

Remove the inline `cursorEncode` and `cursorDecode` functions (lines 38–49):

```typescript
// DELETE these lines:
function cursorEncode(sessionId: string): string {
  return btoa(JSON.stringify({ sid: sessionId }));
}

function cursorDecode(cursor: string): string {
  try {
    const parsed = JSON.parse(atob(cursor));
    return parsed.sid;
  } catch {
    throw new HTTPException(400, { message: "Invalid cursor" });
  }
}
```

Update all call sites in sessions.ts:
- `cursorEncode(sessionId)` → `cursorEncode({ sid: sessionId })`
- `cursorDecode(cursor)` → `cursorDecode<{ sid: string }>(cursor).sid`

In the list handler (around line 67), find:

```typescript
    conditions.push("Id < ?");
    bindings.push(cursorDecode(cursor));
```

Replace with:

```typescript
    conditions.push("Id < ?");
    bindings.push(cursorDecode<{ sid: string }>(cursor).sid);
```

Find the `nextCursor` generation (around line 87):

```typescript
  const nextCursor = rows.length === limit ? cursorEncode(rows[rows.length - 1].Id) : null;
```

Replace with:

```typescript
  const nextCursor = rows.length === limit ? cursorEncode({ sid: rows[rows.length - 1].Id }) : null;
```

- [ ] **Step 2: Update history.ts**

In `server/routes/history.ts`, replace the import section.

Add import:

```typescript
import { cursorEncode, cursorDecode } from "../services/cursor";
```

Remove the inline cursor functions (lines 12–23):

```typescript
// DELETE these lines:
function cursorEncode(id: number): string {
  return btoa(String(id));
}

function cursorDecode(cursor: string): number {
  try {
    return parseInt(atob(cursor), 10);
  } catch {
    throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "history.invalid_cursor", message: "cursor is malformed" } }),
    });
  }
}
```

Update all call sites in history.ts:
- `cursorEncode(id)` → `cursorEncode({ id })`
- `cursorDecode(cursor)` → `cursorDecode<{ id: number }>(cursor).id`

Search for all occurrences. The `buildMovieQuery` function (line 62) has:

```typescript
    bindings.push(cursorDecode(params.cursor));
```

Replace with:

```typescript
    bindings.push(cursorDecode<{ id: number }>(params.cursor).id);
```

Similarly update any `buildTorrentQuery` cursor usage, and all `cursorEncode` calls where the next cursor is generated.

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/sessions-routes.test.ts server/__tests__/history-routes.test.ts`
Expected: PASS — JSON cursors are a format change but tests generate their own cursors from the response.

- [ ] **Step 4: Commit**

```bash
git add server/routes/sessions.ts server/routes/history.ts
git commit -m "refactor(cursor): migrate sessions and history to shared cursor module"
```

---

## Task 3: Workflow Parameter Schema Registry

**Files:**
- Create: `JAVDB_AutoSpider_Web/server/services/workflow-registry.ts`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/workflow-registry.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/workflow-registry.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { getWorkflowSchema, WORKFLOW_REGISTRY, type WorkflowEntry } from "../services/workflow-registry";

describe("workflow-registry", () => {
  it("contains 5 registered workflows", () => {
    expect(WORKFLOW_REGISTRY.size).toBe(5);
  });

  it("returns schema for WeeklyDedup.yml", () => {
    const schema = getWorkflowSchema("WeeklyDedup.yml");
    expect(schema).toBeDefined();
    expect(schema!.displayName).toBe("Weekly Dedup");
    expect(schema!.params.length).toBe(8);
    expect(schema!.safetyGate).toBeDefined();
    expect(schema!.safetyGate!.field).toBe("confirm_production");
  });

  it("returns schema for Migration.yml", () => {
    const schema = getWorkflowSchema("Migration.yml");
    expect(schema).toBeDefined();
    expect(schema!.params.length).toBeGreaterThanOrEqual(15);
    expect(schema!.safetyGate).toBeDefined();
  });

  it("returns schema for TestIngestion.yml", () => {
    const schema = getWorkflowSchema("TestIngestion.yml");
    expect(schema).toBeDefined();
    expect(schema!.params.length).toBe(2);
    expect(schema!.safetyGate).toBeUndefined();
  });

  it("returns schema for BakeCheck.yml", () => {
    const schema = getWorkflowSchema("BakeCheck.yml");
    expect(schema).toBeDefined();
    expect(schema!.params.length).toBe(2);
    expect(schema!.safetyGate).toBeUndefined();
  });

  it("returns schema for RollbackD1.yml", () => {
    const schema = getWorkflowSchema("RollbackD1.yml");
    expect(schema).toBeDefined();
    expect(schema!.params.length).toBe(10);
    expect(schema!.safetyGate).toBeDefined();
  });

  it("returns undefined for unknown workflow", () => {
    expect(getWorkflowSchema("NotReal.yml")).toBeUndefined();
  });

  it("every param has required fields", () => {
    for (const [name, entry] of WORKFLOW_REGISTRY) {
      for (const param of entry.params) {
        expect(param.name, `${name}/${param.name}`).toBeTruthy();
        expect(param.type, `${name}/${param.name}`).toMatch(/^(string|boolean|choice)$/);
        expect(typeof param.required, `${name}/${param.name}`).toBe("boolean");
      }
    }
  });

  it("choice params have choices array", () => {
    for (const [name, entry] of WORKFLOW_REGISTRY) {
      for (const param of entry.params) {
        if (param.type === "choice") {
          expect(param.choices, `${name}/${param.name}`).toBeDefined();
          expect(param.choices!.length, `${name}/${param.name}`).toBeGreaterThan(0);
        }
      }
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/workflow-registry.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement workflow-registry.ts**

Create `server/services/workflow-registry.ts`:

```typescript
export interface WorkflowParam {
  name: string;
  type: "string" | "boolean" | "choice";
  required: boolean;
  default?: string | boolean;
  choices?: string[];
  description?: string;
}

export interface WorkflowEntry {
  filename: string;
  displayName: string;
  description: string;
  category: "ingestion" | "maintenance" | "migration" | "monitoring";
  params: WorkflowParam[];
  safetyGate?: {
    field: string;
    requiredValue: string;
    triggerWhen: Record<string, unknown>;
  };
}

export const WORKFLOW_REGISTRY = new Map<string, WorkflowEntry>();

WORKFLOW_REGISTRY.set("WeeklyDedup.yml", {
  filename: "WeeklyDedup.yml",
  displayName: "Weekly Dedup",
  description: "Rclone deduplication scan — detects and optionally removes duplicate files",
  category: "maintenance",
  params: [
    { name: "runner", type: "choice", required: false, default: "self-hosted", choices: ["ubuntu-latest", "self-hosted"] },
    { name: "root_path", type: "string", required: false, default: "gdrive:/剧集/不可以色色/JAV-Sync", description: "Rclone remote path" },
    { name: "dry_run", type: "boolean", required: false, default: true },
    { name: "confirm_production", type: "string", required: false, default: "", description: 'Type "I-UNDERSTAND" for non-dry-run' },
    { name: "incremental", type: "boolean", required: false, default: false },
    { name: "years", type: "string", required: false, default: "", description: "Comma-separated years list" },
    { name: "workers", type: "string", required: false, default: "4", description: "Number of parallel workers" },
    { name: "log_level", type: "choice", required: false, default: "INFO", choices: ["DEBUG", "INFO", "WARNING", "ERROR"] },
  ],
  safetyGate: {
    field: "confirm_production",
    requiredValue: "I-UNDERSTAND",
    triggerWhen: { dry_run: false },
  },
});

WORKFLOW_REGISTRY.set("Migration.yml", {
  filename: "Migration.yml",
  displayName: "Migration",
  description: "Database migration runner — schema updates, backfills, and inventory alignment",
  category: "migration",
  params: [
    { name: "dry_run", type: "boolean", required: false, default: true },
    { name: "confirm_production", type: "string", required: false, default: "", description: 'Type "I-UNDERSTAND" for non-dry-run' },
    { name: "backup", type: "boolean", required: false, default: false },
    { name: "verify", type: "boolean", required: false, default: false },
    { name: "skip_schema", type: "boolean", required: false, default: false },
    { name: "normalize_datetimes", type: "boolean", required: false, default: false },
    { name: "backfill_actors", type: "boolean", required: false, default: false },
    { name: "align_inventory_history", type: "boolean", required: false, default: false },
    { name: "backfill_limit", type: "string", required: false, default: "0", description: "Max rows to process (0 = all)" },
    { name: "no_proxy", type: "boolean", required: false, default: false },
    { name: "use_cf_bypass", type: "boolean", required: false, default: false },
    { name: "align_limit_per_worker", type: "string", required: false, default: "0", description: "Max missing codes per worker (0 = all)" },
    { name: "align_codes", type: "string", required: false, default: "", description: "Comma-separated video codes override" },
    { name: "align_no_proxy", type: "boolean", required: false, default: false },
    { name: "align_no_login", type: "boolean", required: false, default: false },
    { name: "align_shuffle", type: "boolean", required: false, default: true },
    { name: "align_enqueue_qb", type: "boolean", required: false, default: true },
    { name: "align_execute_delete", type: "boolean", required: false, default: false },
    { name: "align_qb_category", type: "string", required: false, default: "", description: "qBittorrent category override" },
    { name: "runner", type: "choice", required: false, default: "self-hosted", choices: ["ubuntu-latest", "self-hosted"] },
  ],
  safetyGate: {
    field: "confirm_production",
    requiredValue: "I-UNDERSTAND",
    triggerWhen: { dry_run: false },
  },
});

WORKFLOW_REGISTRY.set("TestIngestion.yml", {
  filename: "TestIngestion.yml",
  displayName: "Test Ingestion",
  description: "Test pipeline run with dry-run mode — validates spider and pipeline execution",
  category: "ingestion",
  params: [
    { name: "runner", type: "choice", required: false, default: "ubuntu-latest", choices: ["ubuntu-latest", "self-hosted"] },
    { name: "proxy_spider", type: "boolean", required: false, default: true, description: "Enable proxy for Spider requests" },
  ],
});

WORKFLOW_REGISTRY.set("BakeCheck.yml", {
  filename: "BakeCheck.yml",
  displayName: "Bake Check",
  description: "Check ADR-006 bake metrics — verifies data stability over a bake window",
  category: "monitoring",
  params: [
    { name: "since", type: "string", required: false, default: "2026-05-16", description: "Bake window start date (YYYY-MM-DD)" },
    { name: "runner", type: "choice", required: false, default: "ubuntu-latest", choices: ["ubuntu-latest", "self-hosted"] },
  ],
});

WORKFLOW_REGISTRY.set("RollbackD1.yml", {
  filename: "RollbackD1.yml",
  displayName: "Rollback D1",
  description: "Rollback a session — reverts history and report data for a specific session",
  category: "maintenance",
  params: [
    { name: "session_id", type: "string", required: true, description: "ReportSessions.Id to rollback" },
    { name: "run_id", type: "string", required: false, default: "", description: "GitHub run id for audit trail" },
    { name: "attempt", type: "string", required: false, default: "", description: "GitHub run attempt number" },
    { name: "run_started_at", type: "string", required: false, default: "", description: "ISO timestamp lower bound" },
    { name: "scope", type: "choice", required: false, default: "all", choices: ["all", "reports", "operations", "history"] },
    { name: "dry_run", type: "boolean", required: false, default: true },
    { name: "force", type: "boolean", required: false, default: false, description: "Allow rollback of committed sessions" },
    { name: "log_level", type: "choice", required: false, default: "INFO", choices: ["DEBUG", "INFO", "WARNING", "ERROR"] },
    { name: "runner", type: "choice", required: false, default: "self-hosted", choices: ["ubuntu-latest", "self-hosted"] },
    { name: "confirm_production", type: "string", required: false, default: "", description: 'Type "I-UNDERSTAND" for non-dry-run or force' },
  ],
  safetyGate: {
    field: "confirm_production",
    requiredValue: "I-UNDERSTAND",
    triggerWhen: { dry_run: false },
  },
});

export function getWorkflowSchema(filename: string): WorkflowEntry | undefined {
  return WORKFLOW_REGISTRY.get(filename);
}

export function validateWorkflowInputs(
  filename: string,
  inputs: Record<string, string>,
): { valid: boolean; errors: string[] } {
  const schema = WORKFLOW_REGISTRY.get(filename);
  if (!schema) return { valid: true, errors: [] };

  const errors: string[] = [];

  for (const param of schema.params) {
    if (param.required && !(param.name in inputs)) {
      errors.push(`Missing required parameter: ${param.name}`);
    }
    if (param.type === "choice" && param.name in inputs && param.choices) {
      if (!param.choices.includes(inputs[param.name])) {
        errors.push(`Invalid value for ${param.name}: '${inputs[param.name]}'. Allowed: ${param.choices.join(", ")}`);
      }
    }
  }

  if (schema.safetyGate) {
    const gate = schema.safetyGate;
    let gateTriggered = false;
    for (const [key, value] of Object.entries(gate.triggerWhen)) {
      if (inputs[key] === String(value)) {
        gateTriggered = true;
        break;
      }
    }
    if (inputs.force === "true") {
      gateTriggered = true;
    }
    if (gateTriggered && inputs[gate.field] !== gate.requiredValue) {
      errors.push(`Safety gate: ${gate.field} must be "${gate.requiredValue}" when ${Object.entries(gate.triggerWhen).map(([k, v]) => `${k}=${v}`).join(" or ")} (or force=true)`);
    }
  }

  return { valid: errors.length === 0, errors };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/workflow-registry.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/workflow-registry.ts server/__tests__/workflow-registry.test.ts
git commit -m "feat(gh-actions): add workflow parameter schema registry for 5 workflows"
```

---

## Task 4: Schema Endpoint + Dispatch Validation in gh-actions.ts

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/gh-actions.ts:1-157`

- [ ] **Step 1: Write the failing test**

Add to `server/__tests__/gh-actions-routes.test.ts` (append to existing test file). If the test file doesn't seed the necessary env, use the same `getToken()` helper pattern:

```typescript
// Add these tests to the existing describe block

it("GET /api/gh-actions/workflows/:name/schema returns schema for registered workflow", async () => {
  const token = await getToken();
  const res = await app.request(
    "/api/gh-actions/workflows/WeeklyDedup.yml/schema",
    { headers: { Authorization: `Bearer ${token}` } },
    env,
  );
  expect(res.status).toBe(200);
  const data = (await res.json()) as any;
  expect(data.filename).toBe("WeeklyDedup.yml");
  expect(data.displayName).toBe("Weekly Dedup");
  expect(Array.isArray(data.params)).toBe(true);
  expect(data.params.length).toBe(8);
});

it("GET /api/gh-actions/workflows/:name/schema returns 404 for unregistered workflow", async () => {
  const token = await getToken();
  const res = await app.request(
    "/api/gh-actions/workflows/NotReal.yml/schema",
    { headers: { Authorization: `Bearer ${token}` } },
    env,
  );
  expect(res.status).toBe(404);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/gh-actions-routes.test.ts`
Expected: FAIL — `/schema` route does not exist, returns 404 or falls through.

- [ ] **Step 3: Add schema endpoint and dispatch validation**

In `server/routes/gh-actions.ts`, add imports:

```typescript
import { getWorkflowSchema, validateWorkflowInputs } from "../services/workflow-registry";
```

Add the schema endpoint before the existing `GET /workflows/:name` route (around line 118). **Important:** This route must be registered before the generic `GET /workflows/:name` to avoid Hono matching `:name` = "WeeklyDedup.yml" and then failing on the sub-path `/schema`:

```typescript
// GET /workflows/:name/schema — get workflow parameter schema
ghActionsRoutes.get("/workflows/:name/schema", async (c) => {
  const name = c.req.param("name");
  const schema = getWorkflowSchema(name);
  if (!schema) {
    throw new HTTPException(404, {
      message: JSON.stringify({
        error: { code: "workflow.no_schema", message: `No schema registered for '${name}'` },
      }),
    });
  }
  return c.json(schema);
});
```

Update the `POST /runs` handler (line 78) to add input validation:

After `if (!body.workflow)` check, add:

```typescript
  const validation = validateWorkflowInputs(body.workflow, body.inputs ?? {});
  if (!validation.valid) {
    throw new HTTPException(422, {
      message: JSON.stringify({
        error: {
          code: "workflow.invalid_inputs",
          message: "Workflow input validation failed",
          details: validation.errors,
        },
      }),
    });
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/gh-actions-routes.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/gh-actions.ts server/__tests__/gh-actions-routes.test.ts
git commit -m "feat(gh-actions): add workflow schema endpoint and dispatch input validation"
```

---

## Task 5: Rollback Parameter Completion

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/sessions.ts:221-271`
- Modify: `JAVDB_AutoSpider_Web/server/__tests__/sessions-routes.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `server/__tests__/sessions-routes.test.ts`:

```typescript
describe("POST /api/sessions/:id/rollback — full parameters", () => {
  it("forwards scope and force to GH Actions dispatch", async () => {
    // Seed a session first
    await env.REPORTS_DB.prepare(
      `CREATE TABLE IF NOT EXISTS ReportSessions (
        Id TEXT PRIMARY KEY, Status TEXT, WriteMode TEXT, RunId TEXT, RunAttempt INTEGER,
        DateTimeCreated TEXT, ReportType TEXT, ReportDate TEXT, FailureReason TEXT, CommittedAt TEXT
      )`,
    ).run();
    await env.REPORTS_DB.prepare(
      `INSERT OR IGNORE INTO ReportSessions (Id, Status, DateTimeCreated, ReportType, ReportDate)
       VALUES ('rollback-test-001', 'committed', datetime('now'), 'daily', '2026-05-24')`,
    ).run();

    const { accessToken, csrfToken } = await login();
    const res = await app.request(
      "/api/sessions/rollback-test-001/rollback",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          scope: "history",
          force: true,
          dry_run: true,
          log_level: "DEBUG",
          runner: "ubuntu-latest",
        }),
      },
      env,
    );
    // dry_run=true should dispatch a real GH Actions dry-run
    // In test env GH Actions may not be configured, so expect 503 or success
    // The key thing is the endpoint accepts all parameters without error
    expect([200, 503]).toContain(res.status);
  });

  it("requires confirm_production for non-dry-run", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request(
      "/api/sessions/rollback-test-001/rollback",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          dry_run: false,
          confirm_production: "",
        }),
      },
      env,
    );
    expect(res.status).toBe(422);
  });

  it("requires confirm_production for force=true", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request(
      "/api/sessions/rollback-test-001/rollback",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          dry_run: true,
          force: true,
          confirm_production: "",
        }),
      },
      env,
    );
    expect(res.status).toBe(422);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/sessions-routes.test.ts`
Expected: FAIL — `confirm_production` validation doesn't exist, endpoint doesn't accept `scope`/`force`/etc.

- [ ] **Step 3: Replace the rollback handler**

In `server/routes/sessions.ts`, add import:

```typescript
import { validateWorkflowInputs } from "../services/workflow-registry";
import { createJobRunsRepo } from "../services/job-runs";
```

Replace the entire rollback handler (lines 221–270) with:

```typescript
// POST /:session_id/rollback — rollback a session (dispatches GH Actions workflow)
sessionsRoutes.post("/:session_id/rollback", requireRole("admin"), async (c) => {
  const sessionId = c.req.param("session_id");

  const body = await c.req.json<{
    scope?: string;
    force?: boolean;
    dry_run?: boolean;
    confirm_production?: string;
    log_level?: string;
    runner?: string;
  }>().catch(() => ({} as Record<string, never>));

  const session = await c.env.REPORTS_DB
    .prepare("SELECT Id FROM ReportSessions WHERE Id = ?")
    .bind(sessionId)
    .first<{ Id: string }>();

  if (!session) {
    throw new HTTPException(404, {
      message: JSON.stringify({ error: { code: "session.not_found" } }),
    });
  }

  const inputs: Record<string, string> = {
    session_id: sessionId,
    scope: body.scope ?? "all",
    dry_run: String(body.dry_run ?? true),
    force: String(body.force ?? false),
    confirm_production: body.confirm_production ?? "",
    log_level: body.log_level ?? "INFO",
    runner: body.runner ?? "self-hosted",
  };

  const validation = validateWorkflowInputs("RollbackD1.yml", inputs);
  if (!validation.valid) {
    throw new HTTPException(422, {
      message: JSON.stringify({
        error: {
          code: "rollback.invalid_inputs",
          message: "Rollback validation failed",
          details: validation.errors,
        },
      }),
    });
  }

  if (!isGhActionsConfigured(c.env)) {
    throw new HTTPException(503, {
      message: "GitHub Actions not configured",
    });
  }

  const gh = createGhClient({
    token: c.env.GH_ACTIONS_TOKEN!,
    repo: c.env.GH_ACTIONS_REPO!,
  });
  await gh.dispatchWorkflow("RollbackD1.yml", inputs);

  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  const job = await repo.create("rollback", "RollbackD1.yml", inputs);

  return c.json({
    session_id: sessionId,
    dry_run: inputs.dry_run === "true",
    job_id: job.job_id,
    actions: [{ type: "dispatched", workflow: "RollbackD1.yml", inputs }],
    summary: { dispatched: true },
  });
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/sessions-routes.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/sessions.ts server/__tests__/sessions-routes.test.ts
git commit -m "feat(rollback): forward all RollbackD1.yml parameters with safety gate validation"
```

---

## Task 6: Onboarding Test via GH Actions Dispatch

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/onboarding.ts:50-69`
- Modify: `JAVDB_AutoSpider_Web/server/__tests__/onboarding-routes.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `server/__tests__/onboarding-routes.test.ts`:

```typescript
describe("POST /api/onboarding/test — GH Actions dispatch", () => {
  it("returns unavailable for qb when GH Actions not configured", async () => {
    const { accessToken } = await login();
    const res = await app.request(
      "/api/onboarding/test",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ component: "qb" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    // In test env, GH_ACTIONS_TOKEN is not set → unavailable
    expect(data.component).toBe("qb");
    expect(["dispatched", "unavailable"]).toContain(data.status);
  });

  it("returns unavailable for smtp (no dedicated workflow)", async () => {
    const { accessToken } = await login();
    const res = await app.request(
      "/api/onboarding/test",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ component: "smtp" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.component).toBe("smtp");
    expect(data.status).toBe("unavailable");
  });

  it("javdb test still works synchronously", async () => {
    const { accessToken } = await login();
    const res = await app.request(
      "/api/onboarding/test",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ component: "javdb" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.component).toBe("javdb");
    // No cookie set in test env → ok: false
    expect(typeof data.ok).toBe("boolean");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/onboarding-routes.test.ts`
Expected: FAIL — `status` field not in response, qb test returns `ok: false` not `status: "unavailable"`.

- [ ] **Step 3: Update onboarding test handler**

In `server/routes/onboarding.ts`, add imports:

```typescript
import { createGhClient } from "../services/gh-client";
import { createJobRunsRepo } from "../services/job-runs";
```

Replace the `testComponent` function and the `POST /test` handler with:

```typescript
function isGhActionsConfigured(env: Env): boolean {
  return (
    !!env.GH_ACTIONS_TIER &&
    env.GH_ACTIONS_TIER !== "none" &&
    !!env.GH_ACTIONS_TOKEN &&
    !!env.GH_ACTIONS_REPO
  );
}

type TestableComponent = "javdb" | "qb" | "proxy" | "smtp";

const COMPONENT_WORKFLOW_MAP: Record<string, { workflow: string; inputs: Record<string, string> } | null> = {
  qb: { workflow: "TestIngestion.yml", inputs: { runner: "self-hosted" } },
  proxy: { workflow: "TestIngestion.yml", inputs: { runner: "self-hosted", proxy_spider: "true" } },
  smtp: null,
};

async function testComponent(
  component: TestableComponent,
  config: Record<string, unknown>,
  env: Env,
): Promise<{ ok?: boolean; status?: string; message: string; details: Record<string, unknown> | null }> {
  switch (component) {
    case "javdb": {
      const cookie = config.JAVDB_SESSION_COOKIE;
      if (!cookie) return { ok: false, message: "JAVDB_SESSION_COOKIE not set", details: null };
      return { ok: true, message: "cookie present", details: { length: String(cookie).length } };
    }
    case "qb":
    case "proxy": {
      const mapping = COMPONENT_WORKFLOW_MAP[component];
      if (!mapping) {
        return { status: "unavailable", message: `${component} connectivity test workflow not yet available`, details: null };
      }
      if (!isGhActionsConfigured(env)) {
        return { status: "unavailable", message: "GitHub Actions not configured", details: null };
      }
      const gh = createGhClient({ token: env.GH_ACTIONS_TOKEN!, repo: env.GH_ACTIONS_REPO! });
      const repo = createJobRunsRepo(env.OPERATIONS_DB);
      const job = await repo.create(`test-${component}`, mapping.workflow, mapping.inputs);
      await gh.dispatchWorkflow(mapping.workflow, mapping.inputs);
      return {
        status: "dispatched",
        message: `Dispatched ${mapping.workflow} for ${component} connectivity test`,
        details: { job_id: job.job_id, poll_url: `/api/tasks/${job.job_id}` },
      };
    }
    case "smtp":
      return { status: "unavailable", message: "SMTP connectivity test workflow not yet available", details: null };
    default:
      return { ok: false, message: `Unknown component: ${component}`, details: null };
  }
}
```

Update the `POST /test` handler to pass `env`:

Replace:

```typescript
  const result = await testComponent(body.component as TestableComponent, config);
```

with:

```typescript
  const result = await testComponent(body.component as TestableComponent, config, c.env);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/onboarding-routes.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/onboarding.ts server/__tests__/onboarding-routes.test.ts
git commit -m "feat(onboarding): replace 501 stubs with GH Actions dispatch for connectivity tests"
```

---

## Task 7: Init-Once Table Initialization

**Files:**
- Create: `JAVDB_AutoSpider_Web/server/services/table-init.ts`
- Modify: `JAVDB_AutoSpider_Web/server/app.ts`
- Modify: `JAVDB_AutoSpider_Web/server/routes/operations.ts`
- Modify: `JAVDB_AutoSpider_Web/server/routes/tasks.ts`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/table-init.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/table-init.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import { initializeTables } from "../services/table-init";

describe("initializeTables", () => {
  it("creates job_runs table in OPERATIONS_DB", async () => {
    await initializeTables(env);

    const result = await env.OPERATIONS_DB.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='job_runs'",
    ).first<{ name: string }>();
    expect(result?.name).toBe("job_runs");
  });

  it("creates system_state table in OPERATIONS_DB", async () => {
    await initializeTables(env);

    const result = await env.OPERATIONS_DB.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='system_state'",
    ).first<{ name: string }>();
    expect(result?.name).toBe("system_state");
  });

  it("is idempotent — running twice does not error", async () => {
    await initializeTables(env);
    await initializeTables(env);
    // No error thrown
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/table-init.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement table-init.ts**

Create `server/services/table-init.ts`:

```typescript
import type { Env } from "../env";

export async function initializeTables(env: Env): Promise<void> {
  await env.OPERATIONS_DB.batch([
    env.OPERATIONS_DB.prepare(
      `CREATE TABLE IF NOT EXISTS job_runs (
        job_id       TEXT PRIMARY KEY,
        workflow     TEXT NOT NULL,
        gh_run_id    INTEGER,
        status       TEXT NOT NULL DEFAULT 'dispatched',
        inputs       TEXT,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
      )`,
    ),
    env.OPERATIONS_DB.prepare(
      `CREATE TABLE IF NOT EXISTS system_state (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      )`,
    ),
    env.OPERATIONS_DB.prepare(
      `CREATE TABLE IF NOT EXISTS api_config (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      )`,
    ),
  ]);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/table-init.test.ts`
Expected: PASS

- [ ] **Step 5: Add init middleware to app.ts**

In `server/app.ts`, add import:

```typescript
import { initializeTables } from "./services/table-init";
```

Add the init middleware after the CORS middleware (line 44) and before the public routes (line 46):

```typescript
let tablesInitialized = false;

app.use("/api/*", async (c, next) => {
  if (!tablesInitialized) {
    try {
      await initializeTables(c.env);
      tablesInitialized = true;
    } catch (e) {
      console.error("Table initialization failed, will retry on next request:", e);
    }
  }
  await next();
});
```

- [ ] **Step 6: Remove ensureTable() calls from operations.ts**

In `server/routes/operations.ts`, remove all 3 `await repo.ensureTable();` calls (lines 93, 128, 164).

Line 92–93:

```typescript
  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();  // DELETE THIS LINE
```

Line 127–128:

```typescript
  const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
  await repo.ensureTable();  // DELETE THIS LINE
```

Line 163–164:

```typescript
    const repo = createJobRunsRepo(c.env.OPERATIONS_DB);
    await repo.ensureTable();  // DELETE THIS LINE
```

- [ ] **Step 7: Remove ensureTable() calls from tasks.ts**

In `server/routes/tasks.ts`, remove all 6 `await repo.ensureTable();` calls (lines 90, 133, 159, 174, 196 area, 206).

Search for all lines containing `ensureTable` and delete each one. The `createJobRunsRepo()` calls remain — only the `ensureTable()` calls are removed.

- [ ] **Step 8: Run full test suite**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run`
Expected: All tests pass. The init middleware ensures tables exist before any route handler runs.

- [ ] **Step 9: Commit**

```bash
git add server/services/table-init.ts server/app.ts server/routes/operations.ts server/routes/tasks.ts server/__tests__/table-init.test.ts
git commit -m "perf(init): replace per-request ensureTable with cold-start-only initialization"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [ ] Workflow schema registry for 5 workflows (Task 3)
- [ ] Schema endpoint `GET /workflows/:name/schema` (Task 4)
- [ ] Dispatch input validation with safety gates (Task 4)
- [ ] Rollback full parameter forwarding (Task 5)
- [ ] Rollback safety gate (`confirm_production`) (Task 5)
- [ ] Onboarding test GH Actions dispatch (Task 6)
- [ ] Onboarding graceful degradation (Task 6)
- [ ] Unified cursor encoding (Tasks 1–2)
- [ ] `ensureTable()` init-once middleware (Task 7)

**2. Placeholder scan:** No TBD, TODO, or "fill in later" found.

**3. Type consistency:** `cursorEncode({id})` / `cursorDecode<{id: number}>()` used consistently. `WorkflowEntry` and `WorkflowParam` types match between registry, tests, and endpoint response. `validateWorkflowInputs()` used in both `gh-actions.ts` dispatch and `sessions.ts` rollback.

**4. Observations:**
- The `ensureTable()` method on `JobRunsRepo` is NOT deleted from `job-runs.ts` — it's still available for ad-hoc use or tests. Only the per-request calls in route handlers are removed.
- The `system_state` and `api_config` tables are included in `initializeTables()` because `onboarding.ts` and `config-store.ts` depend on them existing.
