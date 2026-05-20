# IMP-011: Frontend Rewrite — Phase 3: Power-User Features & Analytics

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver GH Actions advanced tiers (`edit`/`admin`), Migrations UI, global log search, and statistics dashboard — completing the full frontend surface.

**Architecture:** See [ADR-008](../adr/ADR-008-frontend-rewrite-architecture.md) for all architectural decisions.

**Tech Stack:** Same as Phase 2. Chart library TBD (see Task 5).

**Source spec:** `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md` §6.2 Phase 3, §8.4 Phase 3

**Related:** [ADR-008](../adr/ADR-008-frontend-rewrite-architecture.md), [IMP-009](IMP-009-frontend-phase1-completion.md) (Phase 1), [IMP-010](IMP-010-frontend-phase2-full-cli-coverage.md) (Phase 2)

**Prerequisites:** Phase 2 complete ([IMP-010](IMP-010-frontend-phase2-full-cli-coverage.md)). GH Actions monitor tier functional.

**Timing:** "When needed" — Phase 3 ships incrementally based on operator demand after Phase 2 dogfooding.

---

## Design Status

| Feature | Design status | Notes |
|---------|--------------|-------|
| GH Actions `edit` tier | Specified (§8.4) | YAML editor + `actionlint` dry-run |
| GH Actions `admin` tier | Specified (§8.4) | Secrets CRUD (values opaque) |
| Migrations UI | Specified (§8.4) | List + run single migration |
| Global log search | **Deferred** | Storage strategy TBD — depends on Phase 2 log volume observations |
| Statistics dashboard | **Deferred** | Scope + chart library TBD — depends on Phase 2 data accumulation |

Tasks 1–3 are fully specified. Tasks 4–5 require a design brainstorming session after Phase 2 dogfooding before implementation begins.

---

## Endpoints

### Fully specified (6 endpoints)

| Method | Path | Purpose | Tier required |
|--------|------|---------|--------------|
| `PUT` | `/api/gh-actions/workflows/{name}` | Edit workflow YAML | `edit` |
| `GET` | `/api/gh-actions/secrets` | List secret names + updated_at (values opaque) | `admin` |
| `POST` | `/api/gh-actions/secrets` | Create or update a secret | `admin` |
| `DELETE` | `/api/gh-actions/secrets/{name}` | Delete a secret | `admin` |
| `GET` | `/api/migrations` | List migrations + applied state | `admin` |
| `POST` | `/api/migrations/{id}/run` | Run a single migration | `admin` |

### Design deferred (1 endpoint)

| Method | Path | Purpose | Blocker |
|--------|------|---------|---------|
| `GET` | `/api/logs/search?q=` | Global log search | Log persistence strategy not decided |

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

- [ ] **Step 1: Add GitHub Contents API methods to client.**

  ```python
  # javdb/integrations/gh_actions/client.py — new methods
  def get_workflow_content(self, filename: str) -> dict:
      """Return { content: str (decoded), sha: str, path: str }."""
      resp = self._client.get(f"/repos/{self._repo}/contents/.github/workflows/{filename}")
      resp.raise_for_status()
      data = resp.json()
      import base64
      return {
          "content": base64.b64decode(data["content"]).decode("utf-8"),
          "sha": data["sha"],
          "path": data["path"],
      }

  def update_workflow_content(self, filename: str, content: str, sha: str,
                               message: str, branch: str = "main") -> dict:
      """Commit updated workflow file. Returns { commit_sha }."""
      import base64
      resp = self._client.put(
          f"/repos/{self._repo}/contents/.github/workflows/{filename}",
          json={
              "message": message,
              "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
              "sha": sha,
              "branch": branch,
          },
      )
      resp.raise_for_status()
      return {"commit_sha": resp.json()["content"]["sha"]}
  ```

