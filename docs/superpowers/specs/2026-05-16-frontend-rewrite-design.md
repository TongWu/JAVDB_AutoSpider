# Frontend Rewrite Design — `javdb-autospider-web`

**Date:** 2026-05-16
**Author:** brainstorming session (Claude + your_github_username)
**Status:** Approved — implementation plan to follow
**Replaces:** `apps/web/` in the main monorepo (frozen since 2026-03-26)

---

## 1. Overview

A full rewrite of the JAVDB AutoSpider Vue frontend into a new, standalone repository (`javdb-autospider-web`). The existing `apps/web/` directory has been stale for 51 days while the backend gained 40+ commits, leaving the UI exposing roughly 30% of available functionality, carrying duplicate `.js`/`.ts` files from an incomplete TypeScript migration, a runtime bug in DashboardPage (i18n `t()` shadowed by a `v-for` loop variable), placeholder/disabled UI, no automated tests, and a silently broken auth refresh flow. Patching is more expensive than starting over.

The new frontend is the **primary user-facing console** for the system. The Proxy Coordinator UI remains a separate runtime/proxy monitoring surface — this rewrite is complementary to it, not a replacement.

## 2. Goals

- Cover **every** operation currently expressible via the CLI or backend API through the UI, phased across three releases.
- Provide a **soft, onboarding-friendly visual direction** (Notion / Stripe Dashboard style: rounded cards, soft palette, light shadows, purple-pink accent) with full responsiveness down to mobile.
- Support **three deployment topologies** behind a single FE codebase: bundled Docker (FE+BE co-located), split deploy (FE on static host, BE on user VPS/serverless), and GitHub Actions ingestion mode (BE dispatches workflows instead of running spider locally).
- Make the FE **deployment-mode-agnostic** — it talks to a single abstract backend, discovers capabilities at runtime via `GET /api/capabilities`, and never embeds vendor-specific assumptions.
- Establish **automated test coverage for every backend endpoint** (unit + contract + E2E) so feature regressions are caught at CI.
- Keep architecture small enough that one engineer (with AI assistance) can hold it in head.

## 3. Non-Goals

- Multi-tenant user system, public sign-up, billing, or any consumer-facing surface — this is a self-hoster console only.
- React/Svelte migration — staying on Vue 3 (the only widely-used Vue 3-native option that matches the design direction is Naive UI).
- Replacing the Proxy Coordinator UI — that remains its own surface.
- Rewriting the backend — only the new endpoints listed in §8 are added to the main repo.
- Native mobile apps — full responsive web is the target.
- **Developer/debug-only CLIs as first-class UI features** — `apps/cli/fetch_page.py` and `apps/cli/config_generator.py` stay CLI-only. They serve developer workflows (one-shot HTML fetching for parser debugging, generating `config.py` from environment for CI) that do not benefit from a web UI.
- **Interactive embedded third-party browsing.** The Browse page does not embed a navigable javdb.com inside an iframe or webview. javdb's CORS, CSP and login flow make this both technically fragile and security-risky. We render server-parsed results in our own DOM instead — see §6.3.

## 4. Repository & Deployment Topology

### 4.1 New repo: `javdb-autospider-web`

Standalone GitHub repo, separate from `JAVDB_AutoSpider_CICD` monorepo. Skeleton:

```
javdb-autospider-web/
├── README.md
├── LICENSE
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html
├── public/
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/
│   ├── stores/                 # Pinia (6 stores, see §9)
│   ├── api/                    # axios client + typed wrappers
│   ├── composables/
│   ├── i18n/                   # zh-CN / en / ja
│   ├── theme/                  # Naive UI themeOverrides (direction C tokens)
│   ├── components/
│   ├── pages/                  # one folder per route
│   └── types/                  # incl. api.gen.ts (openapi-typescript output)
├── tests/
│   ├── unit/                   # Vitest
│   ├── contract/               # OpenAPI schema verification against running BE
│   └── e2e/                    # Playwright (~20 user journeys)
├── docker/
│   ├── Dockerfile              # multi-stage build → nginx static
│   └── nginx.conf
├── docker-compose.yml          # FE image + remote BE image reference
├── .github/workflows/          # ci, e2e, docker, release
└── docs/
    ├── deploy-colocated.md
    ├── deploy-split.md
    └── deploy-github-mode.md
```

No monorepo tooling (pnpm workspaces, nx, turborepo) — single Vue app does not justify it.

### 4.2 docker-compose lives in the new repo

`docker-compose.yml` in `javdb-autospider-web/` references the BE image published by the main repo:

```yaml
services:
  api:
    image: ghcr.io/your_github_username/javdb-autospider-api:latest
    # env, volumes, ports per main repo's existing deployment docs
  web:
    build: ./docker
    ports: ["5173:80"]
    depends_on: [api]
    environment:
      VITE_API_BASE_URL: http://api:8100
```

This requires two new workflows in the main repo (`JAVDB_AutoSpider_CICD`):

- `publish-api-image.yml` — build and push BE image to GHCR on every main push.
- `publish-openapi.yml` — dump `/openapi.json` to `docs/api/openapi.json` + GH Release artifact, consumed by FE CI for type generation.

### 4.3 Three deployment topologies

FE is configured solely via `VITE_API_BASE_URL`. BE behavior varies by `INGESTION_MODE` env.

| Topology      | `VITE_API_BASE_URL`       | BE `INGESTION_MODE` | Storage   | `capabilities.deployment` |
|---------------|---------------------------|---------------------|-----------|--------------------------|
| Colocated     | `http://api:8100` (compose internal) | `local`             | SQLite or D1 | `colocated` |
| Split         | `https://api.example.com` (user-hosted BE) | `local` or `github` | D1        | `split` |
| GH-managed    | same as split             | `github`            | D1        | `split` (or `unknown`) |

The `deployment` field uses neutral terms (`colocated` / `split` / `unknown`) — never names specific providers like Vercel or Cloudflare.

## 5. Tech Stack

### 5.1 Runtime dependencies

```json
{
  "vue": "^3.5",
  "vue-router": "^4",
  "pinia": "^2",
  "vue-i18n": "^9",
  "naive-ui": "^2.40",
  "vfonts": "^0.1",
  "axios": "^1",
  "date-fns": "^3",
  "@vueuse/core": "^11"
}
```

### 5.2 Dev dependencies

