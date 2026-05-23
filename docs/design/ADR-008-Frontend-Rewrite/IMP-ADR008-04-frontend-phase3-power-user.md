# IMP-ADR008-04: Frontend Rewrite — Phase 3: Power-User Features & Analytics

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver GH Actions advanced tiers (`edit`/`admin`), Migrations UI, global log search, and statistics dashboard — completing the full frontend surface.

**Architecture:** See [ADR-008](ADR-008-frontend-rewrite-architecture.md) for all architectural decisions.

**Tech Stack:** Same as Phase 2. Charts: `vue-chartjs` (Chart.js wrapper), lazy-loaded on statistics route.

**Source spec:** `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md` §6.2 Phase 3, §8.4 Phase 3

**Related:** [ADR-008](ADR-008-frontend-rewrite-architecture.md), [IMP-ADR008-02](IMP-ADR008-02-frontend-phase1-completion.md) (Phase 1), [IMP-ADR008-03](IMP-ADR008-03-frontend-phase2-full-cli-coverage.md) (Phase 2)

**Prerequisites:** Phase 2 complete ([IMP-ADR008-03](IMP-ADR008-03-frontend-phase2-full-cli-coverage.md)). GH Actions monitor tier functional.

**Timing:** "When needed" — Phase 3 ships incrementally based on operator demand after Phase 2 dogfooding.

---

## Design Status

| Feature | Design status | Notes |
|---------|--------------|-------|
| GH Actions `edit` tier | **BE done** / FE specified | YAML editor endpoint implemented + tested |
| GH Actions `admin` tier | **BE done** / FE specified | Secrets CRUD endpoints implemented + tested |
| Migrations UI | **BE done** / FE specified | List + dry-run endpoints implemented + tested |
| Global log search | **BE done** / FE specified | File grep endpoint + 20 tests — see Task 4 |
| Statistics dashboard | **BE done** / FE specified | Summary + trend endpoints + 27 tests — see Task 5 |

Tasks 1–5 backend is complete (endpoints + unit tests committed). All FE tasks are specified and ready for implementation in the web repo.

---

## Endpoints

### Implemented (10 endpoints — BE done, FE pending)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/gh-actions/workflows/{name}` | Get workflow YAML content + SHA | `edit` |
| `PUT` | `/api/gh-actions/workflows/{name}` | Edit workflow YAML | `edit` |
| `GET` | `/api/gh-actions/secrets` | List secret names + updated_at (values opaque) | `admin` |
| `POST` | `/api/gh-actions/secrets` | Create or update a secret | `admin` |
| `DELETE` | `/api/gh-actions/secrets/{name}` | Delete a secret | `admin` |
| `GET` | `/api/migrations/` | List migrations + applied state | `admin` |
| `POST` | `/api/migrations/{id}/run` | Preview or run a single migration | `admin` |
| `GET` | `/api/logs/search?q=&job_id=&date_from=&date_to=&limit=100` | Search task logs via file grep | `admin` |
| `GET` | `/api/stats/summary` | Aggregated metrics snapshot | any authenticated |
| `GET` | `/api/stats/trend?metric=&period=7d\|30d\|90d` | Time-series data for a single metric | any authenticated |

---

## Task 1: GH Actions — Workflow YAML Editor (Tier `edit`)

**Files:**
- Modify: `javdb/integrations/gh_actions/client.py` (main repo — add `get_workflow_content`, `update_workflow_content`)
- Modify: `apps/api/routers/gh_actions.py` (main repo)
- Modify: `apps/api/schemas/gh_actions.py` (main repo)
- Create: `src/pages/gh-actions/WorkflowEditorPage.vue` (web repo)

**Endpoint: `PUT /api/gh-actions/workflows/{name}`**

Request:
```json
{
  "content": "name: CI\non: push\njobs: ...",
  "commit_message": "ci: update CI workflow",
  "branch": "main"
}
```

Response:
```json
{
  "updated": true,
  "commit_sha": "abc123",
  "validation_warnings": []
}
```

**Implementation notes:**
- BE reads current file via GitHub Contents API: `GET /repos/{repo}/contents/.github/workflows/{name}`
- BE validates YAML syntax before committing (Python `yaml.safe_load` — reject if parse fails)
- BE writes via GitHub Contents API: `PUT /repos/{repo}/contents/.github/workflows/{name}` with base64-encoded content + SHA of current file (optimistic concurrency)
- Branch protection may reject — surface the GitHub API error clearly