- [ ] **Step 2: Implement PUT endpoint with YAML validation.**

  ```python
  @router.put("/workflows/{name}", dependencies=[Depends(_require_gh_edit)])
  async def update_workflow(name: str, body: WorkflowUpdateRequest, user=Depends(_require_admin)):
      import yaml
      try:
          yaml.safe_load(body.content)
      except yaml.YAMLError as e:
          raise HTTPException(422, f"Invalid YAML: {e}")

      client = _get_gh_client()
      current = client.get_workflow_content(name)
      result = client.update_workflow_content(
          name, body.content, current["sha"], body.commit_message, body.branch
      )
      return {"updated": True, "commit_sha": result["commit_sha"], "validation_warnings": []}
  ```

- [ ] **Step 3: Build FE WorkflowEditorPage.**

  Layout:
  - Left: workflow file list (from `GET /api/gh-actions/workflows`, filtered to show filenames)
  - Center: textarea or code editor (Monaco editor via `@monaco-editor/vue` — only if bundle budget allows, otherwise plain `<textarea>` with monospace font) showing YAML content loaded from `GET /api/gh-actions/workflows/{name}`
  - Bottom bar: commit message input + branch selector + "Save" button
  - Validation: client-side YAML syntax check on keystroke (debounced), red gutter for invalid lines

- [ ] **Step 4: Write unit + integration tests.**

  ```python
  def test_update_workflow_invalid_yaml(test_client, httpx_mock):
      resp = test_client.put("/api/gh-actions/workflows/ci.yml", json={
          "content": "invalid: yaml: [",
          "commit_message": "test",
      })
      assert resp.status_code == 422

  def test_update_workflow_success(test_client, httpx_mock):
      httpx_mock.add_response(...)  # Mock GitHub Contents API
      resp = test_client.put("/api/gh-actions/workflows/ci.yml", json={
          "content": "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n",
          "commit_message": "ci: test update",
      })
      assert resp.status_code == 200
      assert resp.json()["updated"] is True
  ```

- [ ] **Step 5: Run tests + commit (both repos).**

  ```bash
  # Main repo
  git commit -m "feat(api): add workflow YAML editor endpoint (GH Actions tier edit)"
  # Web repo
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

- [ ] **Step 1: Add secrets methods to GitHub client.**

  ```python
  def list_secrets(self) -> list[dict]:
      resp = self._client.get(f"/repos/{self._repo}/actions/secrets")
      resp.raise_for_status()
      return resp.json()["secrets"]  # [{ name, created_at, updated_at }]

  def create_or_update_secret(self, name: str, value: str) -> None:
      # Fetch repo public key
      key_resp = self._client.get(f"/repos/{self._repo}/actions/secrets/public-key")
      key_resp.raise_for_status()
      key_data = key_resp.json()

      # Encrypt with NaCl sealed box
      from nacl.public import SealedBox, PublicKey
      import base64
      public_key = PublicKey(base64.b64decode(key_data["key"]))
      sealed = SealedBox(public_key).encrypt(value.encode("utf-8"))
      encrypted_value = base64.b64encode(sealed).decode("ascii")

      # Upsert secret
      resp = self._client.put(
          f"/repos/{self._repo}/actions/secrets/{name}",
          json={"encrypted_value": encrypted_value, "key_id": key_data["key_id"]},
      )
      resp.raise_for_status()

  def delete_secret(self, name: str) -> None:
      resp = self._client.delete(f"/repos/{self._repo}/actions/secrets/{name}")
      resp.raise_for_status()
  ```

- [ ] **Step 2: Implement router endpoints.**

  All three gated on `_require_gh_admin` (tier `admin`).

- [ ] **Step 3: Build FE SecretsPage.**

  Layout:
  - Table: secret name, created_at, updated_at. **No value column** (values are opaque — GitHub never returns them).
  - "Add Secret" button → modal with name + value (textarea, masked by default with show/hide toggle). Submit → POST.
  - Row actions: "Update" (same modal, name readonly) + "Delete" (confirmation dialog).
  - Warning banner: "Secret values cannot be retrieved after saving. Only the name and timestamps are visible."

- [ ] **Step 4: Write tests + commit (both repos).**

  ```bash
  # Main repo
  git commit -m "feat(api): add GH Actions secrets CRUD endpoints (tier admin)"
  # Web repo
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

