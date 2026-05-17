# IMP-009: Frontend Rewrite — Phased Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the frontend rewrite across three phases, delivering a standalone `javdb-autospider-web` repository that replaces `apps/web/` and covers every CLI-expressible operation via the UI.

**Architecture:** See [ADR-008](../adr/ADR-008-frontend-rewrite-architecture.md) for all architectural decisions and rationale.

**Tech Stack:** Vue 3.5, Naive UI 2.40+, Pinia 2, vue-router 4, vue-i18n 9, axios, TypeScript 5, Vite 6, Vitest 2, Playwright 1.48+.

**Source spec:** `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md`

**Prerequisites:** Main repo has the FastAPI backend running with existing endpoints. New repo `javdb-autospider-web` is created.

**Note:** FE-specific implementation plans for each sub-plan (Plans A–E) live in `JAVDB_AutoSpider_Web/docs/plans/`. This IMP covers the overall structure; sub-plans contain task-level checklists.

---

## Repository Skeleton

```
javdb-autospider-web/
├── README.md
├── package.json / tsconfig.json / vite.config.ts / index.html
├── public/
├── src/
│   ├── main.ts / App.vue
│   ├── router/
│   ├── stores/           # 6 Pinia stores (auth, capabilities, tasks, ui, onboarding, i18n)
│   ├── api/              # axios client + typed wrappers + api.gen.ts
│   ├── composables/      # useApi, usePolling, useLogStream, useIndexStatus
│   ├── i18n/             # zh-CN.json, en.json, ja.json
│   ├── theme/            # Naive UI themeOverrides (Direction C tokens)
│   ├── components/       # grouped by page/feature
│   ├── pages/            # one folder per route
│   └── types/            # api.gen.ts (openapi-typescript output)
├── tests/
│   ├── unit/             # Vitest
│   ├── contract/         # OpenAPI schema verification against running BE
│   └── e2e/              # Playwright (~26 user journeys)
├── docker/
│   ├── Dockerfile        # multi-stage → nginx static
│   └── nginx.conf
├── docker-compose.yml    # FE image + BE image reference
├── .github/workflows/    # ci, e2e, docker, release
└── docs/
    ├── plans/            # per-plan implementation checklists
    ├── deploy-colocated.md
    ├── deploy-split.md
    └── deploy-github-mode.md
```

---

## Information Architecture

### Top-level Navigation

```
🏠 Home                    Dashboard
⚡ Run                     Daily + Ad Hoc (Standard/Advanced)
📋 Activity
   ├ Tasks                 Task runs + log viewer
   └ Sessions              ReportSessions + rollback/commit
🌐 Browse                  Resolve / Lists / Preview
💾 Data                    Movies / Torrents (Phase 2)
⚙️ Operations              qB / PikPak / Rclone / Email / Cleanup (Phase 2)
🔧 Diagnostics             Health / Parse tester / JavDB session (Phase 2)
🚀 GitHub Actions          Runs / Workflows / Secrets (Phase 2–3, conditional)
🛠 Settings                Config / Auth / Capabilities / Appearance
```

### Phase Breakdown

| Phase | Pages | Goal |
|---|---|---|
| **Phase 1 (MVP)** | Login, Onboarding, Home, Run, Tasks, Sessions, Browse, Settings | Replace `apps/web/` with parity + onboarding + Browse |
| **Phase 2** | Data, Operations, Diagnostics, GH Actions `monitor` | Full CLI surface coverage |
| **Phase 3** | GH Actions `edit`/`admin`, Migrations, log search, statistics | Power-user features |

---

## Phase 1: Pre-work in Main Repo (Plan A)

### Task A1: Docker + CI infrastructure

- [ ] Verify/create BE Dockerfile (`docker/Dockerfile.api`)
- [ ] Land `publish-api-image.yml` (build + push to GHCR)
- [ ] Land `publish-openapi.yml` (dump `/openapi.json` to `docs/api/openapi.json` + GH Release)

### Task A2: Foundation endpoints

- [ ] `GET /api/capabilities` — returns version, ingestion_mode, storage_backend, features, gh_actions tier, deployment
- [ ] `POST /api/test/reset` — gated on `TEST_MODE=1` (404 otherwise). Truncates tables for E2E isolation
- [ ] Tighten Pydantic response models on Phase 1 endpoints (`tasks/*`, `auth/*`, `config/*`, `explore/*`)

### Task A3: Onboarding endpoints

