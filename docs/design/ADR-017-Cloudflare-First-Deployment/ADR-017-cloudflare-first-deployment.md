# ADR-017: Cloudflare-First Full-Stack Deployment

| Field       | Value                                      |
| ----------- | ------------------------------------------ |
| **Status**  | Proposed                                   |
| **Date**    | 2026-05-23                                 |
| **Authors** | Ted                                        |
| **Related** | [ADR-008](../ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md), [ADR-010](../ADR-010-D1-Access-Port/) |

## Context

The system currently deploys across multiple platforms:

- **Cloudflare**: D1 databases (source of truth), Proxy Coordinator Worker with 6 Durable Objects, DNS zone
- **Docker/GHCR**: FastAPI backend (uvicorn), Vue 3 frontend (nginx), spider cron container
- **GitHub Actions**: CI/CD pipelines, scheduled ingestion, rollback workflows

This platform fragmentation creates operational overhead:
1. D1 is accessed via HTTP API from Docker containers, adding latency vs. native Worker bindings
2. Frontend and backend deployments are managed separately (Docker images, docker-compose)
3. No Preview Deployments for rapid iteration
4. Managing Docker infrastructure (VPS, container orchestration) for what is primarily a CRUD API + task dispatcher

The frontend (`javdb-autospider-web`) is already a standalone Vue 3 SPA (per ADR-008) with a single `VITE_API_BASE_URL` environment variable controlling API routing.

## Decision

Deploy the full-stack application (Vue 3 frontend + API backend) on **Cloudflare Pages** as a unified project, while **preserving the existing Docker deployment** as an alternative.

### Architecture

```
Cloudflare Pages (javdb-autospider-web repo)
├── Vue 3 SPA (Static Assets, Global CDN)
└── Pages Functions (/api/*)
    ├── Hono Framework (TypeScript)
    ├── D1 Binding (native, zero-latency)
    ├── DO Binding (Proxy Coordinator, via Service Binding)
    └── GitHub Actions Dispatch (heavy tasks)

Docker Deployment (JAVDB_AutoSpider_CICD repo, unchanged)
├── FastAPI + Uvicorn
├── D1 HTTP API or SQLite
└── subprocess execution (spider, pipeline, rclone, qB)
```

### Key Decisions

#### D1: API backend rewritten in TypeScript (Hono), not Python-on-Workers

Cloudflare Workers Python (Pyodide) was evaluated and rejected:
- **`lxml`** (C extension): not available in Pyodide
- **`cryptography`** (C/Rust extension): not available in Pyodide
- **`bcrypt`** / `passlib[bcrypt]` (C extension): not available in Pyodide
- **`curl_cffi`** (C extension): not available in Pyodide

A TypeScript rewrite using Hono is the pragmatic choice:
- Hono is the standard framework for Cloudflare Workers
- D1 bindings are native and fully typed
- Web Crypto API replaces PyJWT + cryptography for JWT auth
- TypeScript aligns with the Vue 3 frontend (unified language)
- 80% of API routes are SQL query + JSON serialization, translating directly

#### D2: Heavy tasks dispatched to GitHub Actions, not executed in Workers

Workers have CPU time limits (10-30ms per request on Free/Pro plans). Long-running operations (spider, pipeline, rclone, qB) are dispatched to existing GitHub Actions workflows:

| Operation | Workflow Dispatched |
| --------- | ------------------- |
| Daily scraping | `DailyIngestion.yml` |
| Ad-hoc URL scraping | `AdHocIngestion.yml` |
| rclone scan/execute | `RcloneManager.yml` |
| qB file filter | `QBFileFilter.yml` |
| Session rollback | `RollbackD1.yml` |

The API endpoint triggers the dispatch and returns immediately. The frontend polls the GitHub Actions API for run status.

#### D3: `javdb-autospider-web` evolves from pure FE to full-stack Pages project

Per ADR-008, the frontend lives in a separate repository. This ADR extends that repo to include Cloudflare Pages Functions (Hono API), making it a full-stack project:

```
javdb-autospider-web/
├── src/                          # Vue 3 frontend (existing, unchanged)
├── functions/                    # Cloudflare Pages Functions
│   └── api/
│       └── [[route]].ts          # Hono catch-all router
├── server/                       # API business code
│   ├── app.ts                    # Hono app + route mounting
│   ├── routes/                   # Route handlers (12 modules)
│   │   ├── auth.ts
│   │   ├── history.ts
│   │   ├── sessions.ts
│   │   ├── config.ts
│   │   ├── tasks.ts
│   │   ├── operations.ts
│   │   ├── explore.ts
│   │   ├── diagnostics.ts
│   │   ├── capabilities.ts
│   │   ├── onboarding.ts
│   │   ├── system.ts
│   │   └── gh-actions.ts
│   ├── middleware/
│   │   ├── auth.ts               # JWT via Web Crypto API
│   │   └── cors.ts
│   ├── services/
│   │   ├── d1-repos.ts           # D1 repository layer
│   │   ├── gh-dispatch.ts        # GitHub Actions workflow dispatch
│   │   └── config-store.ts       # D1-backed config store
│   └── types/                    # Generated from OpenAPI schema
├── wrangler.toml                 # D1 + DO bindings
├── package.json
└── vite.config.ts
```

