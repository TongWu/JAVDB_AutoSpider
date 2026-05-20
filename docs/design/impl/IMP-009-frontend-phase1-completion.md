# IMP-009: Frontend Rewrite — Phase 1 Completion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 1 remaining work: E2E fixture seeding, missing journeys, BE cleanups, and cutover to delete `apps/web/` + `apps/desktop/`.

**Architecture:** See [ADR-008](../adr/ADR-008-frontend-rewrite-architecture.md) for all architectural decisions.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, Playwright, Vue 3.5, Naive UI, Vitest.

**Source spec:** `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md`

**Related:** [IMP-010](IMP-010-frontend-phase2-full-cli-coverage.md) (Phase 2), [IMP-011](IMP-011-frontend-phase3-power-user.md) (Phase 3)

**Status:** Phase 1 feature-complete. Plans A–E shipped. Remaining: E2E fixtures, 2 journeys, BE cleanups, cutover.

---

## Shipped (reference only)

| Plan | Scope | Key deliverables |
|------|-------|-----------------|
| **A — BE foundation** | Main repo | Capabilities, onboarding, sessions API, test/reset gate, `publish-api-image.yml`, `publish-openapi.yml`, Pydantic tightening |
| **B — FE bootstrap** | Web repo | Scaffold, theme, layout, auth with single-flight refresh, boot gate, Dashboard skeleton, Docker, CI |
| **C — Onboarding + Run + Tasks** | Web repo | Five-step wizard, Run page (Daily/Ad-Hoc × Standard/Advanced), Tasks + log Drawer, E2E journeys 1/2/3 |
| **D — Sessions + Settings** | Web repo | Sessions list + drawer + rollback + commit, Config/Auth/Capabilities/Appearance, 52 unit specs |
| **E — Browse** | Web repo | Resolve + Lists + Preview + D1 badge composable, E2E journeys 4/4a/4b |
| **Follow-ups** | Both repos | BE change-password endpoint + FE dialog, per-field config i18n labels (~100 keys), D6 E2E scaffold, Journey 7 (change password) |

---

## Task 1: E2E Session Fixtures

**Files:**
- Create: `JAVDB_AutoSpider_Web/tests/e2e/fixtures/seed-sessions.ts`
- Modify: `JAVDB_AutoSpider_Web/tests/e2e/sessions-rollback.spec.ts`
- Modify: `apps/api/routers/test_mode.py` (main repo)

**Context:** D6 specs (journeys 5/5a/5b) currently `test.skip()` when no sessions exist. Need a seeding mechanism gated on `TEST_MODE=1`.

- [ ] **Step 1: Add seed endpoint to BE.**

  In `apps/api/routers/test_mode.py`, add `POST /api/test/seed-sessions`. Creates 3 test sessions:

  | Session | Status | Write mode | Rows |
  |---------|--------|-----------|------|
  | `test-committed-001` | `committed` | `audit` | 2 movies, 3 torrents in MovieHistory/TorrentHistory + audit rows |
  | `test-finalizing-002` | `finalizing` | `pending` | 3 movies in PendingMovieHistoryWrites |
  | `test-inprogress-003` | `in_progress` | `audit` | 1 movie in MovieHistory + audit row + 2 movies in PendingMovieHistoryWrites |

  Response: `{ "seeded": 3, "session_ids": [...] }`

- [ ] **Step 2: Write unit test for seed endpoint.**

  ```python
  # tests/unit/test_seed_sessions.py
  def test_seed_sessions_creates_three_sessions(test_client):
      resp = test_client.post("/api/test/seed-sessions")
      assert resp.status_code == 200
      assert resp.json()["seeded"] == 3
  ```

  Run: `pytest tests/unit/test_seed_sessions.py -v`
  Expected: PASS

- [ ] **Step 3: Write `seed-sessions.ts` fixture.**

  ```typescript
  // tests/e2e/fixtures/seed-sessions.ts
  import { request } from '@playwright/test';

  export async function seedSessions(baseURL: string) {
    const ctx = await request.newContext({ baseURL });
    const resp = await ctx.post('/api/test/seed-sessions');
    expect(resp.ok()).toBeTruthy();
    return resp.json();
  }
  ```