```json
{
  "typescript": "^5",
  "vite": "^6",
  "@vitejs/plugin-vue": "^5",
  "vue-tsc": "^2",
  "openapi-typescript": "^7",
  "vitest": "^2",
  "@vue/test-utils": "^2",
  "@playwright/test": "^1.48",
  "eslint": "^9",
  "@typescript-eslint/parser": "^8",
  "eslint-plugin-vue": "^9",
  "prettier": "^3"
}
```

### 5.3 Key decisions

- **No Tailwind / UnoCSS.** Naive UI's component primitives + scoped `<style>` blocks suffice. A second styling system creates a "which one owns this border-radius?" maintenance hazard.
- **No Storybook.** Project scale does not justify it; components-as-documentation is enough.
- **No `pinia-persistedstate`.** A 10-line `useStorage` from `@vueuse/core` covers what we need.
- **Theme is one config file**, `src/theme/index.ts`, exporting Naive UI `themeOverrides` for light/dark variants encoding direction C: 12-16px border radius, soft shadow `0 1px 2px rgba(15,23,42,0.04), 0 6px 18px rgba(15,23,42,0.06)`, primary `#7c3aed` with `#ec4899` gradient accent, base background `#faf9f7` light / `#0e0d12` dark. Applied at the root via `<n-config-provider :theme-overrides="overrides">`.
- **i18n keys are namespaced** (`dashboard.welcome`, `runs.daily.trigger`, `errors.config.qb_unreachable`). Three flat JSON locales (`zh-CN.json`, `en.json`, `ja.json`). **Parity enforced by `@intlify/eslint-plugin-vue-i18n`'s `missing-keys` rule + a 30-line `scripts/check-i18n-parity.mjs` that hard-fails CI on any locale-asymmetric key set.** No locale is allowed to lag.
- **HTTP client = axios + hand-written typed wrappers**, not openapi-fetch. `openapi-typescript` generates `src/types/api.gen.ts` (shape definitions only). `src/api/` has per-endpoint wrapper functions like `getTaskById(id: string): Promise<paths['/api/tasks/{id}']['get']['responses']['200']['content']['application/json']['data']>`. The wrappers use a shared `httpClient` (axios instance) with interceptors. The tradeoff is repetitive wrapper boilerplate vs. losing axios's interceptor + cancel-token ergonomics. We accept the boilerplate because the interceptor stack (auth refresh queue, CSRF injection, error → toast, request_id) is heavier than the type-generation gain. Wrappers can be partially generated by a small codegen script once API stabilizes.
- **Axios interceptors** handle: CSRF header injection on mutating verbs, 401 refresh queue + replay (single-flight — algorithm in §8.1), uniform error → toast pipeline, `X-Request-Id` UUID generation per request.

## 6. Information Architecture

### 6.1 Top-level navigation (left sidebar, grouped)

```
🏠 Home                          Dashboard
⚡ Run                           Daily + Ad Hoc (mode-aware)
📋 Activity
   ├ Tasks                       All task runs + log viewer
   └ Sessions                    ReportSessions + rollback/commit
🌐 Browse                        Embedded javdb browser
💾 Data
   ├ Movies                      MovieHistory browse/search
   └ Torrents                    TorrentHistory browse/search
⚙️ Operations
   ├ qBittorrent                 qb_uploader status + qb_file_filter
   ├ PikPak                      pikpak_bridge queue + history
   ├ Rclone                      dedup status + manual run
   ├ Email                       notification test + history
   └ Cleanup                     on-demand: stale-session cleanup + claim-stage sweep
🔧 Diagnostics
   ├ Health                      deep health check trigger
   ├ Parse tester                6 parse endpoints + URL parse + low-level spider job submit (Advanced)
   └ JavDB session               cookie status + refresh + sync-cookie from Browse
🚀 GitHub Actions   (conditional, capabilities.gh_actions.tier != 'none')
   ├ Runs                        list/dispatch/logs (tier monitor+)
   ├ Workflows                   YAML editor (tier edit+)
   └ Secrets                     secrets CRUD (tier admin)
🛠 Settings
   ├ Config                      full config editor
   ├ Auth                        password change, session info
   ├ Capabilities                GH tier toggle, INGESTION_MODE display
   └ Appearance                  light/dark, i18n
```

Nine top-level groups. Sub-items rendered as in-page tabs where it reduces clicks (e.g. Activity has Tasks/Sessions tabs at the page top, not two separate sidebar items).

### 6.2 Phase breakdown

| Phase | Pages | Goal |
|-------|-------|------|
| **Phase 1 (MVP)** | Login, Onboarding, Home, Run, Tasks, Sessions, Browse, Settings/Config, Settings/Auth, layout shell + theme + i18n | Replace existing `apps/web/` with parity + fixed bugs + onboarding |
| **Phase 2** | Data (Movies/Torrents), Operations (qB/PikPak/Rclone/Email), Diagnostics (all), GH Actions tier `monitor` | Full coverage of current CLI surface |
| **Phase 3** | GH Actions tiers `edit`/`admin`, Migrations, Settings/Capabilities, Settings/Appearance, global log search, statistics charts | Power-user features + analytics |

Estimated total Vue component files at completion: 35-45. Estimated routes: ~25 (with ~10 being subroutes of grouped sections).

### 6.3 Key page sketches

**Home / Dashboard** — status pill + version + 4 stat cards (Running / Today / Failed / Pending sessions) + recent 10 runs + recent 5 sessions + quick actions. If `onboarded === false`, top-section insert with "Resume setup" cards.

**Run** — tabs for Daily / Ad Hoc. Each tab has two modes: **Standard** (default) and **Advanced**.

- *Daily Standard form*: proxy override, dry-run, `start_page` (default 1), `end_page` (default 10). Posts to existing `POST /api/tasks/daily` (`DailyTaskPayload`).
- *Ad Hoc Standard form*: URL input, proxy override, dry-run, `ignore_release_date` checkbox. Posts to `POST /api/tasks/adhoc` (`AdhocTaskPayload`).
- *Advanced (both tabs)*: collapsible section exposing the full `SpiderJobPayload` surface (`phase`, `ignore_history`, `max_movies_phase1`, `max_movies_phase2`, `enable_dedup`, `redownload_threshold`, `no_rclone_filter`, `disable_all_filters`, plus everything above). Posts to `POST /api/jobs/spider`. Each control has a tooltip linking to the CLI flag it maps to.
- "Run on" toggle (Local Worker | GitHub Actions) visible only when `capabilities.ingestion_mode === 'dual'`. On submit, form collapses to a run-card + live log stream.