- [x] **Step 1: Add GitHub Contents API methods to client.** _(Done)_

  Implemented in `javdb/integrations/gh_actions/client.py`: `get_workflow_content()` and `update_workflow_content()`.

- [x] **Step 2: Implement PUT endpoint + GET content endpoint with YAML validation.** _(Done)_

  Implemented in `apps/api/routers/gh_actions.py`:
  - `GET /api/gh-actions/workflows/{name}` — returns decoded YAML content + SHA (tier `edit`)
  - `PUT /api/gh-actions/workflows/{name}` — validates YAML, commits via GitHub Contents API (tier `edit`)
  - Workflow name validated against `_SAFE_WORKFLOW_NAME_RE = re.compile(r"^[\w\-\.]+\.ya?ml$")`

- [x] **Step 3: Write unit tests.** _(Done)_

  Tests in `tests/unit/test_gh_actions_client.py` and `tests/unit/test_gh_actions_endpoints.py`.

- [ ] **Step 4: Build FE WorkflowEditorPage.**

  **File:** `src/pages/gh-actions/WorkflowEditorPage.vue` (web repo, new)

  **Route:** Add to `src/router/routes.ts`:
  ```typescript
  {
    path: '/gh-actions/workflows',
    name: 'gh-actions-workflows',
    component: () => import('@/pages/gh-actions/WorkflowEditorPage.vue'),
    meta: { requiresAuth: true },
  }
  ```

  **Sidebar:** Add to `routeMap` in `src/components/layout/Sidebar.vue`:
  ```typescript
  workflows: '/gh-actions/workflows',
  ```
  (Menu item `workflows` already exists in sidebar options under GitHub Actions group.)

  **API:** Add to `src/api/gh-actions.ts`:
  ```typescript
  export interface WorkflowContentResponse {
    content: string
    sha: string
    path: string
  }
  export interface WorkflowUpdateRequest {
    content: string
    sha: string
    commit_message: string
    branch?: string
  }
  export interface WorkflowUpdateResponse {
    updated: boolean
    commit_sha: string
    validation_warnings: string[]
  }

  export async function getWorkflowContent(name: string): Promise<WorkflowContentResponse> {
    const { data } = await http.get<WorkflowContentResponse>(
      `/api/gh-actions/workflows/${encodeURIComponent(name)}`
    )
    return data
  }

  export async function updateWorkflow(
    name: string, body: WorkflowUpdateRequest
  ): Promise<WorkflowUpdateResponse> {
    const { data } = await http.put<WorkflowUpdateResponse>(
      `/api/gh-actions/workflows/${encodeURIComponent(name)}`, body
    )
    return data
  }
  ```

  **Layout:**
  - Left panel: workflow file list (reuse `listWorkflows()`, show `filename` column). Click selects a workflow.
  - Center: `<NInput type="textarea">` with monospace font, loaded from `getWorkflowContent(name)`. No Monaco — keep bundle small.
  - Bottom bar: `<NInput>` for commit message (placeholder: `ci: update <filename>`) + `<NButton>` "Save". On save, call `updateWorkflow()` with current SHA for optimistic concurrency.
  - Error handling: 409 (SHA conflict) → show "File changed since loading, please reload". 422 (invalid YAML) → inline error message.
  - Visibility: only render when `ghTier` is `edit` or `admin`. Redirect to `/403` otherwise.

- [ ] **Step 5: Commit web repo.**

  ```bash
  git commit -m "feat(fe): add Workflow YAML editor page (GH Actions tier edit)"
  ```

---

## Task 2: GH Actions — Secrets CRUD (Tier `admin`)

**Files:**
- Modify: `javdb/integrations/gh_actions/client.py` (main repo)
- Modify: `apps/api/routers/gh_actions.py` (main repo)
- Create: `src/pages/gh-actions/SecretsPage.vue` (web repo)

**Endpoints:**

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `GET` | `/api/gh-actions/secrets` | — | `{ secrets: [{ name, created_at, updated_at }] }` |
| `POST` | `/api/gh-actions/secrets` | `{ name, value }` | `{ created: true }` |
| `DELETE` | `/api/gh-actions/secrets/{name}` | — | `{ deleted: true }` |

**GitHub API detail:** Secrets must be encrypted with the repo's public key before upload. The flow:
1. `GET /repos/{repo}/actions/secrets/public-key` → `{ key_id, key }` (base64 NaCl public key)
2. Encrypt value with `libsodium` sealed box using the public key
3. `PUT /repos/{repo}/actions/secrets/{name}` with `{ encrypted_value, key_id }`

