# ADR-030: Web Backend Feature Parity — Config, Stats, and Password Management

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted                                                              |
| **Date**    | 2026-05-24                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md) |

## Context

A comprehensive audit of the `javdb-autospider-web` TypeScript backend compared against the Python FastAPI backend and `config.py.example` revealed feature parity gaps across three areas:

1. **Config schema gap** — The TS backend exposes 57 config fields; the Python backend has ~130. Of the 73 missing keys, ~26 are runtime-meaningful and should be configurable through the web UI. The remainder are local-deployment-only paths/logs (irrelevant to Cloudflare Workers).
2. **Stats trend incomplete** — Two trend metrics (`duration`, `proxy_bans`) return empty data in the TS backend. `duration` is computable from D1; `proxy_bans` has no Cloudflare-accessible data source.
3. **Change Password missing** — Python backend has `POST /api/auth/change-password`; TS backend lacks it entirely. Users must use `wrangler secret put` to change passwords.

Additionally, three config key naming mismatches between backends cause confusion:
- `SMTP_HOST` (TS) vs `SMTP_SERVER` (Python canonical)
- `START_PAGE` / `END_PAGE` (TS) vs `PAGE_START` / `PAGE_END` (Python canonical)

## Decision

### Config Key Triage: Three-Tier Classification

Not all 73 missing keys warrant inclusion. Keys are triaged by their relevance to the Cloudflare Workers deployment:

| Tier | Criteria | Action | Count |
| ---- | -------- | ------ | ----- |
| **Must add** | Runtime behavior impact; user needs to configure via UI | Add to `config-schema.ts` | 26 |
| **Capabilities** | Deployment-time constants (D1 IDs, coordinator URLs) | Display via `/api/capabilities` (future work) | ~15 |
| **Skip** | Local-only paths (`*_LOG_FILE`, `*_DB_PATH`, `*_DIR`, `*_CSV`) | Not applicable to Cloudflare Workers | ~33 |

### Must-Add Keys (26 total)

#### Spider Parameters (5)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `PAGE_START` | int | spider | no | Renamed from `START_PAGE` |
| `PAGE_END` | int | spider | no | Renamed from `END_PAGE` |
| `PHASE2_MIN_RATE` | float | spider | no | |
| `PHASE2_MIN_COMMENTS` | int | spider | no | |
| `BASE_URL` | string | spider | no | Default: `https://javdb.com` |

#### qBittorrent Ad-Hoc Instance (4)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `QB_URL_ADHOC` | string | qbittorrent | no | |
| `QB_USERNAME_ADHOC` | string | qbittorrent | no | |
| `QB_PASSWORD_ADHOC` | string | qbittorrent | yes | |
| `QB_ALLOW_INSECURE_HTTP` | bool | qbittorrent | no | readonly |

#### qBittorrent Extended (2)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `REQUEST_TIMEOUT` | int | qbittorrent | no | Seconds |
| `DELAY_BETWEEN_ADDITIONS` | int | qbittorrent | no | Seconds |

#### SMTP Completion (3)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `SMTP_SERVER` | string | smtp | no | Renamed from `SMTP_HOST` |
| `EMAIL_FROM` | string | smtp | no | |
| `EMAIL_TO` | string | smtp | no | |

#### Proxy Extended (2)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `PROXY_POOL_MAX_FAILURES` | int | proxy | no | Default: 3 |
| `LOGIN_PROXY_NAME` | string | proxy | no | |

#### Login Advanced (5)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `GPT_API_URL` | string | javdb | no | Captcha solver endpoint |
| `GPT_API_KEY` | string | javdb | yes | |
| `LOGIN_ATTEMPTS_PER_PROXY_LIMIT` | int | javdb | no | Default: 6 |
| `LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH` | int | javdb | no | Default: 3 |
| `LOGIN_VERIFICATION_URLS` | json | javdb | no | JSON array |

#### GitHub Actions Configuration (3)

| Key | Type | Section | Sensitive | Readonly | Notes |
| --- | ---- | ------- | --------- | -------- | ----- |
| `GH_ACTIONS_TIER` | string | ghActions | no | yes | Deployment-time |
| `GH_ACTIONS_REPO` | string | ghActions | no | yes | Deployment-time |
| `GH_ACTIONS_TOKEN` | string | ghActions | yes | no | |

#### API Authentication (2)

| Key | Type | Section | Sensitive | Notes |
| --- | ---- | ------- | --------- | ----- |
| `READONLY_USERNAME` | string | apiConsole | no | |
| `READONLY_PASSWORD_HASH` | string | apiConsole | yes | |

### Config Key Renames via Alias Fallback

TS aligns to Python canonical names. No one-time D1 migration script — instead, use alias fallback in the config load path:

**Load precedence per key:**
1. D1 config table under canonical name (e.g. `SMTP_SERVER`)
2. D1 config table under alias (e.g. `SMTP_HOST`)
3. Environment variable
4. Schema default

**On save:** Always write the canonical name. Old alias key remains in D1 until naturally overwritten or manually cleaned.

**Alias map:**

| Canonical (new) | Alias (old) |
| --------------- | ----------- |
| `SMTP_SERVER` | `SMTP_HOST` |
| `PAGE_START` | `START_PAGE` |
| `PAGE_END` | `END_PAGE` |

### Stats Trend: Duration and Proxy Bans

#### `duration` — Implemented from `job_runs` table

Query `OPERATIONS_DB.job_runs` for completed jobs:

```sql
SELECT DATE(created_at) AS date,
       AVG((julianday(updated_at) - julianday(created_at)) * 86400) AS value
FROM job_runs
WHERE status = 'completed'
  AND created_at >= datetime('now', '-{days} days')
GROUP BY DATE(created_at)
ORDER BY date
```

Returns average job duration in seconds per day.

#### `proxy_bans` — Not available in Cloudflare mode

The Python backend computes proxy bans by grepping local log files. Cloudflare Workers have no filesystem access and no D1 table stores ban events.

**Response shape change:**

```json
{
  "metric": "proxy_bans",
  "period": "7d",
  "available": false,
  "reason": "proxy_bans requires local log access (unavailable in Cloudflare mode)",
  "data": []
}
```

The `available: false` flag signals the frontend to render "N/A" instead of an empty chart. All other metrics continue to return `"available": true`.

### Change Password Endpoint

**New endpoint:** `POST /api/auth/change-password`

**Request:**

```json
{
  "old_password": "current-password",
  "new_password": "new-password-at-least-8-chars"
}
```

**Behavior:**
1. Verify `old_password` against current hash (D1 config → env fallback).
2. Validate `new_password` (minimum 8 characters).
3. Hash with bcrypt (cost factor 10).
4. Write `ADMIN_PASSWORD_HASH` (or `READONLY_PASSWORD_HASH` for readonly users) to D1 config table via `saveConfigKeys()`.
5. Return `{ status: "ok" }`.

**Auth requirement:** Authenticated user can only change their own password. Admin cannot change readonly user's password through this endpoint (use config PUT for that).

### `findUser()` Async with D1 Priority

`findUser()` in `server/services/users.ts` becomes async and accepts a D1 database parameter:

```typescript
export async function findUser(env: Env, db: D1Database): Promise<User | undefined>
```

**Password hash resolution per user:**
1. Query D1 `api_config` table for `ADMIN_PASSWORD_HASH` (or `READONLY_PASSWORD_HASH`)
2. If found → use D1 value
3. If not found → fall back to `env.ADMIN_PASSWORD_HASH`

**Call sites requiring `await`:**
- `server/routes/auth.ts`: login handler, refresh handler

## Out of Scope

- **33 local-deployment-only keys** (`*_LOG_FILE`, `*_DB_PATH`, `*_DIR`, `*_CSV`) — irrelevant to Cloudflare Workers.
- **15 Cloudflare/coordination keys** — future capabilities endpoint enhancement.
- **Frontend page changes** — Config UI auto-renders from `/config/meta`; no manual page work needed.
- **Python backend changes** — This ADR targets TS backend only.
- **Password change for readonly users via admin** — Use `PUT /api/config` with `READONLY_PASSWORD_HASH`.

## Consequences

### Positive

- Web UI can configure 83 fields (57 existing + 26 new), covering all runtime-meaningful parameters.
- Config key names match Python's canonical definitions — eliminates cross-backend confusion.
- Users can change passwords through the UI without CLI access.
- Stats trend dashboard shows job duration data; proxy_bans explicitly marked as unavailable rather than silently empty.
- Alias fallback is zero-downtime — existing D1 config values keep working during transition.

### Negative

- `findUser()` becomes async — adds D1 read on every login/refresh. Acceptable latency (~10ms per D1 query).
- `available` field added to trend response — frontend must handle this new field (graceful: unrecognized fields are ignored by current frontend).
- Alias map is a form of tech debt — old keys linger in D1 until overwritten. Acceptable for 3 keys.

### Risks

- **D1 config priority over env** for password hash means `wrangler secret put ADMIN_PASSWORD_HASH` no longer overrides once a change-password has been done through the UI. Document this behavior.
- **bcrypt in Cloudflare Workers** — `bcryptjs` (pure JS) is already used for verification. Hashing at cost 10 takes ~100ms in Workers — acceptable for a password change endpoint called rarely.