**Tasks** — virtualized table + filters (status / mode / date / search). Row click opens Drawer with full log + metadata + associated SessionId.

**Sessions** — ReportSessions table + state filter. Row actions: rollback (with dry-run preview), commit (for stuck finalizing), view detail. Detail Drawer lists movies/torrents written in that session.

**Browse** — server-parsed resolver and list explorer. No interactive third-party iframe. Three sub-modes via in-page tabs:

1. *Resolve* (default) — search bar accepts either a video code (e.g. `ABC-123`) or a javdb URL. Code submits to `POST /api/explore/search-by-video-code`; URL submits to `POST /api/explore/resolve`. Result renders as a FE-styled detail card: thumbnail, title, code, release date, actors, tags, magnets table sortable by size/quality, D1 status badge for each magnet row, and actions per magnet (`Add to qBittorrent` → `POST /api/explore/download-magnet`) plus a top-level `One-click download` (→ `POST /api/explore/one-click`).
2. *Lists* — server-parsed lists of javdb's category / top / tag pages. Tabs: `Top` / `Categories` / `Tags` / `Custom URL`. Each tab posts to `/api/parse/top`, `/api/parse/category`, `/api/parse/tags`, or `/api/parse/url` respectively, with the BE doing the page fetch + parse. Result renders as a virtualized grid of result cards. D1 status badge on each card, color coded (green = committed, yellow = pending session, red = errored prior run, gray = unknown). Badge data fetched in batches via `POST /api/explore/index-status` as cards enter the viewport (IntersectionObserver).
3. *Preview* — diagnostic-only. Paste a raw javdb URL → BE fetches via `GET /api/explore/proxy-page` → FE renders the sanitized snapshot in a `sandbox`-attributed iframe (read-only). No interaction or navigation. A `Parse this` button above the iframe switches to *Resolve* for the same URL. Used to verify what BE actually sees before debugging a parse failure.

The previous design assumed an interactive embedded javdb browser with DOM-injected overlays. That is incompatible with the BE's existing `proxy-page` endpoint (which returns a single sanitized snapshot with `frame-ancestors 'self'` and `connect-src 'self'`) and would require a same-origin fetch-proxy with relaxed CSP that we explicitly do not want. The new design keeps all third-party HTML server-side and renders only FE-controlled DOM.

Top toolbar (visible across all three sub-modes): search bar (also serves as the *Resolve* input), `Recent searches` dropdown, `Cookie sync` button → `POST /api/explore/sync-cookie` (uploads the current javdb session to BE — useful when user has refreshed cookie elsewhere).

**Data → Movies / Torrents** — table + full-text search + tag/actor/category filters + filter by SessionId + CSV export.

**Operations sub-pages** — each is a focused card view. qB: torrent list + small-file filter button. PikPak: transfer queue + history. Rclone: last dedup report + manual run. Email: test send + send history.

**Diagnostics → Parse tester** — left panel: paste HTML or fill URL + parser selector (index / detail / category / top / tags / auto-detect). Right panel: structured result as collapsible JSON tree.

**GH Actions → Runs** — workflows list (cron + last run status) + dispatcher (form auto-generated from `workflow_dispatch.inputs`) + run log streaming.

**Settings → Config** — six tabs (Core / Proxy Pool / qB / PikPak / Rclone / SMTP). Save triggers BE hot-reload. Proxy pool tab reuses the design of the existing `ProxyPoolEditor` component.

### 6.4 D1 status badge rendering

Badges are FE-rendered into our own DOM (search result cards and list cards in §6.3 Browse). No injection into third-party HTML.

- IntersectionObserver collects visible card `href`s.
- Every 150 ms (debounced), the queue is flushed: `POST /api/explore/index-status` with up to 50 hrefs per call.
- Response maps each href to a state in `{committed, pending, failed_recent, unknown}`. FE maintains a `Map<href, status>` in component-local state, keyed for stable re-render.
- Badge renders as a 6 px dot in the card corner. Color tokens: committed = `#10b981`, pending = `#f59e0b`, failed_recent = `#dc2626`, unknown = `#9ca3af`. Hover tooltip shows `SessionId · last action timestamp · last error (if any)`.
- The same mechanism powers the magnets-table badge in *Resolve* mode: each magnet's torrent hash is independently statused.

Removed: any reliance on `postMessage` from iframe, on DOM injection into javdb pages, or on intercepting third-party scripts.

## 7. Onboarding

### 7.1 Detection & entry

`GET /api/onboarding/status` returns `{ completed: bool, required_missing: string[], skippable_missing: string[] }`. On app boot, if `completed === false`, router redirects to `/onboarding`.

A standalone route (not a modal) — modal layouts struggle on small mobile viewports and a route is shareable (`/onboarding?step=3`), resumable mid-flow, and easier to deep-link from external docs.

Re-entry from `Settings → Config → Re-run setup` is always available.

### 7.2 Five steps

1. **Welcome** — one-paragraph explainer + diagram (Spider → DB → qBittorrent → notifications) + "Configure now" / "I'll do it later" (the latter dismisses but leaves a persistent banner).
2. **JavDB session** — tab A (paste `_jdb_session` cookie + Test) / tab B (credentials + headless login flow). Skippable but warned.
3. **qBittorrent** — URL + username + password + "allow self-signed certs" + Test connection.
4. **Proxy** — mode selector (None / Single / Pool); Pool expands the `ProxyPoolEditor`; `PROXY_MODULES` checkboxes (`spider` / `qbittorrent` / `pikpak`).
5. **First Run** — choice: "Run a real daily ingestion now (first 3 pages)" → calls `POST /api/tasks/daily` with `start_page=1, end_page=3` using the existing `DailyTaskPayload` fields (no new parameter needed) → on completion lands on Home with the run visible; or "Explore the console" → skip directly to Home.

On completion, BE writes `system_state.onboarded = true`.

### 7.3 Visual reference

A mockup of Step 3 (qBittorrent) was confirmed in the brainstorming session — see commit history for the artifact, or recreate via `npm run dev` once Phase 1 lands.

### 7.4 Non-wizard reminders (post-onboarding nudges)

Optional integrations (PikPak, Rclone, SMTP, GitHub Actions) are not in the wizard. The Dashboard shows dismissible hint cards for unconfigured features. Dismissals persist via `POST /api/onboarding/dismiss-hint`, stored under `system_state.dismissed_hints` (string array).