Python library: `PyNaCl` (`nacl.public.SealedBox`).

- [x] **Step 1: Add secrets methods to GitHub client.** _(Done)_

  Implemented in `javdb/integrations/gh_actions/client.py`: `list_secrets()`, `create_or_update_secret()` (NaCl sealed box encryption via `PyNaCl`), `delete_secret()`.

- [x] **Step 2: Implement router endpoints.** _(Done)_

  Implemented in `apps/api/routers/gh_actions.py`:
  - `GET /api/gh-actions/secrets` — list secret names + timestamps (tier `admin`)
  - `POST /api/gh-actions/secrets` — create/update with NaCl encryption (tier `admin`)
  - `DELETE /api/gh-actions/secrets/{name}` — delete a secret (tier `admin`)
  - Secret name validated against `_SAFE_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")`

- [x] **Step 3: Write unit tests.** _(Done)_

  Tests in `tests/unit/test_gh_actions_client.py` (NaCl encrypt/decrypt round-trip) and `tests/unit/test_gh_actions_endpoints.py` (all tier/role/CRUD/error scenarios).

- [ ] **Step 4: Build FE SecretsPage.**

  **File:** `src/pages/gh-actions/SecretsPage.vue` (web repo, new)

  **Route:** Add to `src/router/routes.ts`:
  ```typescript
  {
    path: '/gh-actions/secrets',
    name: 'gh-actions-secrets',
    component: () => import('@/pages/gh-actions/SecretsPage.vue'),
    meta: { requiresAuth: true },
  }
  ```

  **Sidebar:** Add to `routeMap` in `src/components/layout/Sidebar.vue`:
  ```typescript
  secrets: '/gh-actions/secrets',
  ```
  (Menu item `secrets` already exists in sidebar options under GitHub Actions group.)

  **API:** Add to `src/api/gh-actions.ts`:
  ```typescript
  export interface SecretItem {
    name: string
    created_at: string
    updated_at: string
  }
  export interface SecretsResponse {
    secrets: SecretItem[]
  }
  export interface CreateSecretRequest {
    name: string
    value: string
  }
  export interface CreateSecretResponse {
    created: boolean
  }
  export interface DeleteSecretResponse {
    deleted: boolean
  }

  export async function listSecrets(): Promise<SecretsResponse> {
    const { data } = await http.get<SecretsResponse>('/api/gh-actions/secrets')
    return data
  }

  export async function createOrUpdateSecret(body: CreateSecretRequest): Promise<CreateSecretResponse> {
    const { data } = await http.post<CreateSecretResponse>('/api/gh-actions/secrets', body)
    return data
  }

  export async function deleteSecret(name: string): Promise<DeleteSecretResponse> {
    const { data } = await http.delete<DeleteSecretResponse>(
      `/api/gh-actions/secrets/${encodeURIComponent(name)}`
    )
    return data
  }
  ```

  **Layout:**
  - `<NAlert type="warning">` banner at top: "Secret values cannot be retrieved after saving. Only the name and timestamps are visible."
  - `<NDataTable>` with columns: Name, Created, Updated, Actions.
  - "Add Secret" `<NButton>` → `<NModal>` with `<NForm>`: name (`<NInput>`, `A-Z0-9_` pattern, uppercase), value (`<NInput type="textarea">` with `show-password-on="click"` for masking). On submit → `createOrUpdateSecret()`.
  - Row actions: "Update" (same modal, name field readonly) + "Delete" (`<NPopconfirm>` → `deleteSecret()`).
  - Visibility: only render when `ghTier` is `admin`. Redirect to `/403` otherwise.
  - On success: `useMessage().success(...)` + reload table.

- [ ] **Step 5: Commit web repo.**

  ```bash
  git commit -m "feat(fe): add GitHub Actions Secrets management page (tier admin)"
  ```

---

## Task 3: Migrations UI

