# ADR-018: Web Backend Security & Data Integrity Hardening

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted                                                              |
| **Date**    | 2026-05-24                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

## Context

A comprehensive audit of the `javdb-autospider-web` TypeScript backend (Hono on Cloudflare Workers) revealed security and data-integrity gaps compared to the Python FastAPI backend. The root cause is architectural: Cloudflare Workers are stateless — the Python backend's in-memory security mechanisms (rate limiting via `RATE_BUCKETS`, token revocation via `REVOKED_JTI`, session tracking via `ACTIVE_TOKENS`) have no equivalent in the TS backend.

### Audit Findings

**P0 — Security (4 issues):**

1. **No rate limiting** — Login endpoint vulnerable to brute-force / credential-stuffing attacks. Workers have no in-memory state between requests.
2. **Token revocation impossible** — Logout deletes cookies but the JWT remains valid until natural expiry (30 min). No mechanism to invalidate compromised tokens.
3. **CORS wildcard** — `origin: (o) => o ?? "*"` accepts any origin, violating the security model when combined with `credentials: true`.
4. **Plain-text passwords in production** — `verifyPassword()` accepts `plain:` prefix in all environments, allowing unencrypted credential storage in production.

**P1 — Data Integrity (4 issues):**

5. **Cross-database commit without atomicity** — Session commit with `drop_pending=true` operates across REPORTS_DB and HISTORY_DB without transaction protection. Partial failure leaves inconsistent state.
6. **Unbounded export queries** — `/movies/export` and `/torrents/export` skip pagination, risking Worker timeout/OOM as data grows (~40,000 rows currently).
7. **CSRF token not returned on refresh** — `/refresh` endpoint omits CSRF token from response body. Long sessions may lose CSRF validity, causing 403 errors on mutations.
8. **Missing D1 indexes** — Foreign keys and frequently-filtered columns (`MovieHistoryId`, `SessionId`, `Status`) lack indexes. Query performance degrades with data growth.

## Decision

Introduce a **KV Namespace** (`AUTH_KV`) as lightweight state storage for the Workers backend. Fix all 8 issues in the TypeScript backend only — the Python backend is unaffected.

### P0-1: Rate Limiting via KV

Add a `rateLimit(limit, windowSeconds)` middleware using KV counters.

**Key design:**
- Key format: `rl:{ip}:{endpoint}:{window_start}`
- Value: request count
- TTL: window duration (auto-cleanup)

**Limits:**
- `POST /api/auth/login`: 5 requests / 60s per IP
- `POST /api/auth/refresh`: 10 requests / 60s per IP
- Other mutations: 20 requests / 60s per IP

**Response on limit exceeded:** HTTP 429 with `Retry-After` header.

**Consistency trade-off:** KV is eventually consistent (~60s propagation across colos). Concurrent requests from the same colo have a read-modify-write race (no atomic increment). For this project's scale (1-2 users, single geographic region), this is acceptable. The goal is brute-force prevention, not precise metering.

### P0-2: Token Revocation via KV (Mutation Requests Only)

**On logout:** Write `revoked:{jti}` → KV with TTL = token's remaining seconds.

**On authenticated mutation requests (POST/PUT/DELETE):** `requireAuth()` middleware queries KV for `revoked:{jti}`. If found → 401.

**On GET requests:** Skip revocation check. A revoked token can still read data until natural expiry (max 30 min). This avoids adding ~12ms KV latency to every API call.

**Session counting:** `sessions:{username}` → JSON array `[{jti, exp}]`. On login:
1. Read current sessions, remove expired entries.
2. If count ≥ 3, reject login (HTTP 429).
3. Append new `{jti, exp}`, write back.

Soft limit: read-modify-write race could briefly exceed 3 sessions. Next login auto-cleans.

### P0-3: CORS Explicit Whitelist

Replace wildcard CORS with environment-driven origin list:

- **Production** (`ENVIRONMENT=production`): Read `CORS_ORIGINS` env var (comma-separated). Empty = same-origin only (no CORS headers emitted).
- **Development** (`ENVIRONMENT !== production`): Auto-include `http://localhost:*` and `http://127.0.0.1:*`.
- Cloudflare same-domain deployment: CORS headers are unnecessary (same-origin), but explicit whitelist prevents misconfiguration.

### P0-4: Reject Plain-Text Passwords in Production