- [ ] **Step 1: Implement migration list endpoint.**

  ```python
  # apps/api/routers/migrations.py
  @router.get("/migrations", response_model=MigrationListResponse)
  async def list_migrations(user=Depends(_require_admin)):
      migration_dir = Path("javdb/migrations/d1")
      files = sorted(migration_dir.glob("*.sql"))
      applied = _get_applied_migrations()  # Query D1/SQLite for applied list
      return {
          "migrations": [
              {
                  "id": f.stem.split("_")[0],  # "0018"
                  "filename": f.name,
                  "applied": f.stem.split("_")[0] in applied,
                  "applied_at": applied.get(f.stem.split("_")[0]),
              }
              for f in files
          ]
      }
  ```

- [ ] **Step 2: Implement run migration endpoint.**

  ```python
  @router.post("/migrations/{migration_id}/run")
  async def run_migration(migration_id: str, body: RunMigrationRequest, user=Depends(_require_admin)):
      migration_file = _find_migration_file(migration_id)
      if not migration_file:
          raise HTTPException(404, f"Migration {migration_id} not found")

      sql = migration_file.read_text()

      if body.dry_run:
          return {"dry_run": True, "sql_preview": sql, "statements": sql.count(";")}

      # Execute
      conn = get_db("history")  # or appropriate DB based on migration content
      conn.executescript(sql)
      _mark_migration_applied(migration_id)
      return {"dry_run": False, "applied": True, "migration_id": migration_id}
  ```

- [ ] **Step 3: Build FE MigrationsPage.**

  Layout:
  - Table: migration ID, filename, applied (green check / gray dash), applied_at.
  - "Pending" badge count at top.
  - Row action for unapplied migrations: "Preview" button → drawer showing SQL content (syntax-highlighted via `<pre>` with monospace). "Apply" button → confirmation dialog → POST with `dry_run=false`.
  - Applied migrations are read-only rows (no actions).

- [ ] **Step 4: Write tests + commit (both repos).**

  ```bash
  # Main repo
  git commit -m "feat(api): add migrations list + run endpoints (Phase 3)"
  # Web repo
  git commit -m "feat(fe): add Migrations management page"
  ```

---

## Task 4: Global Log Search (Design TBD)

**Status:** Deferred. Design depends on Phase 2 dogfooding observations.

**Open questions to resolve before implementation:**
1. **Log persistence strategy** — Where are completed task logs stored long-term? Options: (a) new `TaskLogs` table in D1 with log content as TEXT, (b) file-system logs at `reports/logs/{task_id}.log`, (c) structured log entries in a dedicated table (one row per log line with timestamp + level + message).
2. **Search mechanism** — SQL LIKE (simple, slow for large volumes), SQLite FTS5 (fast but D1 lacks FTS), or file-system grep (not available in D1-only deployments).
3. **Log volume** — How much log data per task run? If <50KB average × 10 runs/day = 500KB/day = ~180MB/year. If stored as TEXT blobs in D1, this is manageable. If stored line-by-line, row count explodes.
4. **Retention policy** — How long should logs be searchable? 30 days? 90 days? Configurable?

**Prerequisite:** After Phase 2 dogfooding (~4 weeks), run a brainstorming session to decide the above. File: `docs/design/adr/ADR-009-log-search-design.md`.

**Endpoint (tentative):**
```
GET /api/logs/search?q=<text>&date_from=&date_to=&task_id=&level=
→ { items: [{ task_id, timestamp, level, message, context }], next_cursor }
```