- [ ] `GET /api/onboarding/status` — `{ completed, required_missing[], skippable_missing[] }`
- [ ] `POST /api/onboarding/test` — test component (`javdb`/`qb`/`proxy`/`smtp`)
- [ ] `POST /api/onboarding/complete` — mark setup done
- [ ] `POST /api/onboarding/dismiss-hint` — dismiss hint card
- [ ] `GET/PUT /api/system/state` — generic KV (dismissed_hints, prefs)

### Task A4: Sessions endpoints

- [ ] `GET /api/sessions` — list with `?state=` filter + cursor pagination
- [ ] `GET /api/sessions/{id}` — detail: writes, run identity, error log
- [ ] `POST /api/sessions/{id}/rollback` — `{ dry_run, include_pending, restore_from_audit }` → plan + actions
- [ ] `POST /api/sessions/{id}/commit` — `{ force, drop_pending }`

### Task A5: Auth endpoint

- [ ] `POST /api/auth/change-password` — re-auth + bcrypt hash + persist to config store. Rate limit `(5, 60, user)`

---

## Phase 1: FE Bootstrap (Plan B)

### Task B1: Scaffold + theme

- [ ] `npm create vite@latest` scaffold
- [ ] Theme tokens in `src/theme/index.ts` (Direction C light + dark)
- [ ] Naive UI provider in `App.vue` with `<n-config-provider :theme-overrides>`
- [ ] Layout shell: sidebar + header + content area

### Task B2: Auth flow

- [ ] Login page with JWT flow
- [ ] Single-flight refresh queue (§8.1 algorithm)
- [ ] CSRF token injection interceptor
- [ ] Role-gated route guards
- [ ] Session expiry notification (2 min before expiry)

### Task B3: Capabilities boot gate

- [ ] `<CapabilitiesGate>` in `App.vue` — blocks until capabilities resolve
- [ ] 200 ms shimmer placeholder
- [ ] On failure → `/error` with retry
- [ ] Backend version skew guard

### Task B4: CI + Docker

- [ ] `ci.yml` — lint → typecheck → unit → contract → build
- [ ] `e2e.yml` — Playwright matrix (chromium/firefox/webkit)
- [ ] `docker.yml` — multi-stage build → push GHCR
- [ ] `docker-compose.yml` referencing BE image

---

## Phase 1: Onboarding + Run + Tasks (Plan C)

### Task C1: Onboarding wizard

- [ ] Five-step wizard at `/onboarding` (Welcome → JavDB → qB → Proxy → First Run)
- [ ] Component test scaffolds per step
- [ ] Post-onboarding hint cards for unconfigured features

### Task C2: Run page

- [ ] Daily / Ad Hoc tabs, each with Standard + Advanced modes
- [ ] Standard: core fields. Advanced: full `SpiderJobPayload` surface
- [ ] "Run on" toggle when `capabilities.ingestion_mode === 'dual'`
- [ ] Live log stream on submit

### Task C3: Tasks page

- [ ] Virtualized table + filters (status/mode/date/search)
- [ ] Row click → Drawer with full log + metadata + SessionId

---

## Phase 1: Sessions + Settings (Plan D)

### Task D1: Sessions page

- [ ] ReportSessions table + state filter
- [ ] Rollback dry-run preview → apply
- [ ] Commit for stuck `finalizing` sessions
- [ ] Detail Drawer with movies/torrents written

### Task D2: Settings — Config

- [ ] Full config editor with section tabs
- [ ] Save triggers BE hot-reload + capabilities refresh
- [ ] Per-field i18n labels via `normalizeSectionKey()` mapping

### Task D3: Settings — Auth

- [ ] Password info display
- [ ] Change password dialog (3 fields, client-side validation, server error display)

### Task D4: Settings — Capabilities + Appearance

- [ ] Capabilities display (GH tier, ingestion mode, features)
- [ ] Light/dark theme toggle
- [ ] Locale selector (zh-CN / en / ja)

---

## Phase 1: Browse (Plan E)

### Task E1: Browse layout + Resolve

- [ ] `/browse` route with BrowseToolbar + BrowseTabs (`?mode=` query sync)
- [ ] Resolve: video code → `search-by-video-code` or URL → `resolve`
- [ ] ResolveCard: detail branch (thumbnail+title+code+actors+tags+magnets) + code-search branch
- [ ] ResolveMagnetTable: size sorter, D1 badge per row, admin-only Download, one-click