In `verifyPassword()`:
- If `ENVIRONMENT === "production"` and hash starts with `plain:` → return false, log warning via `console.warn()`.
- Development/test environments retain `plain:` support for convenience.

### P1-5: Session Commit Operation Ordering

For `POST /sessions/:id/commit` with `drop_pending=true`:

**Order: delete pending first, then update session status.**

1. `HISTORY_DB.batch()`: DELETE from `PendingMovieHistoryWrites` and `PendingTorrentHistoryWrites` WHERE SessionId = ?
2. `REPORTS_DB.prepare()`: UPDATE `ReportSessions` SET Status = 'committed' WHERE Id = ?

**Failure analysis:**
- Step 1 succeeds, step 2 fails → Session remains `finalizing`, pending rows already deleted. Retry commit: no pending to delete, status updates to committed. **Recoverable.**
- Reverse order (status first, pending second) → Session marked `committed`, pending rows remain permanently. StaleSessionCleanup won't touch committed sessions. **Unrecoverable.**

Wrap in try/catch; on partial failure return HTTP 207 with detail of which step succeeded.

### P1-6: Export Hard Limit

Add `LIMIT 100000` to both `/movies/export` and `/torrents/export` queries.

When truncated:
- Response header: `X-Export-Truncated: true`
- Response header: `X-Export-Total-Count: {actual_count}`
- Final CSV row: `# Export truncated at 100,000 rows. Total: {count}`

Prepend UTF-8 BOM (`﻿`) for Windows Excel compatibility.

### P1-7: CSRF Token on Refresh

`POST /api/auth/refresh` response:
- Add `csrf_token` field to JSON response body.
- Re-set `csrf_token` cookie with refreshed `Max-Age` matching new access token expiry.
- Frontend `client.ts` already checks for `csrf_token` in refresh response and updates `sessionStorage` — no frontend changes needed.

### P1-8: D1 Indexes

Apply via `wrangler d1 execute` (one-time, not migration-tracked):

```sql
CREATE INDEX IF NOT EXISTS idx_th_movie_id ON TorrentHistory(MovieHistoryId);
CREATE INDEX IF NOT EXISTS idx_mh_session ON MovieHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_rs_status ON ReportSessions(Status);
CREATE INDEX IF NOT EXISTS idx_th_session ON TorrentHistory(SessionId);
```

At ~40,000 rows, index creation completes in <2s. No downtime required. Reversible via `DROP INDEX`.

## New Cloudflare Resources

| Resource       | Binding Name | Purpose                                     |
| -------------- | ------------ | ------------------------------------------- |
| KV Namespace   | `AUTH_KV`    | Rate limiting, token revocation, session count |

`wrangler.toml` addition:

```toml
[[kv_namespaces]]
binding = "AUTH_KV"
id = "<created-at-deploy-time>"
preview_id = "<created-at-deploy-time>"
```

`env.ts` addition:

```typescript
AUTH_KV: KVNamespace;
```

## Out of Scope

- **Python backend changes** — This ADR targets the TS backend only.
- **501 stub completion** — Stubbed endpoints (crawl, parse, migrations, etc.) are ADR-017 design decisions and not revisited here.
- **Config schema gap** (73 missing keys) — Independent feature work, not security/integrity.
- **Stats trend empty implementations** (duration, proxy_bans) — Functional incompleteness, not integrity.
- **`ensureTable()` per-request overhead** — Performance optimization, not correctness.
- **Password timing attack** (user enumeration) — With only 2 fixed usernames (admin/readonly), attack surface is negligible.

## Consequences

### Positive

- Login brute-force protection restored (parity with Python backend).
- Logout actually works — compromised tokens can be invalidated.
- CORS no longer accepts arbitrary origins.
- Cross-database commit has defined failure/recovery semantics.
- Export queries won't crash Workers at scale.
- D1 query performance improves with indexes on hot paths.

### Negative

- KV adds a new Cloudflare resource to manage (billing, wrangler config).
- Mutation requests gain ~12ms latency from KV revocation check.
- Rate limiting is approximate (eventual consistency) — acceptable for current scale.
- Session limit is soft (race condition may briefly exceed 3) — self-heals on next login.

### Risks

- **KV outage** → Rate limiting and revocation fail open (requests proceed without checks). Acceptable: security degrades to current baseline, not worse.
- **KV cost** — Free tier: 100k reads/day, 1k writes/day. Auth traffic is well within these limits for a personal project.