**Placeholder task list** (to be expanded after design decision):
- [ ] Brainstorming session → ADR-009
- [ ] D1 migration for log storage table
- [ ] BE search endpoint
- [ ] FE log search page (search bar + results list + task-ID deep link)
- [ ] E2E journey

---

## Task 5: Statistics Dashboard (Design TBD)

**Status:** Deferred. Scope and chart library to be decided after Phase 2 data accumulation.

**Candidate metrics (to be confirmed):**

| Category | Metric | Data source |
|----------|--------|-------------|
| **Run metrics** | Daily/weekly success/failure rate | `ReportSessions` |
| **Run metrics** | Average run duration | `ReportSessions` (StartedAt → CompletedAt) |
| **Run metrics** | Movies/torrents extracted per run | `ReportMovies`, `ReportTorrents` |
| **Storage metrics** | MovieHistory/TorrentHistory growth curve | `MovieHistory` COUNT by DateTimeCreated buckets |
| **Storage metrics** | PikPak transfer volume | `PikpakHistory` |
| **Storage metrics** | Rclone dedup freed bytes | `DedupRecords` SUM(ExistingFolderSize) |
| **System metrics** | Proxy pool ban frequency | Task logs (requires log search) |
| **System metrics** | D1 request volume | External (Cloudflare dashboard — not fetchable via API) |

**Chart library candidates:**

| Library | Bundle size (gzip) | Pros | Cons |
|---------|-------------------|------|------|
| Chart.js | ~60 KB | Simple, well-documented | Limited chart types |
| ECharts | ~250 KB | Rich, powerful | Blows bundle budget |
| Lightweight custom (SVG) | ~5 KB | Tiny | Manual work for each chart |
| `vue-chartjs` (Chart.js wrapper) | ~65 KB | Vue-native API | Extra wrapper layer |

**Recommendation:** Chart.js via `vue-chartjs` for the 3-4 chart types needed (line, bar, donut). Lazy-loaded on the statistics route to avoid impacting initial bundle.

**Prerequisite:** After Phase 2 dogfooding, run a brainstorming session to finalize scope and chart selection. File: `docs/design/adr/ADR-010-statistics-dashboard-design.md`.

**Placeholder task list** (to be expanded after design decision):
- [ ] Brainstorming session → ADR-010
- [ ] BE aggregation endpoints (`GET /api/stats/summary`, `GET /api/stats/trend?metric=&period=`)
- [ ] FE statistics page with chart components (lazy-loaded)
- [ ] E2E journey

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
| 20 | Migrations: run a migration | List migrations → find pending → preview SQL → apply → verify applied state |

**Note:** Journeys for log search and statistics will be added after their respective design sessions.

- [ ] **Step 1: Write journey 18 — workflow YAML editor.**
- [ ] **Step 2: Write journey 19 — secrets CRUD.**
- [ ] **Step 3: Write journey 20 — migrations.**
- [ ] **Step 4: Run full E2E suite (all phases).**

  ```bash
  npx playwright test --project=chromium
  ```
  Expected: all 26 journeys pass (13 Phase 1 + 10 Phase 2 + 3 Phase 3).

- [ ] **Step 5: Commit.**

  ```bash
  git commit -m "test(e2e): add Phase 3 journeys 18-20 — workflow editor, secrets, migrations"
  ```

---

## Suggested Execution Order

```
1. Task 1 (GH Actions YAML editor)   — fully specified, can start immediately
2. Task 2 (GH Actions secrets)       — fully specified, independent of Task 1
3. Task 3 (Migrations UI)            — fully specified, independent
4. Task 6 (Multi-tab optimization)   — optional, can be done anytime
5. Task 4 (Log search)               — BLOCKED on design brainstorming (ADR-009)
6. Task 5 (Statistics)               — BLOCKED on design brainstorming (ADR-010)
```

Tasks 1–3 and 6 are implementable. Tasks 4–5 require design sessions after Phase 2 dogfooding.
