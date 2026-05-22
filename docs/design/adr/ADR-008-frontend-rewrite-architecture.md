# ADR-008: Frontend Rewrite — Standalone `javdb-autospider-web` Repository

**Status**: Accepted — Phase 1 shipped; Phase 2 planned, Phase 3 deferred (as of 2026-05-19)
**Date**: 2026-05-17 (amended 2026-05-18)
**Deciders**: Brainstorming session (design spec: `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md`)
**Related Implementation Plans**: [IMP-ADR008-01](../impl/archive/IMP-ADR008-01-frontend-phase1-backend-prerequisites.md) (BE prerequisites — completed 2026-05-16), [IMP-ADR008-02](../impl/IMP-ADR008-02-frontend-phase1-completion.md) (Phase 1 completion — feature-complete; cutover pending), [IMP-ADR008-03](../impl/IMP-ADR008-03-frontend-phase2-full-cli-coverage.md) (Phase 2 — planned, not started), [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) (Phase 3 — deferred, depends on Phase 2 dogfooding)

## Outstanding Work

- IMP-ADR008-02 cutover items: E2E fixtures, 2 remaining user journeys, BE cleanups, production cutover from `apps/web/` (deleted) to standalone `javdb-autospider-web` repo.
- IMP-ADR008-03 (Phase 2 — full CLI surface coverage): not yet started.
- IMP-ADR008-04 (Phase 3 — power-user features and analytics): deferred; scope to be decided after Phase 2 dogfooding.

---

## Context

The existing `apps/web/` directory in the monorepo has been stale for 51+ days while the backend gained 40+ commits. The old frontend:

- Exposes roughly 30% of available API functionality.
- Carries duplicate `.js`/`.ts` files from an incomplete TypeScript migration.
- Has a runtime bug in DashboardPage (`i18n t()` shadowed by a `v-for` loop variable).
- Contains placeholder/disabled UI and no automated tests.
- Has a silently broken auth refresh flow (no single-flight queue; concurrent 401s cause cascading retries).

Patching the existing code is more expensive than a rewrite. The system also needs a first-run onboarding wizard, a Browse page for server-parsed javdb results, session rollback UI, and coverage of every CLI-expressible operation — none of which exist today.

---

## Decision

Build a new frontend in a **standalone GitHub repository** (`javdb-autospider-web`), separate from the `JAVDB_AutoSpider_CICD` monorepo. The old `apps/web/` and `apps/desktop/` directories are deleted at cutover.

### D1: Standalone repository (not monorepo)

The FE gets its own repo, CI, and Docker image. The main repo publishes a BE Docker image and `openapi.json` artifact consumed by FE CI.

**Rationale**: A single Vue app does not justify monorepo tooling (pnpm workspaces, nx, turborepo). Separate repos decouple FE release cadence from BE, and the only contract is the OpenAPI schema — enforced by type generation + contract tests.

### D2: Vue 3 + Naive UI

Runtime: Vue 3.5, vue-router 4, Pinia 2, vue-i18n 9, Naive UI 2.40+, axios, date-fns, @vueuse/core.

**Rationale**: Staying on Vue 3 (the only framework the operator knows). Naive UI is the only widely-used Vue 3-native component library that matches the "friendly cards" visual direction — Vuetify is Material-opinionated, Ant Design Vue is enterprise-dense, Element Plus is form-heavy.

### D3: Visual direction — "Friendly Cards" (Direction C)

Notion / Stripe Dashboard style: rounded cards (12–16 px radius), soft palette, light shadows, purple-pink accent (`#7c3aed` primary, `#ec4899` gradient accent), base `#faf9f7` light / `#0e0d12` dark.

Design tokens encoded as Naive UI `themeOverrides` in a single `src/theme/index.ts` file for both light and dark variants. Full token set in Appendix A.