## 8. Auth, Capabilities, API Contract

### 8.1 Auth (sustained from existing BE)

Existing JWT HS256 + admin/readonly + CSRF double-submit cookie + sliding-window rate limit stays. FE fixes:

- **Single-flight refresh queue.** Algorithm:

  ```ts
  // src/api/refresh-queue.ts (pseudocode)
  let refreshing: Promise<string> | null = null
  const queue: Array<{ config: AxiosRequestConfig; resolve: (r:any)=>void; reject: (e:any)=>void }> = []

  axios.interceptors.response.use(undefined, async (err) => {
    const original = err.config
    if (err.response?.status !== 401) throw err
    if (original.url?.endsWith('/api/auth/refresh')) throw err   // avoid infinite loop
    if (original._retried) throw err                              // single retry only

    if (!refreshing) {
      refreshing = axios.post('/api/auth/refresh').then(r => r.data.access_token)
        .finally(() => { refreshing = null })
      refreshing.then(token => {
        auth.setAccessToken(token)
        const q = queue.splice(0)
        q.forEach(({ config, resolve, reject }) => {
          config.headers!['Authorization'] = `Bearer ${token}`
          config._retried = true
          axios.request(config).then(resolve).catch(reject)
        })
      }).catch(e => {
        queue.splice(0).forEach(({ reject }) => reject(e))
        auth.logout()
        router.push('/login')
      })
    }

    return new Promise((resolve, reject) => queue.push({ config: original, resolve, reject }))
  })
  ```

  Notes:
  - `/api/auth/refresh` requests bypass the queue to prevent infinite loops.
  - `_retried` flag prevents secondary retry storms.
  - Mutating verbs (POST/PUT/PATCH/DELETE) are retried — accepted as a tradeoff since BE is expected to be idempotent for these flows; if a specific endpoint is non-idempotent, FE callers can opt out via `config.skipRefreshRetry = true`.
  - On refresh failure, `Authorization` header is stripped before the redirect to `/login`.
- **CSRF token injection.** Mutating verbs (POST/PUT/PATCH/DELETE) read `csrf_token` from cookie and set `X-CSRF-Token` header.
- **Session expiry notification.** Two minutes before access token expiry, Naive UI Notification offers "Stay signed in" (proactive refresh).
- **Role gating** via `router.meta.roles` + nav guard. Readonly users see Data / Tasks / Sessions; write actions hidden in UI and 403'd by BE.

### 8.2 Capabilities

```
GET /api/capabilities
```

```json
{
  "version": "2.0.0",
  "ingestion_mode": "local",
  "gh_actions": {
    "tier": "none",
    "repo": null,
    "token_configured": false
  },
  "storage_backend": "sqlite",
  "features": {
    "pikpak": false,
    "rclone": false,
    "smtp": true,
    "proxy_pool": true,
    "javdb_login": true,
    "proxy_preview": true
  },
  "deployment": "colocated",
  "build": {
    "frontend_version": "0.1.0",
    "backend_version": "2.1.3",
    "git_sha": "abc123"
  }
}
```

Field values:

- `ingestion_mode`: `local` | `github` | `dual`
- `gh_actions.tier`: `none` | `monitor` | `edit` | `admin` (each is a strict superset of the prior). Named by the scope of GitHub resources the FE can touch — never letter-coded.
- `storage_backend`: `sqlite` | `d1` | `dual`
- `deployment`: `colocated` | `split` | `unknown`

FE caches in `capabilitiesStore` with 5-minute TTL. Forced invalidation on Settings save.

### 8.3 API conventions

- **No `/v1/` version prefix.** FE compiles against BE's `openapi.json`; mismatch fails CI. Backend version reported separately via `capabilities.build.backend_version`.
- **Error envelope** for all 4xx/5xx:
  ```json
  {
    "error": {
      "code": "config.qb_unreachable",
      "message": "Cannot reach qBittorrent at https://...",
      "details": { "url": "...", "reason": "Connection refused" },
      "request_id": "req_01HXY...",
      "trace_id": null
    }
  }
  ```
- **Pagination is cursor-based:**
  ```
  GET /api/tasks?cursor=eyJpZCI6MTIzfQ&limit=50
  → { "items": [...], "next_cursor": "...", "total_estimate": 1234 }
  ```
- **`X-Request-Id`** is generated client-side per request, echoed by BE in logs and error responses, surfaced (first 8 chars) in error toasts.

### 8.4 New backend endpoints by phase

**Phase 1 — 10 new BE endpoints + wiring of 3 existing ones**

| Endpoint | Status | Purpose |
|----------|--------|---------|
| `GET /api/capabilities` | new | Boot-time + post-config-change |
| `GET /api/onboarding/status` | new | First-run detection |
| `POST /api/onboarding/test` | new | Test a single component (`javdb`/`qb`/`proxy`/`smtp`) |
| `POST /api/onboarding/complete` | new | Mark setup done |
| `POST /api/onboarding/dismiss-hint` | new | Dismiss a hint card |
| `GET/PUT /api/system/state` | new | Generic KV (dismissed_hints, prefs) |
| `GET /api/sessions` | new | List ReportSessions w/ `?state=` filter + cursor pagination |
| `GET /api/sessions/{id}` | new | Session detail: writes (pending + committed + audit), run identity, error log |
| `POST /api/sessions/{id}/rollback` | new | Body: `{ dry_run: bool, include_pending: bool, restore_from_audit: bool }` — mirrors `apps/cli/rollback.py` flags. Returns plan + applied-actions list. |
| `POST /api/sessions/{id}/commit` | new | Body: `{ force: bool, drop_pending: bool }` — mirrors `apps/cli/commit_session.py` |
| `POST /api/jobs/spider` | **exists** | Wire Run page's Advanced tab to existing rich `SpiderJobPayload` (phase, ignore_history, max_movies_phase1/2, enable_dedup, redownload_threshold, no_rclone_filter, disable_all_filters) |
| `GET /api/jobs/{job_id}/status` | **exists** | Poll spider job status from Run's Advanced tab |
| `POST /api/explore/sync-cookie` | **exists** | Wire to Browse toolbar `Cookie sync` button |

**Phase 2 — 18 new BE endpoints + wiring of 1 existing**