**Files:**
- Create: `apps/api/routers/migrations.py` (main repo)
- Create: `apps/api/schemas/migrations.py` (main repo)
- Modify: `apps/api/server.py` (main repo)
- Create: `src/pages/migrations/MigrationsPage.vue` (web repo)

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/migrations` | List D1 SQL migration files + applied state |
| `POST` | `/api/migrations/{id}/run` | Run a single migration |

**Implementation notes:**
- Migration files live in `javdb/migrations/d1/*.sql`, numbered sequentially (e.g., `0001_initial.sql`, `0018_email_notification_history.sql`)
- Applied state tracked in D1's `d1_migrations` table (or Wrangler's internal tracking)
- `GET /api/migrations` scans the directory, cross-references with applied state, returns: `[{ id: "0018", filename, applied: bool, applied_at }]`
- `POST /api/migrations/{id}/run` executes the SQL file against the active storage backend. **Safety gate**: require `dry_run=true` by default, show the SQL preview, then re-submit with `dry_run=false`.

- [x] **Step 1: Implement migration list endpoint.** _(Done)_

  Implemented in `apps/api/routers/migrations.py`:
  - `GET /api/migrations/` — scans `javdb/migrations/d1/*.sql`, cross-references with `system_state` table in `operations.db` for applied state
  - Migration ID is the full stem (e.g. `0042_system_state_table`), not just the prefix number
  - Gated on `require_role("admin")`

- [x] **Step 2: Implement run migration endpoint.** _(Done)_

  Implemented in `apps/api/routers/migrations.py`:
  - `POST /api/migrations/{migration_id}/run` — path traversal blocked via `resolve().relative_to()`
  - `dry_run=true` (default) returns SQL preview + statement count
  - `dry_run=false` returns 501 with instruction to use Wrangler CLI (remote execution deferred)
  - Schemas in `apps/api/schemas/migrations.py`

- [x] **Step 3: Write unit tests.** _(Done)_

  12 tests in `tests/unit/test_migrations_endpoints.py` covering admin/readonly/anon access, listing, applied state, dry-run preview, 501 for non-dry-run, 404, path traversal.

- [ ] **Step 4: Build FE MigrationsPage.**

  **File:** `src/pages/migrations/MigrationsPage.vue` (web repo, new)

  **Route:** Add to `src/router/routes.ts`:
  ```typescript
  {
    path: '/migrations',
    name: 'migrations',
    component: () => import('@/pages/migrations/MigrationsPage.vue'),
    meta: { requiresAuth: true, roles: ['admin'] },
  }
  ```

  **Sidebar:** Add new group to `options` in `src/components/layout/Sidebar.vue` (after Diagnostics, before Settings):
  ```typescript
  items.push({
    label: t('nav.migrations'),
    key: 'migrations',
    icon: () => '🗄️',
  })
  ```
  Add to `routeMap`:
  ```typescript
  migrations: '/migrations',
  ```
  Add i18n key `nav.migrations` to locale files.

  **API:** Create `src/api/migrations.ts` (web repo, new):
  ```typescript
  import { http } from './client'

  export interface MigrationItem {
    id: string
    filename: string
    applied: boolean
    applied_at: string | null
  }
  export interface MigrationsResponse {
    migrations: MigrationItem[]
  }
  export interface RunMigrationResponse {
    migration_id: string
    dry_run: boolean
    sql_preview: string
    statements: number
    applied?: boolean
  }

  export async function listMigrations(): Promise<MigrationsResponse> {
    const { data } = await http.get<MigrationsResponse>('/api/migrations/')
    return data
  }

  export async function runMigration(
    id: string, dryRun: boolean = true
  ): Promise<RunMigrationResponse> {
    const { data } = await http.post<RunMigrationResponse>(
      `/api/migrations/${encodeURIComponent(id)}/run`, { dry_run: dryRun }
    )
    return data
  }
  ```

  **Layout:**
  - Header: "Migrations" title + `<NBadge>` showing count of unapplied migrations.
  - `<NDataTable>` columns: Filename, Status (`<NTag type="success">Applied</NTag>` or `<NTag type="default">Pending</NTag>`), Applied At (formatted date or "—").
  - Row action for unapplied: "Preview" `<NButton>` → `<NDrawer>` showing SQL content in `<NCode>` or `<pre style="font-family: monospace">`. Drawer footer has "Apply" button (disabled — shows tooltip "Remote execution not yet supported. Use Wrangler CLI.").
  - Applied rows: no actions, read-only.
  - Admin-only page: redirect to `/403` if user role is not `admin`.

- [ ] **Step 5: Commit web repo.**

  ```bash
  git commit -m "feat(fe): add Migrations management page"
  ```

---

## Task 4: Global Log Search

**Design decisions (resolved 2026-05-23):**
1. **Storage:** File-system — logs already exist at `logs/jobs/{job_id}.log` with paired `.meta.json` metadata files. No database needed.
2. **Search mechanism:** File grep via Python `subprocess` or in-process line scan. Scopes to task logs only (not system/infra logs).
3. **Volume:** ~50-200KB per log × ~10 jobs/day = manageable. Files are already there.
4. **Retention:** No automatic cleanup for now. Files persist until manually removed.

**Scope:** Task job logs only (`logs/jobs/*.log`). System logs, infra logs, and other log sources are out of scope.

**Files:**
- Create: `apps/api/routers/logs.py` (main repo)
- Create: `apps/api/schemas/logs.py` (main repo)
- Modify: `apps/api/services/runtime.py` (main repo — register router)
- Create: `src/pages/logs/LogSearchPage.vue` (web repo)
- Create: `src/api/logs.ts` (web repo)

**Endpoint: `GET /api/logs/search`**

Query parameters:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | _(required)_ | Search text (case-insensitive substring match) |
| `job_id` | string | `null` | Filter to a specific job |
| `date_from` | string (ISO date) | `null` | Only search logs created on or after this date |
| `date_to` | string (ISO date) | `null` | Only search logs created on or before this date |
| `limit` | int | `100` | Max results returned (hard cap: 500) |

Response:
```json
{
  "results": [
    {
      "job_id": "daily-20260523-092547-391a",
      "line_number": 42,
      "text": "18:20:36    Pipeline      STARTING JAVDB PIPELINE",
      "kind": "daily",
      "created_at": "2026-05-23T09:25:47+00:00"
    }
  ],
  "total_matched": 157,
  "truncated": false
}
```

- [x] **Step 1: Implement log search endpoint (BE).** _(Done)_

  Implemented in `apps/api/routers/logs.py`. Hardened: corrupt `.meta.json` handling, UTF-8 encoding with `errors="ignore"`, uses `context.RESOLVED_JOB_LOG_DIR` instead of relative path. Schema in `apps/api/schemas/logs.py`. Registered in `apps/api/services/runtime.py`.

  **File:** `apps/api/routers/logs.py`

  ```python
  import json
  from pathlib import Path
  from fastapi import APIRouter, Depends, HTTPException, Query
  from apps.api.infra.auth import require_role
  from apps.api.schemas.logs import LogSearchResponse, LogSearchItem

  router = APIRouter(prefix="/api/logs", tags=["logs"])
  _LOGS_DIR = Path("logs/jobs")
  _HARD_CAP = 500

  @router.get("/search", response_model=LogSearchResponse)
  def search_logs(
      q: str = Query(..., min_length=1, max_length=200),
      job_id: str | None = Query(None),
      date_from: str | None = Query(None),
      date_to: str | None = Query(None),
      limit: int = Query(100, ge=1, le=_HARD_CAP),
      _user=Depends(require_role("admin")),
  ):
      if not _LOGS_DIR.exists():
          return LogSearchResponse(results=[], total_matched=0, truncated=False)

      # Collect candidate log files
      candidates = []
      for meta_path in sorted(_LOGS_DIR.glob("*.meta.json"), reverse=True):
          meta = json.loads(meta_path.read_text())
          jid = meta.get("job_id", meta_path.stem.removesuffix(".meta"))
          if job_id and jid != job_id:
              continue
          created = meta.get("created_at", "")
          if date_from and created < date_from:
              continue
          if date_to and created > date_to + "T23:59:59Z":
              continue
          log_path = meta_path.with_suffix("").with_suffix(".log")
          if log_path.exists():
              candidates.append((jid, log_path, meta))

      # Search through files
      results = []
      total = 0
      q_lower = q.lower()
      for jid, log_path, meta in candidates:
          for i, line in enumerate(log_path.read_text().splitlines(), 1):
              if q_lower in line.lower():
                  total += 1
                  if len(results) < limit:
                      results.append(LogSearchItem(
                          job_id=jid, line_number=i, text=line,
                          kind=meta.get("kind", ""),
                          created_at=meta.get("created_at", ""),
                      ))

      return LogSearchResponse(
          results=results, total_matched=total, truncated=total > limit,
      )
  ```

  **File:** `apps/api/schemas/logs.py`

  ```python
  from pydantic import BaseModel

  class LogSearchItem(BaseModel):
      job_id: str
      line_number: int
      text: str
      kind: str
      created_at: str

  class LogSearchResponse(BaseModel):
      results: list[LogSearchItem]
      total_matched: int
      truncated: bool
  ```

  Register in `apps/api/services/runtime.py`.

- [x] **Step 2: Write unit tests for log search endpoint.** _(Done)_

  20 tests in `tests/unit/test_logs_endpoints.py`. Uses `tmp_path` + monkeypatch `_LOGS_DIR`. Test cases:
  - Search with match → returns results with correct line numbers
  - Search with `job_id` filter → only searches that job
  - Search with `date_from`/`date_to` → filters by metadata timestamp
  - No match → empty results
  - Results exceed limit → `truncated=true`, `total_matched` > len(results)
  - Empty `logs/jobs/` dir → empty results
  - Admin-only access (readonly → 403, anon → 401)

- [ ] **Step 3: Build FE LogSearchPage.**

  **File:** `src/pages/logs/LogSearchPage.vue` (web repo, new)

  **Route:** Add to `src/router/routes.ts`:
  ```typescript
  {
    path: '/logs',
    name: 'logs',
    component: () => import('@/pages/logs/LogSearchPage.vue'),
    meta: { requiresAuth: true, roles: ['admin'] },
  }
  ```

  **Sidebar:** Add to `options` (after Migrations, before Settings):
  ```typescript
  items.push({
    label: t('nav.logs'),
    key: 'logs',
    icon: () => '📜',
  })
  ```
  Add to `routeMap`: `logs: '/logs'`. Add i18n key `nav.logs`.

  **API:** Create `src/api/logs.ts`:
  ```typescript
  import { http } from './client'

  export interface LogSearchItem {
    job_id: string
    line_number: number
    text: string
    kind: string
    created_at: string
  }
  export interface LogSearchResponse {
    results: LogSearchItem[]
    total_matched: number
    truncated: boolean
  }
  export interface LogSearchParams {
    q: string
    job_id?: string
    date_from?: string
    date_to?: string
    limit?: number
  }

  export async function searchLogs(params: LogSearchParams): Promise<LogSearchResponse> {
    const { data } = await http.get<LogSearchResponse>('/api/logs/search', { params })
    return data
  }
  ```

  **Layout:**
  - Search bar: `<NInput>` for query text + `<NSelect>` for job_id (optional, populated from recent jobs) + `<NDatePicker>` range for date filter + "Search" `<NButton>`.
  - Results: `<NDataTable>` with columns: Job ID (`<NTag>`), Line #, Log Text (monospace, full width). Highlight matched substring in yellow.
  - Footer: "Showing X of Y matches" + truncation warning if applicable.
  - Empty state: "Enter a search term to search task logs."
  - Admin-only page.

- [ ] **Step 4: Commit (both repos).**

  ```bash
  # Main repo
  git commit -m "feat(api): add log search endpoint — file grep over logs/jobs/"
  # Web repo
  git commit -m "feat(fe): add Log Search page"
  ```

---

## Task 5: Statistics Dashboard

**Design decisions (resolved 2026-05-23):**
- **Scope:** All 8 metrics (see table below).
- **Chart library:** `vue-chartjs` (Chart.js wrapper, ~65KB gzip). Lazy-loaded on the statistics route to avoid impacting initial bundle.
- **Endpoints:** Two — `GET /api/stats/summary` (snapshot) + `GET /api/stats/trend` (time-series).

**Metrics:**

| # | Metric | Data source | Chart type |
|---|--------|-------------|------------|
| 1 | Success/failure rate | `ReportSessions` (Status column) | Donut |
| 2 | Average run duration | `ReportSessions` (StartedAt → CompletedAt) | Line |
| 3 | Movies extracted per run | `ReportMovies` COUNT grouped by SessionId | Bar |
| 4 | Torrents extracted per run | `ReportTorrents` COUNT grouped by SessionId | Bar |
| 5 | MovieHistory growth | `MovieHistory` COUNT by date bucket | Line |
| 6 | PikPak transfer volume | `PikpakHistory` COUNT by date bucket | Line |
| 7 | Rclone dedup freed | `DedupRecords` SUM(ExistingFolderSize) by date | Bar |
| 8 | Proxy ban frequency | Scan `logs/jobs/*.log` for "ban" keyword, count per day | Line |

Metric 8 (proxy bans) is derived from log files, not DB. If `logs/jobs/` is empty, return zero.

**Files:**
- Create: `apps/api/routers/stats.py` (main repo)
- Create: `apps/api/schemas/stats.py` (main repo)
- Modify: `apps/api/services/runtime.py` (main repo — register router)
- Create: `src/pages/stats/StatsPage.vue` (web repo)
- Create: `src/api/stats.ts` (web repo)

**Endpoint 1: `GET /api/stats/summary`**

Response:
```json
{
  "total_runs": 142,
  "success_rate": 0.92,
  "avg_duration_seconds": 345,
  "total_movies": 12450,
  "total_torrents": 34200,
  "total_pikpak": 8900,
  "total_dedup_freed_bytes": 1073741824,
  "proxy_bans_last_7d": 5
}
```

**Endpoint 2: `GET /api/stats/trend?metric=success_rate&period=30d`**

Query parameters:
| Param | Type | Values | Default |
|-------|------|--------|---------|
| `metric` | string | `success_rate`, `duration`, `movies`, `torrents`, `history_growth`, `pikpak`, `dedup`, `proxy_bans` | _(required)_ |
| `period` | string | `7d`, `30d`, `90d` | `30d` |

Response:
```json
{
  "metric": "success_rate",
  "period": "30d",
  "data_points": [
    { "date": "2026-05-01", "value": 0.95 },
    { "date": "2026-05-02", "value": 1.0 },
    { "date": "2026-05-03", "value": 0.5 }
  ]
}
```

- [x] **Step 1: Implement stats summary endpoint (BE).** _(Done)_

  Implemented in `apps/api/routers/stats.py`. Queries across 3 databases + log file scan. Auth: `_require_auth` (any authenticated user). Each query wrapped in `_safe_query_one` / `_safe_query_all` for graceful degradation. D1 dict-row compatibility via `_extract_row_pair()`. `total_dedup_freed_bytes` filters to `IsDeleted=1` only. Registered in `apps/api/services/runtime.py`.

  **File:** `apps/api/routers/stats.py`

  Queries across multiple databases:
  - `ReportSessions` from reports DB (success/failure counts, avg duration)
  - `MovieHistory` / `TorrentHistory` from history DB (total counts)
  - `PikpakHistory` / `DedupRecords` from operations DB
  - `logs/jobs/*.log` file scan for proxy ban count (grep "ban" in last 7 days' logs)

  Auth: `_require_auth` (any authenticated user). Read-only aggregation, no admin gate needed.

  Implementation note: each data source query is a simple SQL `COUNT(*)` / `AVG()` / `SUM()`. Wrap each in try/except to return `null` if a table doesn't exist yet (graceful degradation).

- [x] **Step 2: Implement stats trend endpoint (BE).** _(Done)_

  Same file. All 8 metrics implemented. `proxy_bans` uses shared `_proxy_bans_by_date()` helper (no duplication with summary). `duration` returns empty (no CompletedAt column yet). Invalid metric/period → 422 with structured error.

  **File:** `apps/api/schemas/stats.py`

  ```python
  from pydantic import BaseModel

  class StatsSummary(BaseModel):
      total_runs: int
      success_rate: float | None
      avg_duration_seconds: float | None
      total_movies: int
      total_torrents: int
      total_pikpak: int
      total_dedup_freed_bytes: int
      proxy_bans_last_7d: int

  class TrendDataPoint(BaseModel):
      date: str
      value: float

  class TrendResponse(BaseModel):
      metric: str
      period: str
      data_points: list[TrendDataPoint]
  ```

- [x] **Step 3: Write unit tests for stats endpoints.** _(Done)_

  27 tests in `tests/unit/test_stats_endpoints.py`. Uses in-memory SQLite with pre-populated test data, handles `_isolate_sqlite` path collapse. Includes concrete value assertions for success_rate and movies trends, period cutoff exclusion, dedup only-deleted-records filter. Test cases:

- [ ] **Step 4: Build FE StatsPage with vue-chartjs.**

  **File:** `src/pages/stats/StatsPage.vue` (web repo, new)

  **Route:** Add to `src/router/routes.ts`:
  ```typescript
  {
    path: '/stats',
    name: 'stats',
    component: () => import('@/pages/stats/StatsPage.vue'),
    meta: { requiresAuth: true },
  }
  ```

  **Sidebar:** Add to `options` (after Home, before Run — stats is a top-level dashboard):
  ```typescript
  { label: t('nav.stats'), key: 'stats', icon: () => '📊' }
  ```
  Add to `routeMap`: `stats: '/stats'`. Add i18n key `nav.stats`.

  **API:** Create `src/api/stats.ts`:
  ```typescript
  import { http } from './client'

  export interface StatsSummary {
    total_runs: number
    success_rate: number | null
    avg_duration_seconds: number | null
    total_movies: number
    total_torrents: number
    total_pikpak: number
    total_dedup_freed_bytes: number
    proxy_bans_last_7d: number
  }
  export interface TrendDataPoint {
    date: string
    value: number
  }
  export interface TrendResponse {
    metric: string
    period: string
    data_points: TrendDataPoint[]
  }

  export async function getStatsSummary(): Promise<StatsSummary> {
    const { data } = await http.get<StatsSummary>('/api/stats/summary')
    return data
  }

  export async function getStatsTrend(
    metric: string, period: string = '30d'
  ): Promise<TrendResponse> {
    const { data } = await http.get<TrendResponse>('/api/stats/trend', {
      params: { metric, period },
    })
    return data
  }
  ```

  **Dependencies:** `npm install vue-chartjs chart.js`

  **Layout:**
  - Top row: 4 summary cards (`<NCard>`) — Total Runs + Success Rate (donut), Avg Duration, Total Movies, Total Torrents. Use `<NStatistic>` for numbers.
  - Second row: 2 summary cards — PikPak Volume, Dedup Freed (formatted as human-readable bytes).
  - Charts section: `<NTabs>` with period selector (`7d` / `30d` / `90d`).
    - Tab "Run Metrics": Line chart (duration trend) + Bar chart (movies/torrents per run)
    - Tab "Growth": Line chart (MovieHistory growth + PikPak volume)
    - Tab "System": Line chart (proxy bans) + Bar chart (dedup freed)
  - Each chart uses `<Line>` / `<Bar>` / `<Doughnut>` from `vue-chartjs`, wrapped in `defineAsyncComponent()` for lazy loading.
  - Loading state: `<NSpin>` while fetching.
  - Error state: `<NEmpty description="Failed to load statistics">` with retry button.

- [ ] **Step 5: Commit (both repos).**

  ```bash
  # Main repo
  git commit -m "feat(api): add statistics summary + trend endpoints"
  # Web repo
  git commit -m "feat(fe): add Statistics Dashboard with vue-chartjs"
  ```

---

## Task 6: Multi-Tab Optimization (Optional)

**Context:** From ADR-008 Open Questions — two browser tabs polling the same backend doubles request volume.

**Implementation:** `BroadcastChannel`-shared polling so only one tab fetches at a time.

- [ ] **Step 1: Create `src/composables/useSharedPolling.ts`.**

  ```typescript
  // Wraps usePolling with BroadcastChannel coordination.
  // Leader election: tab with lowest random ID becomes the poller.
  // Other tabs receive data via BroadcastChannel messages.
  // On leader tab close, remaining tabs re-elect.
  ```

- [ ] **Step 2: Replace `usePolling` usage in Tasks, Sessions, Dashboard, GH Actions pages.**
- [ ] **Step 3: Write unit test verifying only one tab polls.**
- [ ] **Step 4: Browser-test with two tabs open + commit.**

  ```bash
  git commit -m "perf(fe): add BroadcastChannel-shared polling for multi-tab optimization"
  ```

---

## E2E Journeys (Phase 3)

| # | Journey | Key assertions |
|---|---------|---------------|
| 18 | GH Actions: edit workflow YAML | Load workflow → edit content → save → verify commit SHA returned |
| 19 | GH Actions: secrets CRUD | List secrets → add new secret → verify appears in list → delete → verify removed |
| 20 | Migrations: run a migration | List migrations → find pending → preview SQL → verify SQL preview shown |
| 21 | Log search | Enter search term → verify results appear with job_id + line number → filter by job_id → verify filtered |
| 22 | Statistics dashboard | Load page → verify summary cards populated → switch period → verify chart updates |

- [ ] **Step 1: Write journeys 18–22.**
- [ ] **Step 2: Run full E2E suite (all phases).**

  ```bash
  npx playwright test --project=chromium
  ```

  Expected: all 28 journeys pass (13 Phase 1 + 10 Phase 2 + 5 Phase 3).

- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "test(e2e): add Phase 3 journeys 18-22 — editor, secrets, migrations, logs, stats"
  ```

---

## Suggested Execution Order

```
1. Task 1 FE (WorkflowEditorPage)    — BE done, FE ready to build
2. Task 2 FE (SecretsPage)           — BE done, FE ready to build
3. Task 3 FE (MigrationsPage)        — BE done, FE ready to build
4. Task 4 (Log search)               — full stack, specified
5. Task 5 (Statistics dashboard)     — full stack, specified (install vue-chartjs)
6. Task 6 (Multi-tab optimization)   — optional, can be done anytime
7. E2E journeys 18–22               — after all FE pages are built
```

Tasks 1–3 BE is complete. All tasks are fully specified and ready for implementation.