**Rationale**: Operators use the console for 2–5 minutes at a time; a friendly, low-fatigue visual with clear card boundaries and soft status colors reduces cognitive load vs. the dense "admin dashboard" style.

### D4: Three deployment topologies via single codebase

| Topology | `VITE_API_BASE_URL` | BE `INGESTION_MODE` | `capabilities.deployment` |
|---|---|---|---|
| Colocated | `http://api:8100` (compose) | `local` | `colocated` |
| Split | `https://api.example.com` | `local` or `github` | `split` |
| GH-managed | same as split | `github` | `split` / `unknown` |

FE configured solely via `VITE_API_BASE_URL`. The `deployment` field uses neutral terms — never names specific providers.

**Rationale**: Self-hosters run diverse setups. One FE image serving all topologies eliminates per-deployment maintenance.

### D5: Capabilities-driven runtime discovery

`GET /api/capabilities` returns version, ingestion_mode, storage_backend, feature flags, GH Actions tier, and deployment type. FE caches with 5-minute TTL, force-invalidated on Settings save.

Feature gating (sidebar visibility, form fields, action buttons) reads from `capabilitiesStore` — never from build-time constants or URL heuristics.

**Rationale**: Deployment topologies have different feature sets (e.g., no GH Actions in colocated mode, no PikPak unless configured). Runtime discovery keeps the FE codebase agnostic.

### D6: Auth — JWT HS256 + CSRF double-submit + single-flight refresh

Sustains the existing BE auth. FE fixes the broken refresh flow:

- **Single-flight refresh queue**: When a 401 is received, one refresh request fires. All concurrent failing requests are queued and replayed with the new token on success. On refresh failure, all queued requests are rejected and the user is routed to `/login`.
- **CSRF token injection**: Mutating verbs read `csrf_token` from cookie and set `X-CSRF-Token` header.
- **Session expiry notification**: 2 minutes before access token expiry, proactive refresh offer via Naive UI Notification.
- **Role gating**: `router.meta.roles` + nav guard. Readonly users see read-only surfaces; write actions hidden and 403'd.

**Rationale**: The existing BE auth is adequate; the FE was the broken side. Single-flight queue prevents retry storms observed in the old code.

### D7: API conventions

- **No `/v1/` version prefix.** FE compiles against `openapi.json`; mismatch fails CI.
- **Error envelope** for all 4xx/5xx: `{ error: { code, message, details, request_id, trace_id } }`.
- **Cursor-based pagination**: `?cursor=&limit=` → `{ items, next_cursor, total_estimate }`.
- **`X-Request-Id`** generated client-side, echoed by BE, surfaced (first 8 chars) in error toasts.
- **Backend version skew guard**: FE refuses to boot when `capabilities.build.backend_version` < `src/api/min-backend-version.ts`.

**Rationale**: No version prefix avoids route proliferation for a single-consumer API. Cursor pagination is more efficient than offset for large datasets. Client-generated request IDs enable end-to-end correlation without server-side overhead.

### D8: Browse — server-parsed, no interactive iframe

The Browse page renders server-parsed javdb results in FE-controlled DOM. Three sub-modes:

1. **Resolve** — video code → `search-by-video-code` or URL → `resolve`. FE renders detail card + magnet table.
2. **Lists** — category/top/tags pages via `POST /api/parse/url`. FE renders CSS grid of cards.
3. **Preview** — diagnostic: paste URL → `proxy-page` → sandboxed iframe (`sandbox="allow-same-origin"` only). Read-only; "Parse this" hands off to Resolve.

**Rationale**: Embedding navigable javdb.com is incompatible with the BE's `proxy-page` endpoint (single sanitized snapshot with restrictive CSP) and would require a same-origin fetch-proxy with relaxed CSP — an explicit non-goal. Server-parsed approach keeps third-party HTML server-side and renders only FE-controlled DOM.