### Task E2: D1 status badge composable

- [ ] `useIndexStatus()` — 150 ms debounce, ≤50-href chunks, Map caching
- [ ] `D1StatusDot.vue` — 8 px dot with status color + tooltip

### Task E3: Lists sub-mode

- [ ] ListsTabs (Top / Categories / Tags / Custom URL) with per-tab URL defaults
- [ ] Posts to `/api/parse/url` (NOT `parse/top` etc.)
- [ ] ListsGrid: CSS auto-fill grid, `content-visibility: auto`, per-card D1 badge
- [ ] Load more button advances page

### Task E4: Preview sub-mode

- [ ] PreviewFrame: URL input + Fetch + sandboxed iframe (`sandbox="allow-same-origin"`)
- [ ] "Parse this" hands off to Resolve mode

### Task E5: Browse toolbar

- [ ] Search bar (doubles as Resolve input)
- [ ] Recent searches dropdown (sessionStorage, capped 10)
- [ ] Cookie sync button (admin-only) → `POST /api/explore/sync-cookie`

---

## Phase 1: E2E Journeys

### Journey inventory (13 journeys)

| # | Journey | Key endpoints |
|---|---|---|
| 1 | First-run onboarding (5 steps) | `onboarding/*`, `capabilities`, `config`, `tasks/daily` |
| 2 | Login → Dashboard → Run Daily → watch log | `auth/login`, `tasks/daily`, `tasks/{id}/stream` |
| 3 | Run Ad Hoc + Advanced spider job | `tasks/adhoc`, `jobs/spider`, `jobs/{id}/status` |
| 4 | Browse: Resolve → magnet → one-click | `explore/search-by-video-code`, `explore/resolve`, `explore/download-magnet`, `explore/one-click` |
| 4a | Browse: Lists Top → badges → Cookie sync | `parse/url`, `explore/index-status`, `explore/sync-cookie` |
| 4b | Browse: Preview → Parse this → Resolve | `explore/proxy-page`, `explore/resolve` |
| 5 | Sessions: rollback dry-run → apply | `sessions`, `sessions/{id}/rollback` |
| 5a | Sessions: stuck finalizing → commit force | `sessions/{id}/commit` |
| 5b | Sessions: audit+pending → rollback both | `sessions/{id}/rollback` |
| 6 | Settings: Config edit → save → capabilities refresh | `config`, `config/meta`, `capabilities` |
| 7 | Auth: change password → re-login | `auth/login`, `auth/logout`, `auth/change-password`, `auth/refresh` |
| 8 | Backend down → outage → recovers | `health` |
| 8a | Dismiss hint → reload → confirmed dismissed | `onboarding/dismiss-hint`, `system/state` |

### Data isolation

Each `beforeEach` calls `POST /api/test/reset` (gated on `TEST_MODE=1`). External deps (javdb.com, qB, PikPak, GH API) mocked via `page.route()` interceptors. Fixture responses in `tests/e2e/fixtures/`.

---

## Phase 1: Cutover

- [ ] Publish first FE Docker image to GHCR
- [ ] Update main repo README + `docs/en/self-hoster/web-ui-deploy.md` + `docs/zh/self-hoster/web-ui-deploy.md`
- [ ] **Delete `apps/web/` + `apps/desktop/`** in a single commit referencing ADR-008
- [ ] Remove `electron:dev` and web-related entries from root `package.json`
- [ ] Update CLAUDE.md Architecture section to point at new repo
- [ ] `grep -rn "apps/web\|apps/desktop\|electron:dev" docs/ README.md scripts/` → fix all references
- [ ] Tag main repo + new repo releases simultaneously

---

## Phase 2: Data + Operations + Diagnostics + GH Actions (18 new endpoints)

| Endpoint | Purpose |
|---|---|
| `GET /api/history/movies?q=&filter=` | MovieHistory search |
| `GET /api/history/torrents?q=&filter=` | TorrentHistory search |
| `GET /api/ops/qb/torrents` | qB current torrents (BE-proxied) |
| `POST /api/ops/qb/filter-small` | Trigger qb_file_filter |
| `GET /api/ops/pikpak/queue` | PikPak bridge queue |
| `POST /api/ops/pikpak/transfer` | Trigger PikPak transfer |
| `GET /api/ops/rclone/last` | Last rclone dedup report |
| `POST /api/ops/rclone/run` | Run dedup now |
| `POST /api/ops/email/test` | Send test email |
| `GET /api/ops/email/history` | Notification send history |
| `POST /api/ops/cleanup/stale-sessions` | `{ older_than_hours, dry_run }` |
| `POST /api/ops/cleanup/claim-stages` | `{ dry_run }` |
| `GET /api/diag/javdb-session` | Cookie status & expiry |
| `POST /api/diag/javdb-session/refresh` | Refresh javdb session |
| `GET /api/gh-actions/workflows` | List workflows |
| `GET /api/gh-actions/runs?workflow=` | List runs |
| `POST /api/gh-actions/runs` | Dispatch workflow |
| `GET /api/gh-actions/runs/{id}/logs` | Stream logs |