| Endpoint | Status | Purpose |
|----------|--------|---------|
| `GET /api/history/movies?q=&filter=` | new | MovieHistory search |
| `GET /api/history/torrents?q=&filter=` | new | TorrentHistory search |
| `GET /api/ops/qb/torrents` | new | qB current torrents (BE-proxied) |
| `POST /api/ops/qb/filter-small` | new | Trigger qb_file_filter |
| `GET /api/ops/pikpak/queue` | new | PikPak bridge queue |
| `POST /api/ops/pikpak/transfer` | new | Trigger PikPak transfer |
| `GET /api/ops/rclone/last` | new | Last rclone dedup report |
| `POST /api/ops/rclone/run` | new | Run dedup now |
| `POST /api/ops/email/test` | new | Send test email |
| `GET /api/ops/email/history` | new | Notification send history |
| `POST /api/ops/cleanup/stale-sessions` | new | Wrap `apps/cli/cleanup_stale_in_progress.py` — body: `{ older_than_hours, dry_run }` |
| `POST /api/ops/cleanup/claim-stages` | new | Wrap `apps/cli/sweep_movie_claim_stages.py` — body: `{ dry_run }` |
| `GET /api/diag/javdb-session` | new | Cookie status & expiry |
| `POST /api/diag/javdb-session/refresh` | new | Refresh javdb session |
| `POST /api/login/refresh` | **exists** | Headless javdb login — surfaced from Diagnostics → JavDB session |
| `GET /api/gh-actions/workflows` | new | List workflows |
| `GET /api/gh-actions/runs?workflow=` | new | List runs |
| `POST /api/gh-actions/runs` | new | Dispatch workflow |
| `GET /api/gh-actions/runs/{id}/logs` | new | Stream logs |

**Phase 3 — 6 new endpoints**

| Endpoint | Purpose |
|----------|---------|
| `PUT /api/gh-actions/workflows/{name}` | Edit YAML (tier `edit`) |
| `GET /api/gh-actions/secrets` | List secret names + updated-at (values opaque) (tier `admin`) |
| `POST /api/gh-actions/secrets` | Create/update secret (tier `admin`) |
| `DELETE /api/gh-actions/secrets/{name}` | Delete secret (tier `admin`) |
| `GET /api/migrations` | List migrations + applied state |
| `POST /api/migrations/{id}/run` | Run a single migration |
| `GET /api/logs/search?q=` | Global log search across stored task logs |

## 9. State, Data Flow, Error Handling

### 9.1 Pinia stores (6) and the capabilities boot gate

```
src/stores/
├── auth.ts             JWT, role, user, login/logout/refresh
├── capabilities.ts     /api/capabilities cache, 5min TTL, boot status
├── tasks.ts            Running tasks list + polling scheduler
├── ui.ts               Sidebar collapsed, theme, active toast
├── onboarding.ts       Wizard step, filled fields, test results
└── i18n.ts             Locale, change action
```

Page-local state (table filters, form drafts, modal toggles) stays in component-local `ref`s, optionally persisted to sessionStorage via `useStorage` from `@vueuse/core`. Never globalized.

**Capabilities boot gate.** The layout shell blocks until `capabilities` has resolved at least once. Concretely:

1. `App.vue` renders a `<CapabilitiesGate>` wrapper around `<RouterView>`. The gate shows a 200 ms shimmer placeholder of the sidebar layout.
2. Gate dispatches `capabilitiesStore.fetchInitial()` on mount, which awaits `/api/capabilities`.
3. On success: gate unmounts shimmer, renders `<RouterView>`. Route guards (e.g. GH Actions sidebar entry visibility) read from a populated store — no race.
4. On failure: gate routes to `/error` with an "Unable to load capabilities" message + retry button. This also triggers the §9.3 Level 3 outage screen if `/api/health` is the underlying failure.
5. Subsequent refreshes (5 min TTL or post-Settings invalidation) happen in the background — never re-blocking.

Direct-deep-link access (e.g. user pastes `/gh-actions` URL while capabilities load) is naturally safe: the gate holds rendering until capabilities are known, then the route guard runs against populated state.

### 9.2 Data fetching (three composables, scope-matched)

**A. One-shot GET** — `useApi(url, opts)`. Returns `{ data, error, isLoading }`. Used for most initial page loads.

**B. List + incremental polling** — `usePolling(fn, interval)`. Pauses when `document.visibilityState === 'hidden'`. Auto-clears on unmount. Used for Tasks / Sessions / Dashboard / GH Actions.

**C. Log streaming** — `useLogStream(jobId, opts)`. Initially polls existing `/api/tasks/{id}/stream` with `?offset=`. Component interface remains stable even when BE adds SSE later.

No TanStack Query / SWR. Composables are small enough that the additional dependency is not justified.

### 9.3 Error handling (three levels)

**Level 1 — Single-request errors.** Axios interceptor catches 4xx/5xx, maps `error.code` through i18n (`errors.<code>`), shows Naive UI `message.error` toast with request_id suffix. Components do not write `try/catch` unless they degrade UI on specific failures.

**Level 2 — Route-level errors.** Three reserved routes: `/404`, `/forbidden`, `/error`. Vue Router error handler + per-page error boundaries route here.

**Level 3 — App-level outage.** `GET /api/health` fails on boot → full-screen blocker showing "Cannot reach backend at `<URL>`" + troubleshooting checklist + 5s auto-retry + manual retry button. Prevents the silent white-screen failure mode of the old FE.

### 9.4 Error code → i18n mapping

Every BE `error.code` (e.g. `auth.invalid_credentials`, `config.qb_unreachable`, `session.already_committed`) must have a corresponding key under `errors.*` in `zh-CN.json`, `en.json`, `ja.json`. CI lint enforces all three locales contain identical key sets.

### 9.5 Optimistic updates

Off by default. Operator-oriented UI prefers "wrote it → saw BE confirm" over fast-then-rollback. Exceptions for low-risk UI prefs: sidebar collapse, theme, dismiss-hint.

### 9.6 Real-time refresh matrix

| Data | Mechanism | Interval |
|------|-----------|----------|
| Live task log | `useLogStream` (polling `/api/tasks/{id}/stream`) | 2 s |
| Task list (while Tasks page is active) | `usePolling` | 5 s |
| Sessions list | `usePolling` | 10 s |
| Dashboard overview | `usePolling` | 10 s |
| GH Actions runs | `usePolling` | 15 s |
| Capabilities | one-shot + 5 min TTL + force refresh on Settings save | — |
| MovieHistory / TorrentHistory | user-initiated only | — |

All polling pauses on `document.visibilityState === 'hidden'` and immediately re-fetches + resumes when the tab regains focus.

