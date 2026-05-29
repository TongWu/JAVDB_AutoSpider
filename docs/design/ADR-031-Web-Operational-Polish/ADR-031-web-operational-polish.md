# ADR-020: Web Backend Operational Polish — Workflow UI, Rollback, Onboarding & Architecture

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted                                                              |
| **Date**    | 2026-05-24                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), [ADR-018](../ADR-018-Web-Security-Hardening/ADR-018-web-security-hardening.md), [ADR-019](../ADR-019-Web-Feature-Parity/ADR-019-web-feature-parity.md) |

## Context

After ADR-018 (security hardening) and ADR-019 (feature parity), a final audit pass identified five architecture-level improvements that reduce operational friction and complete the web UI's coverage of the system's operational surface:

1. **Missing workflow UI dispatch** — Three GitHub Actions workflows (`WeeklyDedup`, `Migration`, `TestIngestion`) cannot be triggered from the web UI. The existing `POST /api/gh-actions/runs` endpoint can dispatch any workflow, but the frontend has no way to know each workflow's parameter schema, types, defaults, or validation rules.

2. **Incomplete rollback parameters** — `RollbackD1.yml` accepts 10 parameters (`scope`, `force`, `confirm_production`, `log_level`, `runner`, etc.), but the TS backend's `POST /sessions/:id/rollback` only forwards `session_id`. Users must use GitHub's UI directly for advanced rollback options. The `dry_run` flag returns mock data instead of dispatching a real dry-run to GH Actions.

3. **Onboarding test 501 stubs** — `POST /onboarding/test` returns 501 for qBittorrent, proxy, and SMTP connectivity tests in Cloudflare mode. Workers can't open TCP sockets to these services, but they can dispatch GH Actions workflows to test connectivity asynchronously.

4. **Inconsistent cursor encoding** — `sessions.ts` encodes cursors as `base64(JSON.stringify({sid}))` while `history.ts` uses `base64(String(id))`. Both use keyset pagination (`Id < ?`) but the encoding format differs, making client-side cursor handling inconsistent.

5. **`ensureTable()` per-request overhead** — `operations.ts` (3 calls) and `tasks.ts` (6 calls) run `CREATE TABLE IF NOT EXISTS` on every request, adding ~20ms of D1 round-trip latency to every API call. The table existence check should happen once per Worker isolate lifecycle, not per request.

## Decision

### 1. Workflow Parameter Schema Registry

Create a `workflow-registry.ts` module that defines each workflow's dispatch parameters as structured metadata:

```typescript
interface WorkflowParam {
  name: string;
  type: "string" | "boolean" | "choice";
  required: boolean;
  default?: string | boolean;
  choices?: string[];
  description?: string;
}

interface WorkflowEntry {
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
```

**New endpoint:** `GET /api/gh-actions/workflows/:name/schema` — returns the `WorkflowEntry` for the specified workflow. The frontend renders a dynamic form from this schema.

**Registered workflows (initial set):**

| Workflow | Params | Safety Gate |
| -------- | ------ | ----------- |
| `WeeklyDedup.yml` | 8 | `confirm_production = "I-UNDERSTAND"` when `dry_run = false` |
| `Migration.yml` | 21 | `confirm_production = "I-UNDERSTAND"` when `dry_run = false` |
| `TestIngestion.yml` | 2 | None |
| `RollbackD1.yml` | 10 | `confirm_production = "I-UNDERSTAND"` when `dry_run = false` or `force = true` |

**Validation on dispatch:** `POST /api/gh-actions/runs` validates inputs against the registered schema before dispatching. Safety gates are enforced server-side — the frontend cannot bypass them.

**Already-dispatchable workflows** (`DailyIngestion.yml`, `AdHocIngestion.yml`, `QBFileFilter.yml`, `StaleSessionCleanup.yml`) can be added to the registry later. They already work via the generic dispatch endpoint.

### 2. Rollback Parameter Completion

Update `POST /api/sessions/:id/rollback` to accept and forward all `RollbackD1.yml` parameters:

**Request body (all optional except `session_id` from URL):**

```json
{
  "scope": "all",
  "force": false,
  "dry_run": true,
  "confirm_production": "",
  "log_level": "INFO",
  "runner": "self-hosted"
}
```

**Behavior changes:**
- `dry_run: true` now dispatches a real GH Actions dry run (instead of returning mock data). The response includes the `job_id` for status polling.
- `dry_run: false` requires `confirm_production: "I-UNDERSTAND"`. Validated server-side.
- `force: true` allows rollback of committed sessions. Also requires `confirm_production`.
- `scope` restricts rollback to specific databases (`all`, `reports`, `operations`, `history`).

**Backward compatibility:** If no body is sent, defaults apply (`scope: "all"`, `dry_run: true`, `force: false`). Existing clients that only send `session_id` in the URL continue to work — they get a real dry-run dispatch instead of mock data.

### 3. Onboarding Test via GH Actions Dispatch

Replace 501 stubs with asynchronous GH Actions dispatch for connectivity tests:

**Flow:**
1. `POST /onboarding/test` with `{ component: "qb" }` → dispatch a connectivity-test workflow
2. Response: `{ status: "dispatched", job_id: "test-20260524-...", poll_url: "/api/gh-actions/runs/{run_id}" }`
3. Frontend polls `GET /api/gh-actions/runs/{run_id}` until completion
4. Completed run's logs contain pass/fail result

**Component-to-workflow mapping:**

| Component | Workflow | Inputs |
| --------- | -------- | ------ |
| `qb` | `TestIngestion.yml` | `{ runner: "self-hosted" }` |
| `proxy` | `TestIngestion.yml` | `{ runner: "self-hosted", proxy_spider: true }` |
| `smtp` | *(future: dedicated test workflow)* | — |
| `javdb` | Direct check (cookie length) | No dispatch needed |

**Graceful degradation:**
- If `GH_ACTIONS_TIER` is `"none"` or token is missing → return `{ status: "unavailable", reason: "GitHub Actions not configured" }` (HTTP 200, not 501)
- SMTP test remains unavailable until a dedicated workflow is created. Returns `{ status: "unavailable", reason: "SMTP connectivity test workflow not yet available" }`.

### 4. Unified Cursor Encoding

Extract a shared `server/services/cursor.ts` module:

```typescript
export function cursorEncode(values: Record<string, unknown>): string {
  return btoa(JSON.stringify(values));
}

export function cursorDecode<T = Record<string, unknown>>(cursor: string): T {
  return JSON.parse(atob(cursor)) as T;
}
```

**Migration:**
- `sessions.ts`: already uses JSON format `{sid}` — refactor to import from `cursor.ts`
- `history.ts`: change from `base64(String(id))` to `base64(JSON.stringify({id}))` — **breaking change** for any client holding old cursors

**Breaking change mitigation:** Old-format cursors (plain numeric base64) will fail to parse as JSON. The `cursorDecode` function catches this and returns a 400 error with a clear message: `"Invalid cursor format. Please reload the page."`. Since cursors are ephemeral (only valid for the current browsing session), this is acceptable.

### 5. `ensureTable()` Init-Once Middleware

Replace per-request `ensureTable()` calls with a once-per-isolate initialization:

**Init middleware in `app.ts`:**

```typescript
let tablesInitialized = false;

app.use("/api/*", async (c, next) => {
  if (!tablesInitialized) {
    await initializeTables(c.env);
    tablesInitialized = true;
  }
  await next();
});
```

**`initializeTables()` function** runs all `CREATE TABLE IF NOT EXISTS` statements in a single `db.batch()` call per database binding. This replaces the 9 individual `ensureTable()` calls scattered across `operations.ts` and `tasks.ts`.

**Worker isolate lifecycle:** Cloudflare recycles isolates after ~30 seconds of inactivity. The `tablesInitialized` flag resets on each cold start, so tables are checked once per isolate. For a personal project with low traffic, this means initialization runs a few times per day at most.

**Fallback:** If `initializeTables()` fails (e.g., D1 temporary unavailable), the flag stays `false` and retries on the next request.

## Out of Scope

- **Creating new workflow YAML files** (e.g., connectivity-test workflow for SMTP) — CI/Python-side work.
- **Frontend page changes** — The frontend renders workflow forms from `/api/gh-actions/workflows/:name/schema` dynamically. No manual page work needed.
- **Complex parameter business logic validation** — Only type/required/choices validation. No semantic validation (e.g., "is this rclone path valid").
- **Python backend changes** — This ADR targets the TS backend only.
- **Adding already-working workflows to the registry** — `DailyIngestion`, `AdHocIngestion`, etc. already dispatch fine. Registry entries for them are future work.

## Consequences

### Positive

- All 20 GitHub Actions workflows become triggerable from the web UI (4 new + existing).
- Rollback operations gain full parameter control — `scope`, `force`, and real dry-run dispatch.
- Onboarding tests degrade to async dispatch instead of 501 errors — functional in Cloudflare mode.
- Cursor encoding is consistent across all paginated endpoints — simpler client-side handling.
- Cold-start-only table initialization eliminates ~20ms per-request overhead on hot paths.

### Negative

- Workflow registry is manually maintained — must be updated when workflow YAML changes. Acceptable for a project with ~20 workflows that change infrequently.
- Onboarding tests become asynchronous — slower UX (minutes vs. seconds for a direct connectivity check). Inherent limitation of Cloudflare Workers.
- Cursor format change is a breaking change for `history.ts` — existing client cursors become invalid. Mitigated by the ephemeral nature of cursors.
- Init middleware adds a cold-start penalty (~50ms for batched table creation) — acceptable since it replaces ~180ms of per-request overhead (9 calls × ~20ms).

### Risks

- **Workflow registry drift** — If a workflow YAML adds/removes parameters but the registry isn't updated, the UI will show stale options. Mitigation: document the "update registry" step in the workflow modification checklist.
- **GH Actions rate limiting** — Dispatching connectivity tests counts against the GH Actions API rate limit (1,000 requests/hour for authenticated users). Acceptable for a personal project with 1-2 users.
- **`tablesInitialized` false positive** — If a table is dropped after initialization, subsequent requests won't re-create it. Extremely unlikely in production; Worker restart resets the flag.