### Phase 2 E2E journeys (10 journeys: 9–17)

| # | Journey |
|---|---|
| 9 | Operations → qB filter small files |
| 10 | Operations → PikPak transfer |
| 11 | Operations → Rclone dedup |
| 12 | Operations → Email test + history + resend |
| 12a | Operations → Cleanup: stale sessions + claim stages |
| 13 | Diagnostics → parse tester (HTML paste + URL fetch + page-type detect) |
| 14 | Diagnostics → JavDB session refresh (both paths) |
| 15 | Diagnostics → deep health check |
| 16 | GH Actions monitor (workflows + runs + dispatch + logs) |
| 17 | Data → search Movies/Torrents + CSV export |

---

## Phase 3: GH Actions Advanced + Migrations + Analytics (6 new endpoints)

| Endpoint | Purpose |
|---|---|
| `PUT /api/gh-actions/workflows/{name}` | Edit YAML (tier `edit`) |
| `GET /api/gh-actions/secrets` | List secret names + updated-at (tier `admin`) |
| `POST /api/gh-actions/secrets` | Create/update secret (tier `admin`) |
| `DELETE /api/gh-actions/secrets/{name}` | Delete secret (tier `admin`) |
| `GET /api/migrations` | List migrations + applied state |
| `POST /api/migrations/{id}/run` | Run a single migration |
| `GET /api/logs/search?q=` | Global log search |

### Phase 3 E2E journeys (3 journeys: 18–20)

| # | Journey |
|---|---|
| 18 | GH Actions: edit workflow YAML |
| 19 | GH Actions: secrets CRUD |
| 20 | Migrations run + global log search |

---

## CI Workflows

### New repo (`javdb-autospider-web`)

| Workflow | Trigger | Steps |
|---|---|---|
| `ci.yml` | PR / push | lint → typecheck → unit → contract → build |
| `e2e.yml` | PR + nightly | Playwright matrix; nightly also runs `@external` |
| `docker.yml` | Tag push | Multi-stage → GHCR |
| `release.yml` | Manual | Conventional-commits changelog + GH Release |

### Main repo additions

| Workflow | Purpose |
|---|---|
| `publish-api-image.yml` | Build + push BE Docker image on main push |
| `publish-openapi.yml` | Dump `/openapi.json` to docs + GH Release artifact |

---

## Quality Gates

| Gate | Threshold | Enforcement |
|---|---|---|
| Unit coverage (stores/composables/utils) | ≥ 80% line | CI red |
| Phase 1 E2E journeys | All passing | Merge blocker |
| Phase 2 E2E journeys | All passing or skipped with linked issue | CI warning |
| Bundle size — initial route gzip | ≤ 250 KB | CI red |
| Bundle size — total gzip | ≤ 450 KB | CI red (raisable to 600 KB) |
| Lighthouse Performance / Accessibility | ≥ 80 / ≥ 95 | CI warning |

### Bundle size conventions

- Route-level lazy loading mandatory (`() => import(...)`)
- Locale files lazy-loaded (empty init → dynamic import on change)
- No `vfonts` default font in bundle (system font stack)
- Naive UI per-component imports only
- `NDataTable` usage audited (heaviest component; lightweight `<table>` when a simple list suffices)

---

## Real-time Refresh Matrix

| Data | Mechanism | Interval |
|---|---|---|
| Live task log | `useLogStream` | 2 s |
| Task list | `usePolling` | 5 s |
| Sessions list | `usePolling` | 10 s |
| Dashboard overview | `usePolling` | 10 s |
| GH Actions runs | `usePolling` | 15 s |
| Capabilities | One-shot + 5 min TTL + force on Settings save | — |
| MovieHistory / TorrentHistory | User-initiated | — |

All polling pauses on `visibilityState === 'hidden'` and re-fetches on focus.