## 10. Testing & CI

### 10.1 Three-layer pyramid

**Unit (Vitest)** — stores, composables, pure utilities. Targets: store/composable/utility line coverage ≥ 80%. Components don't require snapshot coverage; only key-interaction tests when valuable.

Mandatory unit specs: `auth` refresh queue/replay; `capabilities` TTL & invalidation; `usePolling` visibility behavior; `useApi` error mapping; i18n key parity across three locales; axios 401 / CSRF interceptors.

**Contract (Vitest)** — boots BE via docker-compose, exercises every endpoint enumerated in the FE-generated `api.gen.ts`, asserts response shape against OpenAPI schema. Catches FE/BE schema drift across separate repos. This is effectively an integration tier; "contract" naming is kept because the assertion is schema-shape, not behavior.

**E2E (Playwright)** — ~26 user journeys covering Phase 1 (13) + Phase 2 (10) + Phase 3 (3), chromium/firefox/webkit.

Per-spec data isolation mechanism: the BE exposes `POST /api/test/reset` **only when started with `TEST_MODE=1`** (env var; in production the route returns 404). The endpoint truncates all tables (or copies a seed SQLite file over the active one) and reinitializes the schema. Each Playwright `beforeEach` calls `/api/test/reset` followed by optional fixture-specific seed POSTs. The docker container stays running across the suite — only data is reset.

External deps (javdb.com, qB, PikPak, GitHub API) are mocked by default via Playwright `page.route()` interceptors with fixture responses checked into `tests/e2e/fixtures/`. A small set of `@external`-tagged journeys runs against real javdb.com on nightly cron only — never on PR CI.

### 10.2 E2E journey inventory (each must hit at least the listed endpoints)

**Phase 1 — 13 journeys**

1. First-run onboarding (5 steps incl. test daily) — `onboarding/status`, `onboarding/test` (with `component=javdb`/`qb`/`proxy`/`smtp`), `onboarding/complete`, `capabilities`, `config GET/PUT`, `tasks/daily` (with `start_page=1, end_page=3`)
2. Login → Dashboard → Run Daily (Standard) → watch log — `auth/login`, `capabilities`, `tasks/daily`, `tasks list`, `tasks/{id}`, `tasks/{id}/stream`
3. Run Ad Hoc with custom URL → Advanced spider job — `auth/login`, `tasks/adhoc`, `jobs/spider`, `jobs/{id}/status`, `tasks/{id}/stream`
4. Browse: Resolve a video code → magnet table → one-click download — `explore/search-by-video-code`, `explore/resolve`, `explore/index-status`, `explore/download-magnet`, `explore/one-click`
4a. Browse: Lists tab (Top) → status badges render → Cookie sync — `parse/top`, `explore/index-status`, `explore/sync-cookie`
4b. Browse: Preview a URL → Parse this → Resolve view — `explore/proxy-page`, `explore/resolve`
5. Sessions → committed-state list → rollback dry-run → rollback apply — `sessions list`, `sessions/{id}`, `sessions/{id}/rollback` (`dry_run=true` then `apply`)
5a. Sessions edge case: stuck `finalizing` session → commit force — `sessions list?state=finalizing`, `sessions/{id}/commit` (`force=true`)
5b. Sessions edge case: session with both pending and audit rows → rollback with `restore_from_audit=true, include_pending=true` — `sessions/{id}/rollback`
6. Settings → Config edit + save → capabilities refresh — `config GET/meta/PUT`, `capabilities` (forced refresh)
7. Auth → change password → re-login → token refresh during long page — `auth/login`, `auth/logout`, `auth/refresh`
8. Backend down → outage screen → backend recovers → resumes — `health`
8a. Settings → dismiss a hint card from Home → reload → confirmed dismissed — `onboarding/dismiss-hint`, `system/state GET/PUT`

**Phase 2 — 10 journeys**

9. Operations → qB filter small files — `ops/qb/torrents`, `ops/qb/filter-small`
10. Operations → PikPak transfer — `ops/pikpak/queue`, `ops/pikpak/transfer`
11. Operations → Rclone dedup — `ops/rclone/last`, `ops/rclone/run`
12. Operations → Email test send + history → resend a failed notification — `ops/email/test`, `ops/email/history`
12a. Operations → Cleanup → dry-run stale sessions sweep + apply, then claim-stages sweep — `ops/cleanup/stale-sessions`, `ops/cleanup/claim-stages`
13. Diagnostics → parse tester (HTML paste + URL fetch + page-type detect) — `parse/index`, `parse/detail`, `parse/category`, `parse/top`, `parse/tags`, `parse/url`, `detect-page-type`, `crawl/index`
14. Diagnostics → JavDB session refresh via both paths — `diag/javdb-session`, `diag/javdb-session/refresh`, `login/refresh` (headless re-login)
15. Diagnostics → deep health check — `health-check`
16. GH Actions monitor — `gh-actions/workflows`, `gh-actions/runs` (list + POST dispatch with `workflow_dispatch.inputs`), `gh-actions/runs/{id}/logs`
17. Data → search Movies/Torrents with filters and CSV export — `history/movies`, `history/torrents`

**Phase 3 — 3 journeys**

18. GH Actions edit a workflow YAML — `gh-actions/workflows/{name}` PUT
19. GH Actions secrets CRUD — `gh-actions/secrets` GET/POST/DELETE
20. Migrations run + global log search — `migrations`, `migrations/{id}/run`, `logs/search`

### 10.3 CI workflows

**New repo (`javdb-autospider-web`):**

| Workflow | Trigger | Steps |
|----------|---------|-------|
| `ci.yml` | Every PR / push | lint (eslint + prettier check) → typecheck (vue-tsc) → unit (vitest) → contract (BE up + schema check) → build (vite) |
| `e2e.yml` | Every PR + nightly cron | Playwright matrix (chromium/firefox/webkit); nightly also runs `@external` |
| `docker.yml` | Tag push | Multi-stage build → push GHCR `ghcr.io/<user>/javdb-autospider-web:<tag>` |
| `release.yml` | Manual | Conventional-commits changelog + GitHub Release |

**Main repo additions:**

- `publish-api-image.yml` — main push → build + push BE Docker image to GHCR. Prerequisite for FE compose.
- `publish-openapi.yml` — main push → dump `/openapi.json` to `docs/api/openapi.json` + GH Release artifact. Consumed by FE CI.

### 10.4 Quality gates