**Correction from original spec**: §6.3 stated Lists would use `parse/top`, `parse/category`, `parse/tags` — but those endpoints only accept raw HTML (`HtmlPayload`). The actual contract for server-side fetch+parse is `POST /api/parse/url` with `UrlPayload`.

### D9: D1 status badge — FE-rendered, batched viewport observer

Badges rendered in FE DOM (search result cards, list cards, magnet table rows).

- `IntersectionObserver` collects visible card `href`s.
- 150 ms debounce → `POST /api/explore/index-status` with ≤50 hrefs per call.
- Response maps href → `{committed, pending, failed_recent, unknown}`.
- FE maintains `Map<href, status>` with one-shot per-mount caching.
- Badge: 8 px dot. Color: committed `#10b981`, pending `#f59e0b`, failed_recent `#dc2626`, unknown `#9ca3af`.

**Rationale**: Removed any reliance on `postMessage` from iframe, DOM injection into javdb pages, or intercepting third-party scripts. Batched viewport observation bounds request volume.

### D10: State management — 6 Pinia stores + capabilities boot gate

```
stores/
├── auth.ts          JWT, role, user, login/logout/refresh
├── capabilities.ts  /api/capabilities cache, 5min TTL, boot status
├── tasks.ts         Running tasks list + polling scheduler
├── ui.ts            Sidebar collapsed, theme, active toast
├── onboarding.ts    Wizard step, filled fields, test results
└── i18n.ts          Locale, change action
```

Page-local state stays in component-local `ref`s (optionally `useStorage` from @vueuse/core). Never globalized.

**Capabilities boot gate**: `App.vue` renders `<CapabilitiesGate>` that blocks until `capabilities` resolves. On failure → `/error` with retry. Subsequent refreshes are background-only.

**Rationale**: Six stores matches six global concerns. The boot gate prevents race conditions where route guards read empty capability state.

### D11: Error handling — three levels

1. **Single-request** — axios interceptor maps `error.code` → i18n key → Naive UI toast.
2. **Route-level** — reserved routes `/404`, `/forbidden`, `/error` via Vue Router error handler.
3. **App-level outage** — `GET /api/health` fails on boot → full-screen blocker with retry. Prevents silent white-screen.

**Rationale**: The old FE silently white-screened when the backend was down. Three levels ensure every failure mode has a visible, actionable surface.

### D12: No Tailwind / UnoCSS

Naive UI's component primitives + scoped `<style>` blocks suffice for the project scale.

**Rationale**: A second styling system creates "which one owns this border-radius?" maintenance hazard without proportional benefit.

### D13: i18n parity — CI-enforced across zh-CN / en / ja

Three flat JSON locales. Namespaced keys (`dashboard.welcome`, `runs.daily.trigger`, `errors.config.qb_unreachable`). Parity enforced by `scripts/check-i18n-parity.mjs` that hard-fails CI on any locale-asymmetric key set.

BE error codes (machine-readable) mapped to FE translations under `errors.*`. BE log strings stay English. UI chrome uses user locale.

**Rationale**: Three-locale system already existed but had no parity enforcement. Asymmetric locales cause silent runtime failures when a key exists in one locale but not another.

### D14: Onboarding — standalone route, not modal

Five-step wizard at `/onboarding` (Welcome → JavDB session → qBittorrent → Proxy → First Run). Route-based, not modal — shareable URL, resumable mid-flow, works on small mobile viewports.

Re-entry from Settings always available. Post-onboarding hint cards for unconfigured optional features (PikPak, Rclone, SMTP, GH Actions) with dismissible persistence.

### D15: HTTP client — axios + hand-written wrappers, not openapi-fetch

`openapi-typescript` generates type definitions only (`api.gen.ts`). Wrappers in `src/api/` use a shared axios instance with interceptors (auth refresh queue, CSRF injection, error → toast, request_id).

**Rationale**: The interceptor stack is heavier than the type-generation gain from openapi-fetch. Wrappers can be partially codegen'd later.