- [ ] **Step 4: Update `sessions-rollback.spec.ts`.**

  Remove `test.skip()` guards. Wire `seedSessions()` in `test.beforeAll`. Assert:
  - Journey 5: list sessions → find committed → rollback dry-run → verify plan → apply → verify state changes to `rolled_back`
  - Journey 5a: find finalizing session → commit force → verify state changes to `committed`
  - Journey 5b: find in-progress session (audit + pending) → rollback with `restore_from_audit=true, include_pending=true` → verify both row types cleaned

- [ ] **Step 5: Run E2E locally.**

  ```bash
  npx playwright test sessions-rollback --project=chromium
  ```
  Expected: 3 journeys PASS

- [ ] **Step 6: Commit both repos.**

  ```bash
  # Main repo
  git add apps/api/routers/test_mode.py tests/unit/test_seed_sessions.py
  git commit -m "feat(api): add POST /api/test/seed-sessions for E2E fixture seeding"

  # Web repo
  git add tests/e2e/fixtures/seed-sessions.ts tests/e2e/sessions-rollback.spec.ts
  git commit -m "test(e2e): wire session fixtures, enable journeys 5/5a/5b"
  ```

---

## Task 2: Journey 8 — Backend Down Outage

**Files:**
- Create: `JAVDB_AutoSpider_Web/tests/e2e/outage.spec.ts`

- [ ] **Step 1: Write Playwright spec.**

  ```typescript
  // tests/e2e/outage.spec.ts
  import { test, expect } from '@playwright/test';

  test('journey 8: backend down shows outage screen, recovery resumes app', async ({ page }) => {
    // Block all API calls
    await page.route('**/api/**', route => route.abort('connectionrefused'));

    await page.goto('/');
    // Outage blocker should render
    await expect(page.getByText('Cannot reach backend')).toBeVisible();
    await expect(page.getByRole('button', { name: /retry/i })).toBeVisible();

    // Unblock API
    await page.unroute('**/api/**');

    // Click retry → should load dashboard
    await page.getByRole('button', { name: /retry/i }).click();
    await expect(page.getByTestId('dashboard')).toBeVisible({ timeout: 10000 });
  });
  ```

- [ ] **Step 2: Run locally.**

  ```bash
  npx playwright test outage --project=chromium
  ```
  Expected: PASS

- [ ] **Step 3: Commit.**

  ```bash
  git add tests/e2e/outage.spec.ts
  git commit -m "test(e2e): journey 8 — backend outage screen + recovery"
  ```

---

## Task 3: Journey 8a — Dismiss Hint

**Files:**
- Create: `JAVDB_AutoSpider_Web/tests/e2e/dismiss-hint.spec.ts`

- [ ] **Step 1: Write Playwright spec.**

  ```typescript
  // tests/e2e/dismiss-hint.spec.ts
  import { test, expect } from '@playwright/test';

  test('journey 8a: dismiss hint card persists across reload', async ({ page }) => {
    // Login (onboarding completed, but PikPak/Rclone unconfigured)
    await page.goto('/');
    // ... login flow ...

    // Dashboard should show hint cards for unconfigured features
    const hintCard = page.getByTestId('hint-card-pikpak');
    await expect(hintCard).toBeVisible();

    // Dismiss the card
    await hintCard.getByRole('button', { name: /dismiss/i }).click();
    await expect(hintCard).not.toBeVisible();

    // Reload and verify persistence
    await page.reload();
    await expect(page.getByTestId('hint-card-pikpak')).not.toBeVisible();
    // Other hint cards still visible
    await expect(page.getByTestId('hint-card-rclone')).toBeVisible();
  });
  ```

- [ ] **Step 2: Run locally.**

  ```bash
  npx playwright test dismiss-hint --project=chromium
  ```
  Expected: PASS

- [ ] **Step 3: Commit.**

  ```bash
  git add tests/e2e/dismiss-hint.spec.ts
  git commit -m "test(e2e): journey 8a — dismiss hint card persistence"
  ```

---

## Task 4: BE Cleanup — Rollback Layering Inversion