| Gate | Threshold | Enforcement |
|------|-----------|-------------|
| Unit coverage (stores/composables/utils) | ≥ 80% line | CI red |
| Phase 1 E2E journeys | all passing | merge blocker |
| Phase 2 E2E journeys (incl. all `parse/*`) | all passing OR skipped with linked issue | merge warning |
| Bundle size — initial route gzip | ≤ 250 KB | CI red |
| Bundle size — total gzip across all chunks | ≤ 450 KB | CI red (raisable to 600 KB by config) |
| Lighthouse Performance / Accessibility | ≥ 80 / ≥ 95 | CI warning |

**Bundle size enforcement requires these production conventions, not just a gate number:**

- **Route-level lazy loading is mandatory.** Every route in `src/router/` uses `() => import('@/pages/.../X.vue')`, never static imports. CI fails if any route's component is statically imported (ESLint rule `import/dynamic-import-chunkname` + custom rule).
- **Locale files lazy-loaded.** `vue-i18n` initialized with empty messages; on locale change, `await import('@/i18n/locales/${locale}.json')`. CI fails if any locale JSON is statically imported.
- **No `vfonts` default font shipping in the bundle.** Use system font stack at the root and document optional self-hosted font installation.
- **Naive UI imported only as needed.** Per-component imports from `'naive-ui'` (e.g. `import { NDataTable, NButton } from 'naive-ui'`), never `import naive from 'naive-ui'`. ESLint enforces.
- **NDataTable usage audited.** It is the heaviest single component; routes that only need a simple list should use a lightweight `<table>` styled by theme tokens.

Without these conventions, the 450 KB ceiling will not hold given the dependency footprint (Naive UI tree-shaken ≈ 90-150 KB gzip, Vue + Router + Pinia + i18n + axios + date-fns + @vueuse ≈ 90-120 KB gzip, app code ≈ 60-100 KB gzip, three locales lazy ≈ 30-60 KB out-of-route).

## 11. Migration / Cutover Plan

Timeline assumes one engineer with AI assistance. Calendar weeks, not full-time. Slippage is expected on the BE side because some endpoints (Sessions rollback in particular) wrap CLI tooling with significant edge-case complexity.

1. **Pre-work in main repo (week 0-1)**
   - Verify or create a BE Dockerfile. The repo today has docker-compose configurations referenced in CLAUDE.md but no BE-image Dockerfile audit was done at brainstorming time. If absent, write a multi-stage `Dockerfile` for the FastAPI service first.
   - Land `publish-api-image.yml` (build + push to `ghcr.io/<user>/javdb-autospider-api:latest`).
   - Land `publish-openapi.yml` (dump `/openapi.json` to `docs/api/openapi.json` + GH Release artifact).
   - Add `/api/capabilities` skeleton returning current-deployment defaults (mostly `false`/`'none'`/`'local'`/`'sqlite'`) so FE can develop against a real endpoint.
   - Add `POST /api/test/reset` gated by `TEST_MODE=1` (route returns 404 otherwise).
   - Tighten Pydantic response models on the existing endpoints the FE will consume in Phase 1 (`tasks/daily`, `tasks/adhoc`, `auth/*`, `config/*`, `explore/*`) so generated TS types are useful.

2. **New repo bootstrap (week 1-2)**
   - Scaffold `javdb-autospider-web` per §4.1.
   - Theme tokens + Naive UI provider + layout shell + sidebar.
   - Auth flow (login + refresh queue per §8.1 + role-gated guards).
   - Capabilities boot gate per §9.1.
   - Dashboard page skeleton (stats + recent runs + hint cards).
   - CI: `ci.yml`, `e2e.yml` (with placeholder journeys), `docker.yml`.
   - Compose runs against locally-running main repo BE.

3. **Phase 1 implementation (weeks 3-6, calendar)**
   - Onboarding wizard (5 steps).
   - Run page (Daily + Ad Hoc, Standard + Advanced).
   - Tasks page (table + log Drawer).
   - Sessions page (table + rollback dry-run / apply + commit, including stuck-finalizing handling and audit/pending edge cases).
   - Browse page (Resolve + Lists + Preview per §6.3) — **deferrable to Phase 2 if calendar overruns**; in that case keep just `search-by-video-code` as an inline Dashboard widget and ship the rest later.
   - Settings → Config + Settings → Auth.
   - BE: land all 10 Phase 1 endpoints listed in §8.4.
   - E2E journeys 1–8a, all 10 written and green on CI.
   - Documentation: new repo's `docs/deploy-colocated.md`, `docs/deploy-split.md`, `docs/deploy-github-mode.md`.

4. **Cutover (end of Phase 1, ~week 6-7)**
   - Publish first FE docker image to GHCR.
   - Update main repo README + `docs/en/self-hoster/web-ui-deploy.md` + `docs/zh/self-hoster/web-ui-deploy.md` to point at the new repo.
   - **Delete `apps/web/`, `apps/desktop/`** in a single commit referencing this design doc. Update CLAUDE.md's "Architecture" section to remove the references. Remove `electron:dev` / web-related entries from the root `package.json`.
   - Search-and-update incoming references: `grep -rn "apps/web\|apps/desktop\|electron:dev" docs/ README.md scripts/`.
   - Tag main repo and new repo releases simultaneously.

5. **Phase 2 implementation (weeks 7-12)**
   - Pages listed in §6.2 Phase 2 + 18 new endpoints + 1 existing endpoint wiring + E2E journeys 9–17.
   - Browse rolls forward here if deferred from Phase 1.

6. **Phase 3 implementation (when needed)**
   - GH Actions advanced tiers (`edit`, `admin`).
   - Migrations UI.
   - Global log search.
   - Statistics dashboard.

The Electron desktop shell is **dropped** in the cutover. The web app remains usable in a browser; if a desktop experience is desired later, it can be re-added as a thin Tauri/Electron wrapper around the same Vite build without coupling to the BE process model.

## 12. Risks & Open Questions

### 12.1 Risks