#### D4: Docker deployment preserved, no changes to Python codebase

The Python FastAPI backend in `JAVDB_AutoSpider_CICD` is not modified. Both deployment modes coexist:

| Dimension | Cloudflare Pages | Docker |
| --------- | ---------------- | ------ |
| Frontend | Vue SPA (CDN) | nginx container |
| API | Hono + D1 Binding (TS) | FastAPI + D1 HTTP API (Python) |
| Database | D1 (native binding) | D1 (HTTP API) or SQLite |
| Heavy tasks | GH Actions dispatch | subprocess local execution |
| Repository | `javdb-autospider-web` | `JAVDB_AutoSpider_CICD` |
| API contract | Same `openapi.json` | Same `openapi.json` |

#### D5: Explore endpoint uses cheerio, not Rust WASM (initially)

The explore endpoint (HTML fetch + parse) uses `cheerio` for DOM parsing in Workers. Rust WASM compilation of the existing parser is deferred as a future optimization — explore is user-triggered, not a performance hotspot.

#### D6: Email notifications via API service or GH Actions dispatch

Workers cannot use raw SMTP (no TCP sockets). Email notifications are either:
- Sent via an email API service (Resend, Mailgun, or Cloudflare Email Workers)
- Dispatched through existing GH Actions workflows that already handle email

### D1 Repository Layer

SQL statements are copied directly from the Python repository layer — D1 is SQLite, so the SQL is identical. Only the binding API differs:

```typescript
// D1 Binding API (TypeScript)
export function createHistoryRepo(db: D1Database) {
  return {
    async loadHistory(filters: HistoryFilters) {
      const stmt = db.prepare(
        "SELECT * FROM MovieHistory WHERE release_date >= ?"
      ).bind(filters.since);
      const { results } = await stmt.all<MovieHistoryRow>();
      return results;
    },
  };
}
```

Three D1 databases are bound in `wrangler.toml`:

```toml
[[d1_databases]]
binding = "HISTORY_DB"
database_name = "javdb-history"
database_id = "<existing-id>"

[[d1_databases]]
binding = "REPORTS_DB"
database_name = "javdb-reports"
database_id = "<existing-id>"

[[d1_databases]]
binding = "OPERATIONS_DB"
database_name = "javdb-operations"
database_id = "<existing-id>"
```

### Config Store Migration

Both `api_config` and `job_runs` tables are added to the **operations** D1 database (`OPERATIONS_DB`), consistent with the existing pattern where operational state lives in `operations.db`.

The Python API stores runtime config in `reports/api_config_store.json` (Fernet-encrypted). In Workers, this is replaced by a D1 table:

```sql
CREATE TABLE api_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Sensitive values are encrypted using the Web Crypto API (AES-GCM) with `SECRETS_ENCRYPTION_KEY` from Workers environment secrets.

### Job Metadata Migration

The Python API stores job metadata as `logs/jobs/*.meta.json` files. In Workers, this is replaced by a D1 table:

```sql
CREATE TABLE job_runs (
    job_id       TEXT PRIMARY KEY,
    workflow     TEXT NOT NULL,
    gh_run_id    INTEGER,
    status       TEXT NOT NULL DEFAULT 'dispatched',
    inputs       TEXT,           -- JSON
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Router Migration Complexity

| FastAPI Router | Hono Route | Complexity | Notes |
| -------------- | ---------- | ---------- | ----- |
| `auth.py` | `auth.ts` | Low | JWT sign/verify via Web Crypto |
| `history.py` | `history.ts` | Low | Pure D1 queries, SQL unchanged |
| `sessions.py` | `sessions.ts` | Low | D1 queries + rollback dispatch |
| `config.py` | `config.ts` | Low | D1 table replaces JSON file |
| `capabilities.py` | `capabilities.ts` | Low | Read config, return flags |
| `system_state.py` | `system-state.ts` | Low | Single D1 table query |
| `diagnostics.py` | `diagnostics.ts` | Medium | D1 aggregate queries |
| `onboarding.py` | `onboarding.ts` | Medium | D1 read/write + init logic |
| `explore.py` | `explore.ts` | Medium | HTML fetch + cheerio parse |
| `gh_actions.py` | `gh-actions.ts` | Medium | GitHub API calls |
| `tasks.py` | `tasks.ts` | High | subprocess → GH Actions dispatch |
| `operations.py` | `operations.ts` | High | qB/rclone/email → dispatch or API |

Low: 6/12 (direct SQL query → JSON translation)
Medium: 4/12 (business logic translation required)
High: 2/12 (execution model redesign)

**Excluded routers:**

- `test_mode.py` — Development/testing only (resets DB state, direct SQLite access). Not deployed to Cloudflare; development testing uses Docker mode.
- `system.py` — Runs subprocesses for health checks and session refresh. In Cloudflare mode, health checks are replaced by Workers built-in monitoring; session refresh is dispatched to GH Actions.

## Implementation Phases

### Phase 1 — Skeleton + Read-Only Queries (MVP)

- Cloudflare Pages project setup (Vite + Hono)
- Auth middleware (JWT via Web Crypto)
- D1 binding configuration (3 databases)
- 4 read-only routes: `capabilities`, `system-state`, `history`, `sessions`
- Frontend `VITE_API_BASE_URL` pointing to Pages URL
- **Acceptance**: Frontend can log in, view history and session lists

### Phase 2 — Config + Diagnostics + Explore

- `config`, `diagnostics`, `onboarding` routes
- Config store migration to D1 table
- Explore endpoint (cheerio parsing)
- **Acceptance**: All query pages functional in frontend

### Phase 3 — Execution + GH Actions Bridge

- `tasks`, `operations`, `gh-actions` routes
- GH Actions dispatch service
- Frontend task polling UI
- **Acceptance**: Frontend can trigger spider/pipeline, view real-time status

### Phase 4 — Optimization + Docker Compatibility Verification

- Docker deployment regression testing
- Performance optimization (D1 queries, cold starts)
- Optional: Rust WASM parser for explore
- E2E test coverage for Cloudflare deployment

## Risks and Mitigations

| Risk | Impact | Mitigation |
| ---- | ------ | ---------- |
| Workers CPU limit (10-30ms) | Complex queries may timeout | D1 queries are inherently fast; pre-compute aggregates if needed |
| GH Actions API rate limit (5000 req/hr) | Frequent status polling | Exponential backoff + cache recent status in D1 |
| OpenAPI contract drift | TS API diverges from Python API | CI contract tests: both implementations share test fixtures |
| D1 binding vs HTTP API behavior differences | Edge case inconsistencies | Unified test cases covering both modes |

## Alternatives Considered

### A1: Vercel (Dual Project — FE + Python Serverless)

Deploy frontend on Vercel Static, backend on Vercel Python Serverless Functions (`@vercel/python`).

- **Rejected because**: Platform fragmentation with existing Cloudflare infrastructure (D1, DO). D1 access would be via HTTP API (no native binding). No Durable Object access. Python runtime cold starts are slower than Workers. Adds a third platform (Cloudflare + Docker + Vercel).

### A2: Cloudflare Workers Python (Pyodide)

Run existing FastAPI code on Workers via Pyodide WebAssembly runtime.

- **Rejected because**: Critical C extension dependencies (`lxml`, `cryptography`, `bcrypt`, `curl_cffi`) are not available in Pyodide. Would require replacing core dependencies, defeating the purpose of "reuse existing code."

### A3: Vercel Frontend + Vercel Rewrites to Docker Backend

Frontend on Vercel, API calls proxied via `vercel.json` rewrites to external Docker backend.

- **Rejected because**: Backend not truly on a serverless platform. Still requires VPS/Docker management. Adds proxy latency. Does not reduce platform fragmentation.

### A4: Thin TS Gateway on Workers + Docker Backend via Cloudflare Tunnel

Lightweight Hono gateway on Workers handling auth + simple D1 queries; complex routes proxied to Docker backend via Cloudflare Tunnel.

- **Not rejected outright**: This is a valid incremental migration path. Could serve as a Phase 0 if full rewrite timeline is too long. The design supports evolving from A4 → full A (recommended approach) by gradually moving routes from proxy to native implementation.

## Cloudflare Pricing

| Dimension | Free Plan | Pro ($20/mo) |
| --------- | --------- | ------------ |
| Requests | 100,000/day | Unlimited |
| Function invocations | 100,000/day | 10M/month |
| D1 reads | 5M/day | 50B/month |
| D1 writes | 100,000/day | 50M/month |
| CPU time | 10ms/request | 30ms/request |
| Builds | 500/month | 5,000/month |

For a personal project, the **Free plan is likely sufficient**. D1 is already in use and does not incur additional cost.