### D16: Data fetching — three composables, no TanStack Query

- `useApi(url, opts)` — one-shot GET.
- `usePolling(fn, interval)` — list + incremental polling, pauses on `visibilityState === 'hidden'`.
- `useLogStream(jobId, opts)` — log streaming via polling `/api/tasks/{id}/stream`.

**Rationale**: Composables are ~30 lines each. TanStack Query adds 15+ KB gzip for caching semantics this app does not need (operator console, not data-heavy dashboard).

### D17: Optimistic updates off by default

Operator-oriented UI prefers "wrote it → saw BE confirm" over fast-then-rollback. Exceptions only for low-risk UI prefs: sidebar collapse, theme, dismiss-hint.

### D18: GitHub Actions integration via direct httpx (Phase 2)

GH Actions endpoints (`list workflows`, `list runs`, `dispatch`, `stream logs`, plus Phase 3 `edit YAML` and `secrets CRUD`) use direct `httpx` calls to the GitHub REST API v3. No third-party library (PyGithub, ghapi).

Token reuses the existing `GIT_PASSWORD` PAT from `config.py`. A separate `GH_ACTIONS_TOKEN` config key is not added unless operators report permission scope conflicts.

**Rationale**: Only 6–7 API calls needed across all three phases. A library adds dependency weight without proportional value. The PAT is already configured and typically carries the `workflow` scope.

### D19: Email notification history table (Phase 2)

New `EmailNotificationHistory` table in `operations.db` (D1 migration `0018`) records every email send attempt: recipient, subject, status (`sent`/`failed`/`resent`), error, timestamps, session ID. The email sending code in `javdb/integrations/notify/email.py` appends a row after each `smtp.send_message()`.

Enables `GET /api/ops/email/history` (list with status filter) and `POST /api/ops/email/{id}/resend` (replay a failed notification).

**Rationale**: Spec Journey 12 requires "resend a failed notification." Without persistent history, resend is impossible and operators have no visibility into send failures.

### D20: History search via SQL LIKE, not FTS (Phase 2)

`GET /api/history/movies` and `GET /api/history/torrents` use SQL `LIKE` / `INSTR` for text search on VideoCode, ActorName, SupportingActors. Cursor-based pagination via keyset on `Id`.

No Full-Text Search (FTS5). SQLite supports it but Cloudflare D1 does not. Maintaining two search code paths is not justified for the expected data scale (~10K movies, ~50K torrents).

**Rationale**: `LIKE` search on indexed columns at 10K–50K scale completes in <50ms. FTS would add complexity without measurable user benefit.

### D21: Data CSV export via BE-side streaming (Phase 2)

`GET /api/history/movies/export` and `GET /api/history/torrents/export` return `StreamingResponse` with `text/csv` content type. The BE generates CSV rows from the full filtered dataset (no pagination limit). The FE triggers a browser download via blob URL.

**Rationale**: Operators export CSV for external analysis and expect the full dataset, not just the current page. Server-side generation ensures data consistency and handles datasets too large for client-side assembly.

---

## Consequences

### Positive

- Clean codebase with full test coverage from day one.
- Every CLI-expressible operation accessible via UI across three phased releases.
- Deployment-mode-agnostic: single FE image works for all self-hoster topologies.
- Auth refresh flow fixed — no more cascading retry storms.
- i18n parity enforced at CI level — no locale can lag.

### Negative

- Two-repo maintenance: BE changes that affect API contracts require coordinated releases.
- Onboarding wizard + Browse page are BE-heavy — tight coupling to parse/explore endpoint response shapes.
- Deleting `apps/web/` + `apps/desktop/` is a one-way door (mitigated by git history).

### Neutral

- Electron desktop shell dropped. Re-addable later as a thin Tauri/Electron wrapper if needed.
- Proxy Coordinator UI remains a separate surface — this rewrite is complementary, not a replacement.

---

## Risks