- **Browse Lists mode depends on heavy server-side parsing.** The 6 parse endpoints (`/api/parse/{index,detail,category,top,tags,url}`) currently lack tight Pydantic response models — they return ad-hoc dicts. If FE generates TS types from a loose schema, Lists rendering becomes brittle. **Mitigation**: §11 pre-work explicitly includes tightening response models on `explore/*` and `parse/*` endpoints before Phase 1's Browse code lands. The §10.4 quality gate "Phase 2 E2E parse coverage must pass" enforces this on Phase 2.
- **D1 status batch endpoint cost.** Lists mode renders 20-40 cards per page, each triggering a status badge fetch. With viewport-batching and 150 ms debounce, the load is bounded, but a user paging fast through Lists could fire many `/api/explore/index-status` calls. **Mitigation**: BE caches `index-status` responses for ~10 s per href; FE additionally caches in-memory for the session.
- **GitHub Actions log streaming** via the GH REST API has rate limits (5000 req/h for authenticated PATs). 15s polling per active run is safe for one user but breaks if many concurrent runs are watched. **Mitigation**: poll only the currently-viewed run, not all runs in the list.
- **OpenAPI-driven type generation** assumes the main repo always publishes a clean `openapi.json`. FastAPI's auto-generated schema currently has untyped `dict` responses on most endpoints (explorer survey). **Mitigation**: §11 step 1 pre-work tightens BE response models for endpoints the FE consumes in Phase 1; subsequent phases tighten their endpoints before they ship.
- **Sessions rollback API wraps a CLI with rich semantics.** `apps/cli/rollback.py` has 15+ flags including dual-mode handling, audit replay, pending cleanup, and per-storage-backend branches. The HTTP wrapper has to surface the same surface area without becoming a thin "exec the CLI" passthrough (which would lose typed responses and structured errors). **Mitigation**: refactor `rollback.py`'s core logic into a library function callable from both the CLI and the FastAPI handler; the API endpoint stays a thin wrapper over the library. Plan extra time for this in §11 step 3.
- **`POST /api/test/reset` is a footgun if accidentally enabled in production.** **Mitigation**: route registration explicitly gated on `os.environ.get('TEST_MODE') == '1'`; integration test that the route returns 404 when not in test mode; deployment docs forbid setting `TEST_MODE=1` in production compose files.
- **Workflow YAML editor (Phase 3 tier `edit`)** can break CI workflows if users save invalid YAML. **Mitigation**: client-side YAML schema validation against `workflow_call.inputs` shape; BE dry-run with `actionlint` before committing.

### 12.2 Decisions made (previously open)

- **Backend version skew across deployment topologies.** **Decided**: FE refuses to boot when `capabilities.build.backend_version` is below the minimum recorded in `src/api/min-backend-version.ts`. The boot gate (§9.1) renders a "Backend version mismatch · please upgrade to ≥ X.Y.Z" page with the user's current `backend_version` and the FE's required minimum. The minimum is bumped only when the FE depends on a contract change. To be enforced from Phase 1.
- **i18n for backend error messages.** **Decided**: BE error codes (machine-readable) are mapped to FE-side translations under `errors.*` in each locale. BE log strings (shown verbatim in task logs) stay English. UI chrome and toasts use the user's locale; log content is operator territory.

### 12.3 Remaining open questions

- **Multi-tab behavior.** Two browser tabs polling the same backend doubles request volume. Cheap mitigation: BroadcastChannel-shared `usePolling` so only one tab fetches at a time. **Recommended**: defer to Phase 2 unless multi-tab use becomes painful in Phase 1 dogfooding.
- **D1 status caching aggressiveness.** §12.1 mentions ~10 s server-side cache + session-scoped client cache. The actual TTL needs validation under realistic Browse-Lists usage. Tune after Phase 2 dogfood.
- **Browse deferral decision.** §11 step 3 leaves Browse as deferrable if Phase 1 calendar overruns. The decision point is end-of-week-5: if Tasks + Sessions + Settings are not green, defer Browse rather than ship it half-baked.

---

## Appendix A — Visual Reference

Visual direction "C. Friendly cards" was selected during brainstorming. Token summary:

- Background: `#faf9f7` (light) / `#0e0d12` (dark)
- Surface: `#ffffff` (light) / `#1a1820` (dark)
- Primary accent: `#7c3aed`
- Secondary accent: `#ec4899` (used in gradient with primary)
- Border: `#e5dccf` (light) / `#2a2730` (dark)
- Border radius: 12-16 px (8 px for inputs)
- Shadow: `0 1px 2px rgba(15,23,42,0.04), 0 6px 18px rgba(15,23,42,0.06)`
- Status colors: green `#10b981`, red `#dc2626`, blue `#3b82f6`, amber `#f59e0b`

These will be encoded as `themeOverrides` in `src/theme/index.ts` for both light and dark variants.

## Appendix B — Removed from main repo at cutover

- `apps/web/` (entire directory)
- `apps/desktop/` (Electron shell — re-addable later as Tauri/Electron wrapper if desired)
- `electron:dev` and any web-related entries in root `package.json`
- Documentation files referencing `apps/web/` setup; replaced with pointers to the new repo's `docs/deploy-*.md`
- CLAUDE.md "Architecture" / "Apps" sections describing the old web/desktop apps; replaced with a one-line pointer to the new repo

## Appendix C — Endpoint coverage matrix (Phase 1)

For audit purposes. Every Phase 1 endpoint (new or wired) must appear in at least one journey.

| Endpoint | Journeys |
|----------|----------|
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
| `POST /api/jobs/spider` (existing) | 3 |
| `GET /api/jobs/{id}/status` (existing) | 3 |
| `POST /api/explore/sync-cookie` (existing) | 4a |
| `POST /api/auth/login` (existing) | 1, 2, 3, 7 |
| `POST /api/auth/refresh` (existing) | 7 |
| `POST /api/auth/logout` (existing) | 7 |
| `POST /api/tasks/daily` (existing) | 1, 2 |
| `POST /api/tasks/adhoc` (existing) | 3 |
| `GET /api/tasks` (existing) | 2 |
| `GET /api/tasks/{id}` (existing) | 2 |
| `GET /api/tasks/{id}/stream` (existing) | 2, 3 |
| `GET /api/config` (existing) | 1, 6 |
| `GET /api/config/meta` (existing) | 6 |
| `PUT /api/config` (existing) | 1, 6 |
| `POST /api/explore/resolve` (existing) | 4, 4b |
| `POST /api/explore/search-by-video-code` (existing) | 4 |
| `POST /api/explore/index-status` (existing) | 4, 4a |
| `POST /api/explore/download-magnet` (existing) | 4 |
| `POST /api/explore/one-click` (existing) | 4 |
| `GET /api/explore/proxy-page` (existing) | 4b |
| `POST /api/parse/top` (existing) | 4a |
| `GET /api/health` (existing) | 8 |