**Files:**
- Create: `javdb/storage/rollback/session_helpers.py` (main repo — move from `apps/cli/db/_session_helpers.py`)
- Modify: `javdb/storage/rollback/core.py`
- Modify: `apps/cli/db/_session_helpers.py` (reduce to re-exports)
- Modify: `apps/cli/db/rollback.py`
- Modify: `apps/cli/db/commit_session.py`
- Modify: tests that monkeypatch `apps.cli.db._session_helpers`

**2026-05-20 update:** this task has been partially completed. The original storage-to-CLI import has moved to `javdb.storage.rollback.session_helpers`, while `apps.cli.db._session_helpers` remains a shim. [ADR-014](../adr/ADR-014-storage-cli-layering.md) and [IMP-027](IMP-027-storage-cli-layering-phase1-guard.md) through [IMP-029](IMP-029-storage-cli-layering-phase3-delete-legacy-wrappers.md) track the final canonical module and wrapper deletion.

**Context:** `javdb/storage/rollback/core.py` imports from `apps.cli.db._session_helpers` — a cross-layer import (library → CLI). The helpers need to move into `javdb/storage/`.

- [ ] **Step 1: Identify all helpers to move.**

  ```bash
  grep -n "^def \|^class \|^@dataclass" apps/cli/db/_session_helpers.py
  ```

  Expected targets: session lookup helpers, MovieClaim fanout logic, JSONL emission, `GITHUB_OUTPUT` helpers, timestamp normalization.

- [ ] **Step 2: Create `javdb/storage/rollback/session_helpers.py`.**

  Move all function and class definitions from `apps/cli/db/_session_helpers.py` into the new file. Keep signatures identical.

- [ ] **Step 3: Update `apps/cli/db/_session_helpers.py` to re-export.**

  ```python
  """Backward-compatibility shim — all helpers now live in javdb.storage.rollback.session_helpers."""
  from javdb.storage.rollback.session_helpers import *  # noqa: F401,F403
  ```

- [ ] **Step 4: Update `javdb/storage/rollback/core.py` import.**

  ```python
  # Before:
  from apps.cli.db._session_helpers import (...)
  # After:
  from javdb.storage.rollback.session_helpers import (...)
  ```

- [ ] **Step 5: Update test monkeypatches.**

  ```bash
  grep -rn "apps\.cli\.db\._session_helpers" tests/
  ```

  Update monkeypatch targets to `javdb.storage.rollback.session_helpers`.

- [ ] **Step 6: Verify no cross-layer imports remain.**

  ```bash
  grep -rn "from apps\." javdb/
  ```
  Expected: zero results.

- [ ] **Step 7: Run tests.**

  ```bash
  pytest tests/ -k "rollback or commit_session or session" -v
  ```
  Expected: all pass.

- [ ] **Step 8: Commit.**

  ```bash
  git add javdb/storage/rollback/session_helpers.py javdb/storage/rollback/core.py \
          apps/cli/db/_session_helpers.py apps/cli/db/rollback.py apps/cli/db/commit_session.py \
          tests/
  git commit -m "refactor(rollback): move session helpers from CLI into javdb.storage, fix layering inversion"
  ```

---

## Task 5: BE Cleanup — Commit Endpoint Side-Effect Parity

**Files:**
- Modify: `apps/api/routers/sessions.py`
- Modify: `apps/api/schemas/sessions.py` (if Pydantic request model exists)

**Context:** `javdb/storage/sessions/commit.py` already has `fanout_claims` and `emit_metrics` flags on `CommitRequest`. The HTTP endpoint (`POST /api/sessions/{id}/commit`) currently passes `fanout_claims=False, emit_metrics=False` — making it a "DB-only" commit. The fix is to expose these flags in the API request body so FE can choose.

- [ ] **Step 1: Check current API request schema.**

  ```bash
  grep -A 20 "class.*CommitBody\|class.*ForceCommit" apps/api/routers/sessions.py apps/api/schemas/
  ```