1. **Browse Lists mode depends on heavy server-side parsing.** The 6 parse endpoints currently lack tight Pydantic response models. **Mitigation**: pre-work tightens response models before FE code lands; Phase 2 E2E enforces.
2. **D1 status batch endpoint cost.** Fast-paging Lists users could fire many index-status calls. **Mitigation**: BE caches ~10 s per href; FE caches in-memory per session.
3. **GH Actions log streaming rate limits** (5000 req/h). **Mitigation**: poll only the currently-viewed run.
4. **OpenAPI type generation assumes clean `openapi.json`.** **Mitigation**: BE response model tightening in pre-work.
5. **Sessions rollback API wraps a CLI with rich semantics (15+ flags).** **Mitigation**: refactor rollback core logic into a library callable from both CLI and API handler.
6. **`POST /api/test/reset` footgun.** **Mitigation**: gated on `TEST_MODE=1` env var; route returns 404 otherwise; integration test verifies.

---

## Resolved Questions

- **Backend version skew**: FE refuses to boot when `capabilities.build.backend_version` < minimum. Boot gate renders "please upgrade" page.
- **i18n for BE errors**: BE error codes mapped to FE translations. Log strings stay English.
- **Rollback library layering inversion (updated 2026-05-20)**: the original `javdb/storage/rollback/core.py` -> `apps.cli.db._session_helpers` import has been removed. The interim helper path is `javdb.storage.rollback.session_helpers`, while `apps.cli.db._session_helpers` remains a shim. [ADR-014](ADR-014-storage-cli-layering.md) tracks final convergence to `javdb.storage.sessions.lifecycle_helpers` and deletion of both legacy wrappers.
- **Commit endpoint side-effect parity**: `javdb/storage/sessions/commit.py` already has `fanout_claims` and `emit_metrics` flags; HTTP endpoint defaults them to `False`. Fix: default to `True` in the API request body for CLI parity. Tracked in [IMP-ADR008-02](../impl/IMP-ADR008-02-frontend-phase1-completion.md) Task 5.
- **PikPak endpoint granularity**: Batch mode only (`POST /api/ops/pikpak/transfer { days, dry_run }`). Single-torrent transfer deferred.
- **Rclone endpoint granularity**: Single endpoint with flags (`POST /api/ops/rclone/run { scan, report, execute, dry_run }`). FE presets "Quick Dedup" and "Advanced" mode.

## Open Questions

- **Multi-tab behavior**: Two tabs double request volume. BroadcastChannel-shared polling recommended but deferred to Phase 3. See [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) Task 6.
- **D1 status caching TTL**: ~10 s server / session client proposed. Needs validation under realistic Browse-Lists usage post-Phase 2.
- **Global log search storage**: Log persistence strategy (DB table vs. filesystem vs. structured rows) not decided. Depends on Phase 2 log volume observations. Deferred to Phase 3 design session → ADR-009. See [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) Task 4.
- **Statistics dashboard scope**: Candidate metrics identified (run success rate, history growth, dedup freed bytes) but scope and chart library not finalized. Deferred to Phase 3 design session → ADR-010. See [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) Task 5.

---

## Appendix A — Design Tokens (Direction C)

| Token | Light | Dark |
|---|---|---|
| Background | `#faf9f7` | `#0e0d12` |
| Surface | `#ffffff` | `#1a1820` |
| Primary accent | `#7c3aed` | `#7c3aed` |
| Secondary accent | `#ec4899` | `#ec4899` |
| Border | `#e5dccf` | `#2a2730` |
| Border radius | 12–16 px (8 px for inputs) | same |
| Shadow | `0 1px 2px rgba(15,23,42,0.04), 0 6px 18px rgba(15,23,42,0.06)` | same |
| Status green | `#10b981` | same |
| Status red | `#dc2626` | same |
| Status blue | `#3b82f6` | same |
| Status amber | `#f59e0b` | same |