---

## Timeline

One engineer with AI assistance. Calendar weeks, not full-time.

| Phase | Weeks | Deliverables |
|---|---|---|
| Pre-work (Plan A) | 0–1 | BE Docker image, OpenAPI publishing, capabilities, test/reset, Pydantic tightening |
| Bootstrap (Plan B) | 1–2 | Scaffold, theme, layout, auth flow, boot gate, CI |
| Core pages (Plans C+D) | 3–5 | Onboarding, Run, Tasks, Sessions, Settings |
| Browse (Plan E) | 5–6 | Resolve + Lists + Preview + D1 badges |
| Cutover | 6–7 | FE Docker image, README updates, delete `apps/web/` + `apps/desktop/` |
| Phase 2 | 7–12 | Data, Operations, Diagnostics, GH Actions monitor |
| Phase 3 | When needed | GH Actions advanced, Migrations, log search, statistics |

---

## Endpoint Coverage Matrix (Phase 1)

Every Phase 1 endpoint must appear in at least one E2E journey.

| Endpoint | Journeys |
|---|---|
| `GET /api/capabilities` | 1, 2, 6 |
| `GET /api/onboarding/status` | 1 |
| `POST /api/onboarding/test` | 1 |
| `POST /api/onboarding/complete` | 1 |
| `POST /api/onboarding/dismiss-hint` | 8a |
| `GET/PUT /api/system/state` | 8a |
| `GET /api/sessions` | 5, 5a, 5b |
| `GET /api/sessions/{id}` | 5 |
| `POST /api/sessions/{id}/rollback` | 5, 5b |
| `POST /api/sessions/{id}/commit` | 5a |
| `POST /api/jobs/spider` | 3 |
| `GET /api/jobs/{id}/status` | 3 |
| `POST /api/explore/sync-cookie` | 4a |
| `POST /api/auth/login` | 1, 2, 3, 7 |
| `POST /api/auth/refresh` | 7 |
| `POST /api/auth/logout` | 7 |
| `POST /api/auth/change-password` | 7 |
| `POST /api/tasks/daily` | 1, 2 |
| `POST /api/tasks/adhoc` | 3 |
| `GET /api/tasks` | 2 |
| `GET /api/tasks/{id}` | 2 |
| `GET /api/tasks/{id}/stream` | 2, 3 |
| `GET /api/config` | 1, 6 |
| `GET /api/config/meta` | 6 |
| `PUT /api/config` | 1, 6 |
| `POST /api/explore/resolve` | 4, 4b |
| `POST /api/explore/search-by-video-code` | 4 |
| `POST /api/explore/index-status` | 4, 4a |
| `POST /api/explore/download-magnet` | 4 |
| `POST /api/explore/one-click` | 4 |
| `GET /api/explore/proxy-page` | 4b |
| `POST /api/parse/url` | 4a |
| `GET /api/health` | 8 |

---

## Implementation Progress

_Updated 2026-05-17._

### Shipped

- **Plan A** — BE foundation (main repo). Capabilities, onboarding endpoints, sessions API, test/reset gate, publish-api-image.yml, publish-openapi.yml, Pydantic tightening. Two cleanups remain open (rollback layering, commit side-effect parity).
- **Plan B** — FE bootstrap. Scaffold, theme, layout, auth with single-flight refresh, boot gate, Dashboard skeleton, Docker image, CI.
- **Plan C** — Onboarding + Run + Tasks. Five-step wizard, Run page (Daily/Ad-Hoc × Standard/Advanced), Tasks + log Drawer. E2E journeys 1/2/3 green.
- **Plan D** — Sessions + Settings. Sessions list + drawer + rollback + commit. Config / Auth / Capabilities / Appearance. 52 unit specs.
- **Plan E** — Browse (Resolve / Lists / Preview). All sub-modes + D1 badge composable + E2E journeys 4/4a/4b. 65 unit specs total.
- **Follow-ups**: BE change-password endpoint + FE dialog, per-field config i18n labels (~100 keys), D6 E2E journey scaffolding, Journey 7 (change password E2E).

### Still open

- Phase 1 E2E fixtures for sessions (D6 specs currently `test.skip()`)
- Per-field config descriptions (optional polish)
- Journey 8 (backend-down outage) + Journey 8a (dismiss-hint)

### Not started

- BE Plan A cleanups (rollback layering inversion, commit endpoint side-effect parity)
- Phase 2 / Phase 3