- [ ] **Step 2: Add `fanout_claims` and `emit_metrics` to the API request body.**

  Both default to `True` for the HTTP endpoint (opposite of the library default) — so the API commit has full parity with the CLI unless the caller explicitly opts out.

  ```python
  class SessionCommitBody(BaseModel):
      force: bool = False
      drop_pending: bool = False
      fanout_claims: bool = True   # default True for HTTP (CLI parity)
      emit_metrics: bool = True    # default True for HTTP (CLI parity)
  ```

- [ ] **Step 3: Wire the flags through to `CommitRequest`.**

  In the sessions router commit handler, pass `fanout_claims=body.fanout_claims, emit_metrics=body.emit_metrics` to the library's `commit_session()`.

- [ ] **Step 4: Write integration test.**

  ```python
  def test_commit_endpoint_with_fanout(test_client, seeded_finalizing_session):
      resp = test_client.post(
          f"/api/sessions/{seeded_finalizing_session}/commit",
          json={"force": True, "fanout_claims": True, "emit_metrics": True},
      )
      assert resp.status_code == 200
      # Verify JSONL drift record was emitted
      assert Path("reports/D1/d1_drift.jsonl").stat().st_size > 0
  ```

- [ ] **Step 5: Run tests.**

  ```bash
  pytest tests/ -k "commit" -v
  ```
  Expected: all pass.

- [ ] **Step 6: Commit.**

  ```bash
  git add apps/api/routers/sessions.py tests/
  git commit -m "fix(sessions): expose fanout_claims + emit_metrics in HTTP commit endpoint"
  ```

---

## Task 6: Cutover

**Prerequisites:** Tasks 1–5 complete. All 13 Phase 1 E2E journeys green.

- [ ] **Step 1: Publish first FE Docker image.**

  ```bash
  # In web repo — tag and push triggers docker.yml workflow
  git tag v0.1.0
  git push origin v0.1.0
  ```

  Verify: `ghcr.io/<user>/javdb-autospider-web:v0.1.0` published.

- [ ] **Step 2: Delete `apps/web/` and `apps/desktop/` in main repo.**

  ```bash
  git rm -r apps/web/ apps/desktop/
  ```

- [ ] **Step 3: Clean root `package.json`.**

  Remove `electron:dev`, `web:dev`, and any other entries referencing `apps/web/` or `apps/desktop/`. If no npm scripts remain, remove `package.json` and `package-lock.json` entirely.

- [ ] **Step 4: Create deployment docs.**

  - Create `docs/handbook/en/self-hoster/web-ui-deploy.md` — setup instructions for the new FE (Docker compose, split deploy, GH Actions mode).
  - Create `docs/handbook/zh/self-hoster/web-ui-deploy.md` — paired Chinese translation.
  - Link from README.md and README_CN.md nav section.

- [ ] **Step 5: Update CLAUDE.md.**

  Remove `apps/web/` and `apps/desktop/` from the Architecture section. Replace with a one-line pointer: `- **Frontend** — Standalone repo [`javdb-autospider-web`](https://github.com/<user>/javdb-autospider-web). See [ADR-008](../adr/ADR-008-frontend-rewrite-architecture.md).`

- [ ] **Step 6: Verify no stale references.**

  ```bash
  grep -rn "apps/web\|apps/desktop\|electron:dev" docs/ README.md README_CN.md scripts/ CLAUDE.md
  ```
  Expected: only historical references in ADR/spec files, not active instructions.

- [ ] **Step 7: Commit and tag.**

  ```bash
  git add -A
  git commit -m "feat(cutover): delete apps/web + apps/desktop, point to javdb-autospider-web repo

  Closes Phase 1 of the frontend rewrite (ADR-008).
  Old web and desktop apps replaced by standalone javdb-autospider-web repository."

  git tag v2.2.0
  ```

---

## Optional: Per-field Config Descriptions

**Priority:** Low — labels (shipped) were the operator pain point. Descriptions are polish.

- [ ] Add `settings.config.fields.{KEY}.description` keys to `en.json`, `zh-CN.json`, `ja.json` (~100 keys each)
- [ ] Wire tooltip rendering in Config page field components (Naive UI `n-tooltip` on `n-form-item`)
- [ ] Verify i18n parity test: `npm run check:i18n`
- [ ] Commit: `feat(i18n): add config field description tooltips`
