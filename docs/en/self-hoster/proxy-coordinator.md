# Proxy Coordinator -- Deployment Guide from Scratch

> An ops-facing step-by-step guide. Deploy a Cloudflare Worker + Durable Object
> onto the Cloudflare Free tier and connect 5 GitHub Actions workflows to
> cross-instance per-proxy throttle coordination. **Zero cost throughout, with
> zero-penalty rollback at any time.**

> **Worker source repository**:
> [TongWu/JAVDB_AutoSpider_Proxycoordinator](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator)
>
> The TypeScript source for the Worker / Durable Object, `wrangler.toml`, unit
> tests, and quota scripts have been split out from this monorepo into the
> standalone repo above (rationale: the deployment lifecycle and toolchain
> dependencies (Node + wrangler) are completely independent from the Python
> spider). All `cd JAVDB_AutoSpider_Proxycoordinator` steps in this document
> assume you have already `git clone`d that repo to any local directory. The
> Python client
> ([`javdb/proxy/coordinator/proxy_coordinator_client.py`](../../javdb/proxy/coordinator/proxy_coordinator_client.py))
> remains in this repository; the two sides are decoupled via HTTP + token.

---

## Table of Contents

- [0. What This Is / Why You Need It](#0-what-this-is--why-you-need-it)
- [1. Prerequisites (One-Time)](#1-prerequisites-one-time)
  - [1.1 Cloudflare Account](#11-cloudflare-account)
  - [1.2 Local Tools](#12-local-tools)
  - [1.3 Clone the Worker Repo + One-Time OAuth Login](#13-clone-the-worker-repo--one-time-oauth-login)
- [2. Project Directory Overview](#2-project-directory-overview)
- [3. Local Development and Unit Tests](#3-local-development-and-unit-tests)
- [4. Generate and Configure the Token](#4-generate-and-configure-the-token)
- [5. First Deployment](#5-first-deployment)
- [6. GitHub Actions Integration](#6-github-actions-integration)
  - [6.1 Add GitHub Secret and Variable](#61-add-github-secret-and-variable)
  - [6.2 Trigger a Manual AdHocIngestion to Verify](#62-trigger-a-manual-adhocingestion-to-verify)
- [7. Monitoring and Observability](#7-monitoring-and-observability)
  - [7.1 Cloudflare Dashboard](#71-cloudflare-dashboard)
  - [7.2 Workers Analytics Engine Queries](#72-workers-analytics-engine-queries)
  - [7.3 70k req/day Alert Threshold (Ops Convention)](#73-70k-reqday-alert-threshold-ops-convention)
- [8. Rollback Steps](#8-rollback-steps)
  - [8.1 Soft Disable (Keep the Worker, but Python Falls Back to Local Throttling)](#81-soft-disable-keep-the-worker-but-python-falls-back-to-local-throttling)
  - [8.2 Full Teardown](#82-full-teardown)
- [9. proxy_id Consistency (CRITICAL)](#9-proxy_id-consistency-critical)
- [10. Free Tier Quota Estimates](#10-free-tier-quota-estimates)
- [11. Troubleshooting FAQ](#11-troubleshooting-faq)
  - [Q1. `401 Unauthorized`](#q1-401-unauthorized)
  - [Q2. `400 missing proxy_id`](#q2-400-missing-proxy_id)
  - [Q3. `429 Too Many Requests` or daily cumulative 100k ceiling hit](#q3-429-too-many-requests-or-daily-cumulative-100k-ceiling-hit)
  - [Q4. `wait_ms` is abnormally long (>30 s)](#q4-wait_ms-is-abnormally-long-30-s)
  - [Q5. Multiple runners are running, but only one has high throughput](#q5-multiple-runners-are-running-but-only-one-has-high-throughput)
  - [Q6. Spider does not call the coordinator at all after deployment](#q6-spider-does-not-call-the-coordinator-at-all-after-deployment)
  - [Q7. Too many DO instances](#q7-too-many-do-instances)
- [12. Upgrade / Advanced Topics](#12-upgrade--advanced-topics)
- [13. Cross-Runtime Login State DO (GlobalLoginState)](#13-cross-runtime-login-state-do-globalloginstate)
  - [13.1 Workflow (Python Side)](#131-workflow-python-side)
  - [13.2 Endpoint Reference](#132-endpoint-reference)
  - [13.3 Deployment (Same as S5)](#133-deployment-same-as-s5)
  - [13.4 Python-Side Configuration](#134-python-side-configuration)
  - [13.5 Troubleshooting](#135-troubleshooting)
  - [13.6 Rollback](#136-rollback)
- [14. P1-A: Cross-Run Proxy Ban + CF Bypass Sharing (Piggybacking on ProxyCoordinator)](#14-p1-a-cross-run-proxy-ban--cf-bypass-sharing-piggybacking-on-proxycoordinator)
  - [14.1 Protocol Changes (Backward Compatible)](#141-protocol-changes-backward-compatible)
  - [14.2 Default TTLs (`wrangler.toml [vars]`)](#142-default-ttls-wranglertoml-vars)
  - [14.3 Ops Cheat Sheet](#143-ops-cheat-sheet)
  - [14.4 Rollback](#144-rollback)
- [15. P1-B / P2-A: MovieClaim DO (Cross-Runner Detail Mutual Exclusion + Failure Cooldown)](#15-p1-b--p2-a-movieclaim-do-cross-runner-detail-mutual-exclusion--failure-cooldown)
  - [15.1 Problem Solved](#151-problem-solved)
  - [15.2 Protocol Endpoints](#152-protocol-endpoints)
  - [15.3 Deployment (Same as S5)](#153-deployment-same-as-s5)
  - [15.4 Default `auto`: `MOVIE_CLAIM_ENABLED` Tri-State Semantics](#154-default-auto-movie_claim_enabled-tri-state-semantics)
  - [15.5 Cross-Day Ingestion Note](#155-cross-day-ingestion-note)
  - [15.6 Troubleshooting](#156-troubleshooting)
  - [15.7 Rollback](#157-rollback)
- [16. P2-E: RunnerRegistry (Ops Observability + Configuration Drift Detection)](#16-p2-e-runnerregistry-ops-observability--configuration-drift-detection)
  - [16.1 Problem Solved](#161-problem-solved)
  - [16.2 Protocol Endpoints](#162-protocol-endpoints)
  - [16.3 Configuration Drift Alert](#163-configuration-drift-alert)
  - [16.4 Deployment](#164-deployment)
  - [16.5 Troubleshooting](#165-troubleshooting)
  - [16.6 Rollback](#166-rollback)
- [17. P2-C: Cross-Runner Login Quota Cooldown](#17-p2-c-cross-runner-login-quota-cooldown)
  - [17.1 Problem Solved](#171-problem-solved)
  - [17.2 Protocol Changes (Backward Compatible)](#172-protocol-changes-backward-compatible)
  - [17.3 Default Configuration (`wrangler.toml [vars]`)](#173-default-configuration-wranglertoml-vars)
  - [17.4 Troubleshooting](#174-troubleshooting)
  - [17.5 Rollback](#175-rollback)
- [18. P2-D: Cross-Run Proxy Pool Health Scoring](#18-p2-d-cross-run-proxy-pool-health-scoring)
  - [18.1 Problem Solved](#181-problem-solved)
  - [18.2 Protocol Changes (Backward Compatible)](#182-protocol-changes-backward-compatible)
  - [18.3 Client Integration](#183-client-integration)
  - [18.4 Tuning Suggestions](#184-tuning-suggestions)
  - [18.5 Rollback](#185-rollback)
- [19. Post-Deploy Smoke Test Script](#19-post-deploy-smoke-test-script)
- [20. W2: Multi-Runner Hardening (Operational Notes)](#20-w2-multi-runner-hardening-operational-notes)

---

## 0. What This Is / Why You Need It

Currently, inside each GH Actions runner process, every worker has its own
human-like sleep + three-window throttling
(`javdb/spider/runtime/sleep.py`). But this is **process-local**:
when two GH Actions runs execute concurrently (sharing the same
`PROXY_POOL_JSON`), they unknowingly send requests through the same physical
proxy at the same time, breaking the human-like interval.

This solution creates a Cloudflare Durable Object instance for each `proxy_id`.
DOs serialize execution by id, making them a natural fit for per-proxy mutual
exclusion + shared throttle state. Before each spider request, the client
`POST /lease`s; the DO returns the `wait_ms` the caller must wait before
sending the request. When any runner hits a CF Turnstile, it notifies the DO
via `/report`, and all other runners will receive an elevated `penalty_factor`
on their next lease.

**Fail-open design**: When the Worker is unreachable / the token mismatches /
the network fails, the Python side automatically falls back to the original
local throttling path with no business impact.

---

## 1. Prerequisites (One-Time)

### 1.1 Cloudflare Account

- Visit <https://dash.cloudflare.com/sign-up> to sign up
- The Free Plan is sufficient -- **no credit card required**
- Note the **Account ID** in the bottom-right corner (also visible on the `Workers & Pages` page)

### 1.2 Local Tools

```bash
# Node.js >= 20 (easiest to install via brew on macOS)
brew install node

# Verify
node --version   # Should be >= 20
npm --version
```

### 1.3 Clone the Worker Repo + One-Time OAuth Login

```bash
# Pick any local directory (placing it alongside this spider repo is most convenient)
git clone https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator.git
cd JAVDB_AutoSpider_Proxycoordinator
npm install              # Install wrangler and other dependencies
npx wrangler login       # Opens browser -- click Allow
```

Success indicator: the terminal displays `Successfully logged in.`

---

## 2. Project Directory Overview

```text
JAVDB_AutoSpider_Proxycoordinator/   # Standalone GitHub repo (already git clone'd)
├── wrangler.toml                    # Worker + DO bindings + tunable constants
├── package.json
├── tsconfig.json
├── vitest.config.ts
├── src/
│   ├── index.ts                     # Worker entry (routing, auth)
│   ├── proxy_coordinator.ts         # ProxyCoordinator DO implementation
│   └── types.ts                     # Env / request types
├── test/
│   └── proxy_coordinator.test.ts    # vitest-pool-workers unit tests (15 tests)
└── scripts/
    └── check-quota.sh               # Daily lease count alert script
```

The `[[migrations]] new_sqlite_classes = ["ProxyCoordinator"]` line in
`wrangler.toml` indicates that **only SQLite-backed DOs** are used, because the
Free Plan only supports SQLite-backed DOs.

---

## 3. Local Development and Unit Tests

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm install
npx wrangler dev                       # http://localhost:8787 dev server
```

Open another terminal to run a smoke test:

```bash
TOKEN=devtoken
curl -s -X POST http://localhost:8787/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"proxy_id": "test", "intended_sleep_ms": 1000}'
# Expected: {"wait_ms": 1XXX, "penalty_factor": 1.0, ...}
```

> **Note**: In local dev mode the token can be any string, but for production
> deployment you must use `wrangler secret put` to set a strong random token.

Run all unit tests:

```bash
npx vitest run     # Should output "15 passed"
npx tsc --noEmit   # Type check
```

---

## 4. Generate and Configure the Token

```bash
# Generate a 64-character strong random hex token
TOKEN=$(openssl rand -hex 32)
echo "Save this token: $TOKEN"      # Be sure to save it! It will also be set in GH Secrets

# Deploy to Cloudflare as a Worker secret
echo -n "$TOKEN" | npx wrangler secret put PROXY_COORDINATOR_TOKEN
# You should see "✨ Success!"
```

**Token purpose**: The Worker's `/lease`, `/report`, and `/state` endpoints all
require an `Authorization: Bearer <TOKEN>` header; the Python side adds it
automatically. `/health` does not require a token, making it convenient for
monitoring health checks.

---

## 5. First Deployment

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npx wrangler deploy
```

Output looks like:

```text
Total Upload: 6.71 KiB / gzip: 2.34 KiB
Uploaded proxy-coordinator (3.21 sec)
Published proxy-coordinator (1.42 sec)
  https://proxy-coordinator.<your-subdomain>.workers.dev
```

**Note this URL** -- it is needed in the next step.

Verify liveness:

```bash
curl https://proxy-coordinator.<your-subdomain>.workers.dev/health
# Should return: ok
```

Verify with the token:

```bash
TOKEN=<token from previous step>
curl -X POST https://proxy-coordinator.wuengineer.workers.dev/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"proxy_id": "smoke-test", "intended_sleep_ms": 500}'
# Should return JSON: {"wait_ms": ~500, "penalty_factor": 1.0, ...}
```

---

## 6. GitHub Actions Integration

### 6.1 Add GitHub Secret and Variable

Open your repository -> **Settings** -> **Secrets and variables** -> **Actions**:

- **Secrets** tab -> **New repository secret**:
  - Name: `PROXY_COORDINATOR_TOKEN`
  - Value: the token generated in step 4

- **Variables** tab -> **New repository variable**:
  - Name: `PROXY_COORDINATOR_URL`
  - Value: the URL from step 5 (without trailing slash)

> Since these workflows all use `environment: Production`, add these in the
> Production environment rather than at the Repository level (if both exist,
> the environment-level value takes precedence).

### 6.2 Trigger a Manual AdHocIngestion to Verify

In the GitHub UI: **Actions** -> select `JavDB Ad Hoc Ingestion` -> **Run workflow**

Expected log changes (in the `Step 1 - Run Spider` step):

```text
INFO:SpiderState: Proxy coordinator client initialised: base_url=https://proxy-coordinator.acme.workers.dev
DEBUG:SleepMgr: Coordinator lease: wait=8.42s (local=7.30s, reason=ok, remote_penalty=1.00, proxy=JP-1)
```

If instead you see:

```text
INFO:SpiderState: Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) — using local throttling only
```

Then the GH variables were not injected into the worker -- check that the
environment level in step 6.1 is correct.

---

## 7. Monitoring and Observability

### 7.1 Cloudflare Dashboard

**Workers & Pages** -> `proxy-coordinator` -> **Metrics** tab:
- Total requests (CPU time, error rate)
- Real-time invocation logs (click the **Logs** tab to enable Tail)

### 7.2 Workers Analytics Engine Queries

Each `/lease` / `/report` call writes a row to the `proxy_coordinator_leases`
dataset. Query via the SQL API:

```bash
ACCOUNT_ID=<your account id>
TOKEN=<API token with "Account Analytics: Read" perm>

curl -X POST "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/analytics_engine/sql" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-binary @- <<'SQL'
SELECT toDate(timestamp) AS day, COUNT(*) AS leases
FROM proxy_coordinator_leases
WHERE blob1 = 'lease'
GROUP BY day
ORDER BY day DESC
LIMIT 7
FORMAT JSON
SQL
```

Or use the script:

```bash
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...
bash JAVDB_AutoSpider_Proxycoordinator/scripts/check-quota.sh
# Output: Last-24h lease count: 4823 (threshold: 70000)
```

### 7.3 70k req/day Alert Threshold (Ops Convention)

The Free Plan hard limits are 100,000 Worker requests/day + 100,000 DO
requests/day. **Convention**: when the 24-hour rolling lease count exceeds
**70,000** (70%), take immediate action (reduce frequency / page range, or
upgrade to the Paid Plan at $5/month).

Optional: set `scripts/check-quota.sh` as a daily cron (GitHub Actions works too):

```yaml
# .github/workflows/CoordinatorQuotaCheck.yml (optional -- not committed by default)
on:
  schedule:
    - cron: '0 */6 * * *'   # Every 6 hours
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout coordinator repo
        uses: actions/checkout@v6
        with:
          repository: TongWu/JAVDB_AutoSpider_Proxycoordinator
      - run: bash scripts/check-quota.sh
        env:
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
```

---

## 8. Rollback Steps

### 8.1 Soft Disable (Keep the Worker, but Python Falls Back to Local Throttling)

GitHub -> Settings -> Variables -> delete `PROXY_COORDINATOR_URL` (or clear it)

On the next spider run, it will output:

```text
Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) — using local throttling only
```

Behavior reverts to pre-PR state. **Zero code changes.**

### 8.2 Full Teardown

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npx wrangler delete
```

The DO instances and SQLite state will be deleted along with it. You can
`wrangler deploy` again at any time to rebuild.

---

## 9. proxy_id Consistency (CRITICAL)

DOs are addressed via `idFromName(proxy_id)` -- **all runners must use the same
string**, otherwise the same physical proxy will be routed to different DO
instances, completely defeating mutual exclusion (and **with no error**).

The Python client's normalization rules (see `_normalize_proxy_id()` in
[`javdb/proxy/coordinator/proxy_coordinator_client.py`](../../javdb/proxy/coordinator/proxy_coordinator_client.py)):

1. Prefer the `name` field from `PROXY_POOL_JSON` (trimmed, truncated to 256 characters)
2. Without a `name`, fall back to `proxy-<sha1(host:port)[:16]>`

**It is strongly recommended that ops explicitly set `name` for all proxies and
ensure the `PROXY_POOL_JSON` string is exactly identical across all runners.**

```jsonc
// PROXY_POOL_JSON Secret (recommended format)
[
  {"name": "JP-1", "http": "http://user:pass@1.2.3.4:8080"},
  {"name": "JP-2", "http": "http://user:pass@5.6.7.8:8080"}
]
```

If a proxy lacks a `name` (i.e., `PROXY_POOL_JSON` does not provide one), the
Python side derives a stable `proxy_id` from `host:port` and emits a **WARNING**
log (no exception; the pipeline is not interrupted):

```text
Coordinator proxy_id derived from host:port hash: proxy-<16hex> — recommend setting `name` in PROXY_POOL_JSON so all runners agree
```

---

## 10. Free Tier Quota Estimates

| Resource | Free Plan Limit | Median Scenario Usage | Upper-Bound Scenario Usage |
|---|---|---|---|
| Worker requests | 100,000/day | 5,000 (5%) | 20,000 (20%) |
| DO requests | 100,000/day | 5,000 (5%) | 20,000 (20%) |
| DO Duration | 13,000 GB-s/day | ~5 GB-s (0.04%) | ~30 GB-s (0.23%) |
| DO SQLite rows R/W | 5 M / 100 K /day | 5,000 (5%) | 20,000 (20%) |
| DO Storage | 5 GB | <1 MB | <1 MB |

Data source: <https://developers.cloudflare.com/durable-objects/platform/pricing/>

Median = `DailyIngestion` once/day, about 5,000 JavDB HTTP requests; upper
bound = triggered a large number of CF retry chains. **The number of instances
(M concurrent GH Actions) does not affect total request count**, because the
DO's `next_available_at` + three-window mechanism caps per-proxy total throughput
at the human-like throttling limit. Adding concurrent instances merely splits the
same throughput across more runners.

> **W2 sub-sharding note**: with `NUM_CLAIM_SHARDS=4` (default), fan-out
> operations (`/commit_completed_movies`, `/rollback_staged_movies`,
> `/sweep_orphan_stages`) send 5 DO sub-requests each (4 sub-shards + 1 legacy
> shard). At ~3 session-level operations per run, this adds ~15 DO requests —
> negligible relative to the per-movie claim calls.

---

## 11. Troubleshooting FAQ

### Q1. `401 Unauthorized`
- GH Secret and Worker Secret are out of sync -> re-run step 4 to synchronize both sides

### Q2. `400 missing proxy_id`
- The Python client did not pass `proxy_id` -- usually a wiring bug on the spider side
- Check whether `state.global_proxy_coordinator` was successfully initialized (look at INFO logs)

### Q3. `429 Too Many Requests` or daily cumulative 100k ceiling hit
- Upgrade to the Workers Paid Plan ($5/month, provides 10M req/month)
- Or temporarily reduce the `PAGE_END` GH Variable to lower the ingestion volume per run

### Q4. `wait_ms` is abnormally long (>30 s)
1. Check the DO state dump:
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "https://proxy-coordinator.../state?proxy_id=JP-1"
   ```
2. See if `requestTimestamps` length is near 200 (30-minute window cap) --
   this means the proxy is being used at high frequency by multiple runners
   simultaneously, which is expected behavior
3. If `cfEvents` is non-zero, CF events have raised the penalty

### Q5. Multiple runners are running, but only one has high throughput
- 99% of the time this is `proxy_id` inconsistency (see S9)
- Check Analytics Engine data:
  ```sql
  SELECT blob1 AS op, blob0 AS proxy_id, COUNT(*) AS n
  FROM proxy_coordinator_leases
  WHERE timestamp > NOW() - INTERVAL '1' HOUR
  GROUP BY blob0, blob1 ORDER BY n DESC
  ```
  If the same physical proxy appears with two different `proxy_id` values, the name fields are inconsistent

### Q6. Spider does not call the coordinator at all after deployment
- `Proxy coordinator not configured` log: GH Var/Secret not injected. Check:
  - Whether the names are exactly `PROXY_COORDINATOR_URL` / `PROXY_COORDINATOR_TOKEN` (case-sensitive)
  - Whether they are set in the `Production` environment (the workflow uses `environment: Production`)
  - Whether the `Generate config.py from GitHub Variables and Secrets` step shows the corresponding `VAR_PROXY_COORDINATOR_URL` env

### Q7. Too many DO instances
- The limit is 500,000 DO instances/account; by proxy count this should be far below that
- Single DO storage limit is 10 GB (far exceeds our few-hundred-byte state)

---

## 12. Upgrade / Advanced Topics

- **Multi-region**: Cloudflare DO automatically selects the nearest PoP -- no configuration needed
- **Custom domain**: Add a CNAME in Cloudflare DNS -> Worker route, removing the
  long `<subdomain>.workers.dev` tail
- **Finer throttle tuning**: Edit the `[vars]` section of `wrangler.toml` and
  re-run `wrangler deploy` -- no Python-side changes needed
- **New endpoints**: Add routes in `src/index.ts`, add DO methods in
  `src/proxy_coordinator.ts`

---

## 13. Cross-Runtime Login State DO (GlobalLoginState)

Sections 0--12 describe the `ProxyCoordinator` (per-proxy throttling DO) that
solves **request pacing** coordination across runners. But JavDB has a second
cross-runner concern -- **at-most-one login session**: logging in on proxy A
invalidates the cookie held by proxy B. If N GH Actions run simultaneously and
each runner independently calls attempt_login_refresh, you get:

1. Duplicate logins waste GPT CAPTCHA credits / increase account lockout risk;
2. The last runner to log in seizes the cookie -> all other runners' cookies
   become invalid -> they re-login -> infinite loop.

`GlobalLoginState` is the **second DO class** deployed within the **same Worker**
(singleton `idFromName("global")`), storing `(logged_in_proxy_name,
encrypted_cookie, version, last_verified_at)` plus a `lease` mutual exclusion
lock. The same `PROXY_COORDINATOR_TOKEN` simultaneously serves:
- Bearer auth for the 5 `/login_state*` endpoints;
- Key derivation for AES-GCM 256 cookie encryption (HKDF-SHA256).

### 13.1 Workflow (Python Side)

At startup, `_inherit_login_state` first `GET /login_state` to retrieve an
existing cookie; when no cookie is available, it proceeds with `acquire_lease`
-> actual login -> `publish` -> `release_lease`. Workers whose lease is taken
by another runner do not block -- the LoginCoordinator pushes `LoginRequired`
tasks into the local `_pending_login_tasks` deque, and the
`_poll_login_state_loop` daemon polls the DO every 3s; once it observes a
`version` increment, it injects the new cookie into the corresponding worker
and redistributes parked tasks. **Workers on other proxies are completely
unaffected** during this time and continue fetching non-login pages normally.

### 13.2 Endpoint Reference

> For the full schema and example curl commands, see the "GlobalLoginState
> endpoints" section in the Worker repo README.

| Endpoint | Purpose |
|---|---|
| `GET /login_state` | Read current (proxy_name, decrypted cookie, version) |
| `POST /login_state/acquire_lease` | Acquire a 5--300s re-login mutual exclusion lock |
| `POST /login_state/publish` | Lease holder publishes new cookie (version+1) |
| `POST /login_state/invalidate` | Optimistic-lock marks cookie as invalid |
| `POST /login_state/release_lease` | Lease holder releases the mutual exclusion lock |

### 13.3 Deployment (Same as S5)

When running `wrangler deploy`, the new `[[migrations]] tag = "v2"` will
automatically create the `GlobalLoginState` class. No new secret is needed --
the encryption key and Bearer auth share `PROXY_COORDINATOR_TOKEN`. **Rotating
the token simultaneously forces the next login** (old cookie decryption fails ->
DO returns `cookie:null` -> the next runner goes through `acquire_lease` and
re-logs in).

### 13.4 Python-Side Configuration

No new environment variables are needed.
`javdb/storage/login_state_client.py` reuses
`PROXY_COORDINATOR_URL` / `PROXY_COORDINATOR_TOKEN`; `setup_proxy_pool` also
calls `setup_login_state_client`, and when unconfigured or `/health` fails, it
silently fails open, degrading to the old "per-runner independent login"
behavior. Each runner generates a one-time
`runtime_holder_id = f"runner-<uuid>"` (`state.runtime_holder_id`) as the lease
holder identifier, which remains constant for the entire process lifetime.

### 13.5 Troubleshooting

| Symptom | Investigation |
|---|---|
| Startup logs show only `Proxy coordinator client initialised` but no `Login-state client initialised` | Worker deployment version is too old and lacks `/login_state*` routes -- re-run S5 to deploy the latest Worker |
| Multiple runners still log in independently | Check whether `wrangler tail` shows `/login_state/acquire_lease` requests on the Worker side; common cause: old client not upgraded / token only injected for some runners |
| `409 lease_required` warning | The old sequential fallback path called `attempt_login_refresh` without first acquiring -- this is expected fail-open behavior; this runner can still use cookies, just without cross-runner sharing |
| `invalidate no-op (current_version > our N)` | Version race -- another runner published a new cookie first; the poller will pull and auto-sync on the next tick; no manual intervention needed |
| Cookie is frequently invalidated | The user logged into the same account in a browser -- this is JavDB's single-session constraint; increase `LOGIN_VERIFICATION_URLS` hit rate to reduce false positives |

### 13.6 Rollback

Soft disable: same as S8.1 -- deleting `PROXY_COORDINATOR_URL` simultaneously
disables both throttle and login-state coordination.

Hard delete: `wrangler delete --class-name GlobalLoginState` (only deletes the
`GlobalLoginState` introduced in v2; does not affect v1's `ProxyCoordinator`).

---

## 14. P1-A: Cross-Run Proxy Ban + CF Bypass Sharing (Piggybacking on ProxyCoordinator)

Sections 0--13 address **throttling** and **login** coordination across runners.
**P1-A** further piggybacks two types of state that were previously only held in
a single runner's memory onto the `ProxyCoordinator` DO:

| State | Before | After P1-A |
|---|---|---|
| `proxy_ban_manager` blocklist | Session-scoped; cleared when the process dies | Cross-run persistent, default **3-day TTL** (259,200,000 ms); auto-unbanned on expiry |
| `proxies_requiring_cf_bypass` | Per-runner dictionary | Each runner passively syncs on the next `/lease`, avoiding redundant CF probing |

**Zero additional DO calls** -- both signal types piggyback on the existing
`/lease` (read path) and `/report` (write path).

### 14.1 Protocol Changes (Backward Compatible)

- `ReportRequest.kind` union extended to
  `"cf" | "failure" | "ban" | "unban" | "cf_bypass" | "success"`, body adds
  optional `ttl_ms?: number` and `reason?: string`.
- `LeaseResponse` adds optional fields `banned: boolean` (default `false`),
  `banned_until: number | null`, `requires_cf_bypass: boolean`,
  `cf_bypass_until: number | null`.
- When an old Worker does not send the new fields, the Python client dataclass
  defaults all to "no signal", making behavior identical to today.

### 14.2 Default TTLs (`wrangler.toml [vars]`)

| Variable | Default | Meaning |
|---|---|---|
| `BAN_TTL_MS` | `259200000` | Default 3 days per `mark_proxy_banned` call |
| `CF_BYPASS_TTL_MS` | Specified by the caller via `ttl_ms`; `0` = permanent | Semantically equivalent to `state.always_bypass_time` |

### 14.3 Ops Cheat Sheet

```bash
# Manually unban a proxy (e.g., after manually confirming health has recovered):
curl -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
     -H "content-type: application/json" \
     -d '{"proxy_id":"JP-1","kind":"unban","reason":"manual"}' \
     "$PROXY_COORDINATOR_URL/report"

# Temporarily mark a proxy as permanent CF bypass (until wrangler delete):
curl -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
     -H "content-type: application/json" \
     -d '{"proxy_id":"JP-1","kind":"cf_bypass","ttl_ms":0,"reason":"sticky"}' \
     "$PROXY_COORDINATOR_URL/report"

# Read a proxy's real-time ban / bypass status (requires GET /state, debug only):
curl -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
     "$PROXY_COORDINATOR_URL/state?proxy_id=JP-1"
```

### 14.4 Rollback

Zero code changes: deleting `PROXY_COORDINATOR_URL` simultaneously disables
ban / bypass sharing. The spider reverts to in-memory blocklist + per-runner CF
dictionary (i.e., pre-PR behavior).

---

## 15. P1-B / P2-A: MovieClaim DO (Cross-Runner Detail Mutual Exclusion + Failure Cooldown)

### 15.1 Problem Solved

When two concurrent ingestions (e.g., DailyIngestion + manual AdHoc) pull the
same movie's `/v/<id>` detail page for the same actor, the process-local
`_completed_entries` is **not coordinated across runners** -- both send HTTP
requests, each sleeps 6--20s, doubling parser overhead. **P1-B** introduces a
new DO class `MovieClaimState`, **sharded by day**
(`idFromName("YYYY-MM-DD-Asia/Singapore")`), implemented with a single-key
snapshot + `cached` in-memory layer + DO Alarm GC every 10 min.

**P2-A** adds `fail_count` / `next_attempt_at` / `last_error_kind` to the same
schema: when a detail-fetch fails, it enters a cooldown ladder via
`/report_failure` (exponential backoff capped at 3 days); after multiple
failures it becomes dead-letter, and other runners are immediately rejected when
calling `claim_movie`.

### 15.2 Protocol Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /claim_movie` | body `{ href, holder_id, ttl_ms, session_id? }` -> `{ acquired, current_holder_id, expires_at, already_completed, cooldown_until?, last_error_kind?, fail_count?, staged_session_id? }`. Phase-1: when `session_id` matches the owner in `staged_complete{}`, returns `already_completed=true` (same-session idempotent); mismatch does not block. |
| `POST /release_movie` | body `{ href, holder_id }` holder releases |
| `POST /complete_movie` | body `{ href, holder_id }` marks as completed (goes directly into `completed_committed[]`, **legacy P1-B path**); also clears P2-A failure records |
| `POST /stage_complete_movie` | **Phase-1**: body `{ href, holder_id, session_id }` writes to `staged_complete{}` (per-session). Entries do not move to `completed_committed[]` until commit/rollback CLI runs. |
| `POST /commit_completed_movies` | **Phase-1**: body `{ session_id }`, batch-promotes that session's `staged_complete` entries to `completed_committed[]`. Called by `apps/cli/db/commit_session.py` after `db_mark_session_committed`. |
| `POST /rollback_staged_movies` | **Phase-1**: body `{ session_id }`, deletes that session's `staged_complete`. Called by `apps/cli/db/rollback.py` after rolling back reports/operations; retries up to 3 times; on failure writes to `reports/D1/d1_drift.jsonl` without blocking DB rollback. |
| `POST /report_failure` | body `{ href, holder_id, error_kind }` enters cooldown / dead-letter |
| `GET /sweep_orphan_stages?older_than_ms=N&date=YYYY-MM-DD` | **Phase-1**: cleans up orphan entries in `staged_complete{}` older than `older_than_ms` (server floor 1 h, no max cap). `apps/cli/db/sweep_claim_stages.py` is scheduled by `StaleSessionCleanup.yml` cron. |
| `GET /movie_status?href=X&date=YYYY-MM-DD` | Debug dump (includes `staged_session_id` / `staged_at`) |

### 15.3 Deployment (Same as S5)

When running `wrangler deploy`, the new `[[migrations]] tag = "v3"
new_sqlite_classes = ["MovieClaimState", "RunnerRegistry"]` will create both
the P1-B and P2-E classes in one go (sharing the v3 tag). `wrangler.toml`
already has the `MOVIE_CLAIM_DO` binding and `MOVIE_CLAIM_TTL_MS` in the
`[vars]` section (default `1800000`, 30 min).

### 15.4 Default `auto`: `MOVIE_CLAIM_ENABLED` Tri-State Semantics

> Since the "movie-claim auto start/stop" rework, `MOVIE_CLAIM_ENABLED` has
> been upgraded from a boolean toggle to a tri-state selector, with the
> **default changed to `auto`**: when the GH Variable is not explicitly set,
> it automatically starts/stops based on active runner count, removing the
> previous ops burden of manually synchronizing the toggle.

| Value (case-insensitive, leading/trailing whitespace ignored) | Behavior |
|---|---|
| **`auto` (new default when variable is not set)** | Driven by the `RunnerRegistry` via the `movie_claim_recommended` field in the `register` / `heartbeat` response: auto-mounts `global_movie_claim_client` when `active_runners.length >= MOVIE_CLAIM_MIN_RUNNERS` (see S15.4.1), unmounts otherwise. |
| `true` / `1` / `yes` | **force_on** -- equivalent to old P1-B behavior: mounts once at startup, ignores registry signals. Use when ops explicitly wants the "old semantics" (e.g., during a mixed-upgrade window). |
| `false` / `0` / `no` / **empty string** | **off** -- never mounts; same effect as "DO not configured". `MOVIE_CLAIM_ENABLED=` (explicitly empty) and "not set" have different semantics: the former forces off, the latter goes to `auto`. |

Client flow in `auto` mode:

1. `setup_movie_claim_client` creates the client + `/health` check passes, then **optimistically mounts** to `state.global_movie_claim_client`, storing the same reference in `_movie_claim_client_pending`. This way, during startup (before receiving the registry response), cross-runner mutual exclusion protection is still active. The worst-case cost is a few extra claim DO calls in the first ~15s of a single-runner deployment; these are unmounted immediately after the first `register` response feeds back.
2. `setup_runner_registry_client` synchronously `register`s and receives `movie_claim_recommended`, immediately feeding it into `_apply_movie_claim_recommendation` to complete the first "mount/unmount based on cohort size" decision.
3. `_runner_heartbeat_loop` feeds `movie_claim_recommended` from each successful heartbeat / re-register response, enabling edge detection of cohort changes.

`force_on` and `off` modes ignore registry signals: the former keeps the mount, the latter keeps it empty. Logging format remains unchanged.

#### 15.4.1 Server-Side Threshold `MOVIE_CLAIM_MIN_RUNNERS`

New entry in Worker `wrangler.toml` `[vars]`:

```toml
MOVIE_CLAIM_MIN_RUNNERS = "2"
```

`RunnerRegistry` in the `register` / `heartbeat` handler **first writes its own
record, then reads the full set, then derives
`movie_claim_recommended = active_runners.length >= MOVIE_CLAIM_MIN_RUNNERS`**;
since the DO serializes execution, the second of two concurrent `register` calls
always sees >= 2, so there is no "both see 1" missed-detection window. The
Worker also backfills the threshold into the response's
`movie_claim_min_runners` field, allowing ops to directly see the currently
effective threshold.

The default threshold of 2 means: single-runner deployments do not need mutual
exclusion; two or more runners enable it automatically. To adjust for deployment
scale, edit `wrangler.toml` -- no code changes, no client changes needed. The
Worker enforces a floor on the value (`< 1` is treated as invalid) to prevent
accidentally setting the threshold to 0, which would make `recommended` always
true and defeat the "disable for single runner" optimization.

#### 15.4.2 Missed-Lock Window and Dynamic Heartbeat Period

In `auto` mode, the worst-case "missed-lock window" during startup: between a
peer joining and this runner's heartbeat feeding back the signal, this runner
still thinks it is alone and does not mount the client. To compress this window
to an acceptable range, `_runner_heartbeat_loop` uses a **dynamic heartbeat
period**:

| Mode / State | Period | Fallback Constant |
|---|---|---|
| `force_on` / `off` | 60 s | `_RUNNER_HEARTBEAT_INTERVAL_SEC` |
| `auto` with `recommended=True` | 60 s | `_RUNNER_HEARTBEAT_INTERVAL_SEC` |
| `auto` with `recommended=False` (single runner state) | 15 s | `_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC` |

The 15s period compresses the worst-case "peer joins to local detection" delay
to ~15s, at the cost of only 4x heartbeat call frequency in single-runner state
(negligible cost for a singleton DO, far less than the +2 claim DO calls per
detail page). Multi-runner state restores the standard 60s period.

Implementation details:

- `RunnerRegistryUnavailable` / other heartbeat exception branches **do not update** `_movie_claim_last_recommended`, preventing a single transient failure from unmounting an already-mounted `global`.
- `_apply_movie_claim_recommendation` edge-triggers INFO logs (`movie-claim auto: mounted/unmounted`); in steady state (consecutive heartbeats with the same signal), it does not print repeatedly.
- All state transitions hold `_movie_claim_lock` (`threading.Lock`), ensuring concurrency safety with the detail path.

### 15.5 Cross-Day Ingestion Note

The shard date is derived from the **task initiation time** (not the claim call
time), ensuring that cross-day ingestions do not lose mutual exclusion by routing
the same movie to two shards when crossing midnight.

### 15.6 Troubleshooting

| Symptom | Investigation |
|---|---|
| `Movie claim client not initialised` but `MOVIE_CLAIM_ENABLED=true` | Worker deployment version is too old or `/health` failed; check `wrangler tail` for `/movie_status` 404 |
| `auto` mode with >= 2 runners online but no `movie-claim auto: mounted` seen | Worker derived the field incorrectly (register/heartbeat should **write first, then read**); use `scripts/verify_proxy_coordinator_deploy.sh` to fire two `register` calls and check whether `movie_claim_recommended` changes with the cohort; temporarily fall back by setting GH Variable `MOVIE_CLAIM_ENABLED=true` to force the force_on old semantics |
| Single-runner deployment shows many `movie-claim auto: unmounted` | `auto` mode's optimistic mount at startup is unmounted by the first `register` signal -- this is the normal steady-state transition; only investigate if you see repeated mount->unmount->mount oscillation, which suggests the RunnerRegistry GC TTL is too short |
| Same movie fetched by two runners across a day boundary | Client timezone configuration drift; check that `Asia/Singapore` and `path_helper.ensure_dated_dir` are consistent |
| A movie can "never" be claimed | That href entered P2-A dead-letter (`fail_count >= 5`); use `complete_movie` to force-clear it or wait 24h for auto GC |
| Alarm GC not firing | The DO must receive at least one write before it registers an alarm; the 10-min cycle starts after the first claim following cold start |

### 15.7 Rollback

- **Switch tri-state back to force_on** (fastest): GH Variables -> `MOVIE_CLAIM_ENABLED=true`, equivalent to old P1-B semantics; ignores registry signals, does not depend on new Worker fields (works even if PR-1 is not deployed).
- **Fully disable**: GH Variables -> `MOVIE_CLAIM_ENABLED=false` (or `0` / `no` / empty string). Both `auto` and `force_on` are disabled, equivalent to deleting the DO.
- Soft disable + delete class: `npx wrangler delete --class-name MovieClaimState` (does not affect v1/v2/v3 other classes).
- Revert PR-2 (client tri-state implementation): new Worker fields are harmless to old clients; only `auto` mode auto start/stop disappears; ops must manually maintain `MOVIE_CLAIM_ENABLED=true` in sync with active runner count.

---

## 16. P2-E: RunnerRegistry (Ops Observability + Configuration Drift Detection)

### 16.1 Problem Solved

In multi-runner concurrent scenarios, there is no way to answer:
- "How many runners are currently active?"
- "What is the other runner's `workflow_run_id`?"
- "Is their `PROXY_POOL_JSON` the same as mine?" (**configuration drift** -- the entire purpose of the originally planned P3-B)

**P2-E** adds a singleton DO `RunnerRegistry` (`idFromName("runners")`); at the
end of `setup_proxy_pool` it `register`s, a daemon `heartbeat`s every 60s, and
`atexit` `unregister`s. The DO's internal Alarm cleans runners with
`last_heartbeat < now - 10 min` every 5 min.

### 16.2 Protocol Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /register` | body `{ holder_id, workflow_run_id, workflow_name, started_at, proxy_pool_hash, page_range? }` -> returns `proxy_pool_hash[]` of all current runners |
| `POST /heartbeat` | body `{ holder_id }` |
| `POST /unregister` | body `{ holder_id }` |
| `GET /active_runners` | Debug dump |

### 16.3 Configuration Drift Alert

The `register` response includes other runners' `proxy_pool_hash` values. If
this runner detects at startup that its `sha1(PROXY_POOL_JSON)[:16]` differs
from an existing runner, it emits a **WARNING**:

```text
PROXY_POOL_JSON drift detected: this runner=<my_hash> peers=[<other_hash>] —
two runners are working with different proxy pools, ban / claim coordination may be inconsistent
```

This is a "lightweight" alert that does not block startup. Ops should verify
whether the `PROXY_POOL_JSON` Secret has been updated in sync across the two
GH Actions workflows.

### 16.4 Deployment

`wrangler.toml` already has the `RUNNER_REGISTRY_DO` binding. On the first
`wrangler deploy`, it shares the `[[migrations]] tag = "v3"` with P1-B and is
created together. `[vars]` section:

| Variable | Default | Meaning |
|---|---|---|
| `RUNNER_REGISTRY_ENABLED` | `"true"` | Worker-side master switch; same-named GH Variable takes precedence |
| `RUNNER_STALE_TTL_MS` | `600000` | Heartbeat exceeding 10 minutes is considered stale |

### 16.5 Troubleshooting

| Symptom | Investigation |
|---|---|
| `Runner registry client not initialised` | Same as S15.6 first row |
| `unregister failed: HTTP 503` | atexit hook; expected (fault-tolerant), does not affect the 5-min alarm GC |
| `proxy_pool_hash` all differ | Secrets not synced across multiple workflows; submit a PR to make `PROXY_POOL_JSON` a reusable workflow input |

### 16.6 Rollback

- Soft disable: GitHub Variables -> `RUNNER_REGISTRY_ENABLED=false` (or delete
  `PROXY_COORDINATOR_URL`).
- Full class deletion: `npx wrangler delete --class-name RunnerRegistry`.

---

## 17. P2-C: Cross-Runner Login Quota Cooldown

### 17.1 Problem Solved

`login_total_budget` (`state.py`) is calculated based on the current run's
`len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT`. Even with
`GlobalLoginState.acquire_lease` serialization, N runners in the same day still
accumulate attempts **against their own independent budgets** -- 5 runners x
5 attempts = 25 login attempts, far exceeding JavDB's rate-limiting threshold.

**P2-C** adds `recent_attempts[]` (24h rolling window + buffer cap) to
`GlobalLoginStateData`, and when `acquire_lease` exceeds the threshold, **it
still grants the lease but also returns `cooldown_until_ms > 0`**. The Python
side `LoginCoordinator` detects the cooldown and immediately releases the lease,
pushing all `LoginRequired` tasks into the `_pending_login_tasks` deque; the
daemon `_poll_login_state_loop` polls every 3s, and after the cooldown lifts,
automatically redistributes them.

### 17.2 Protocol Changes (Backward Compatible)

- `AcquireLeaseResponse` adds optional fields `cooldown_until_ms?: number`,
  `recent_attempt_count?: number` (default 0, ignored by old clients).
- New endpoint `POST /login_state/record_attempt` body `{ holder_id, proxy_name,
  outcome }` -- the publisher calls this once after `publish` success/failure to
  let the DO accumulate attempts.

### 17.3 Default Configuration (`wrangler.toml [vars]`)

| Variable | Default | Meaning |
|---|---|---|
| `LOGIN_COOLDOWN_THRESHOLD` | `"5"` | >= 5 failed attempts in 24h triggers cooldown |
| `LOGIN_COOLDOWN_WINDOW_SEC` | `"3600"` | Rolling window of 1 hour |
| `LOGIN_COOLDOWN_DURATION_MS` | `"1800000"` | Once triggered, all runners pause for 30 minutes |

### 17.4 Troubleshooting

| Symptom | Investigation |
|---|---|
| After startup, spider logs continuously show `parking <N> tasks (cooldown active)` | Expected behavior; check `wrangler tail` to confirm whether cooldown_until_ms is reasonable |
| Cooldown triggers too frequently | Increase `LOGIN_COOLDOWN_THRESHOLD` or `LOGIN_COOLDOWN_WINDOW_SEC` |
| Old Worker does not return `cooldown_until_ms` | Python client defaults to 0, i.e., "no cooldown" -- behavior identical to today |

### 17.5 Rollback

Soft disable is the same as S8.1 (delete `PROXY_COORDINATOR_URL`). To disable
only P2-C on the Worker side without disabling everything else: set
`LOGIN_COOLDOWN_THRESHOLD` to a very large number (e.g., `99999`) then
`wrangler deploy`.

---

## 18. P2-D: Cross-Run Proxy Pool Health Scoring

### 18.1 Problem Solved

`ProxyPool` tracks `success/fail/latency` within a single run but does not
persist across runs. **P2-D** adds
`successEvents[] / failureEvents[] / latencyEma` to
`ProxyCoordinator.CoordinatorState`, and each `/lease` response carries a
derived `health` field (`success_count / failure_count / latency_ema_ms /
score in [0,1]`). The Python side `ProxyPool.get_next_proxy` switches to
**health-weighted random selection** if and only if the `coordinator` is
configured -- good proxies have a significantly higher selection probability,
while bad proxies retain a 5% floor probability to allow recovery.

### 18.2 Protocol Changes (Backward Compatible)

- `ReportRequest.kind` union adds `"success"`, body adds optional
  `latency_ms?: number`.
- `LeaseResponse` adds optional `health: { success_count, failure_count,
  latency_ema_ms, score } | null` (ignored by old clients; new clients fall
  back to 0.5 neutral score when the field is missing).
- Write path synchronously refreshes `cached` to prevent subsequent `/lease`
  reads on the same instance from seeing stale values.

### 18.3 Client Integration

- `request_handler.RequestHandler._do_request` /
  `_do_request_curl_cffi` calls
  `coord.report_async(proxy_id, "success"|"failure", latency_ms=elapsed_ms)`
  after each target site HTTP completion. **CF bypass service calls are not
  counted** (to avoid local bypass latency polluting proxy quality scores).
- At the end of `setup_proxy_pool`,
  `coordinator.get_proxy_health_score` is injected into the Python
  `ProxyPool`'s `health_provider`; the Rust pool retains round-robin for now
  (does not affect correctness).

### 18.4 Tuning Suggestions

Health score formula (`proxy_coordinator.ts` `computeHealthSnapshot`):
- `ratio = success_count / (success_count + failure_count)`
- `latency_penalty = clamp((latency_ema_ms - 500) / 10000, 0, 0.5)`
- `score = ratio - latency_penalty` (no samples -> `score = 0.5`)

For more aggressive behavior (bad proxies bypassed faster), lower the floor in
`ProxyPool._safe_health_score` from `0.05` to `0.01` on the Python side; for
more conservative behavior (avoid oscillation), square the weights:
`weights[i] **= 2`.

### 18.5 Rollback

Soft disable is the same as S8.1. On the Worker side, you cannot "disable only
P2-D while keeping P1-A" -- they share the same DO; if needed, comment out the
`ProxyPool.set_health_provider(None)` call on the Python side, and the pool
reverts to round-robin (while still inheriting P1-A's ban/bypass).

---

## 19. Post-Deploy Smoke Test Script

The repository provides
[`scripts/verify_proxy_coordinator_deploy.sh`](../../scripts/verify_proxy_coordinator_deploy.sh)
as a quick health check after `wrangler deploy` and before triggering a real
AdHocIngestion. The script sends one canary request to key endpoints of all 4
DO classes, printing PASS/FAIL for each line:

```bash
PROXY_COORDINATOR_URL=https://your-worker.workers.dev \
PROXY_COORDINATOR_TOKEN=$(wrangler secret list | grep TOKEN ...) \
    ./scripts/verify_proxy_coordinator_deploy.sh
```

Expected output at the end:

```text
RESULT: all DO classes responded OK.  Safe to trigger AdHocIngestion.
```

Any FAIL means a DO class was not deployed correctly (migration tag mismatch,
binding not applied, wrong token, etc.) -- **resolve before triggering
AdHocIngestion** to avoid discovering that login or claim has entirely
fail-opened and degraded only after a 30+ min run.

Then trigger one AdHocIngestion. In its GH Actions logs you should see 4
`... client initialised` lines:

| Log Line | Source |
|---|---|
| `Proxy coordinator client initialised: base_url=...` | `setup_proxy_coordinator` |
| `Login-state client initialised: base_url=..., holder_id=...` | `setup_login_state_client` |
| `Movie-claim client initialised: base_url=..., mode=auto\|force_on` | `create_movie_claim_client_with_mode_from_env` (appears for all values except `MOVIE_CLAIM_ENABLED=false/0/no/empty string`) |
| `Movie-claim client optimistically mounted (auto, awaiting registry signal): base_url=..., holder_id=...` | `setup_movie_claim_client` (`auto` mode only) |
| `Movie-claim client mounted (force_on): base_url=..., holder_id=...` | `setup_movie_claim_client` (`force_on` mode only) |
| `movie-claim auto: mounted (active_runners >= threshold)` | `_apply_movie_claim_recommendation` (`auto` mode when cohort crosses threshold) |
| `movie-claim auto: unmounted (active_runners < threshold)` | `_apply_movie_claim_recommendation` (`auto` mode when dropping back to single runner) |
| `Runner-registry client initialised: base_url=..., holder_id=..., ...` | `setup_runner_registry_client` |

Finally: delete `PROXY_COORDINATOR_URL` from GH Variables, then trigger another
run. You should see:

```text
Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) — using local throttling only
```

And the spider behavior should be identical to before the PR -- **this is the
final verification of the "fallback to default mechanism when DO is not
configured" contract.**

---

## 20. W2: Multi-Runner Hardening (Operational Notes)

This section documents operational behavior changes introduced by the W2 (Multi-Runtime Compatibility) workstream. All changes are **backward compatible** — old Workers and old Python clients continue to function, but the new behaviors only activate when both sides are updated.

### 20.1 MovieClaim Href Sub-Sharding (W2.2)

**Before**: one DO instance per day (`claims-YYYY-MM-DD`).
**After**: each day is subdivided into `NUM_CLAIM_SHARDS` (default 4) sub-shards by `djb2(href) % N`, creating DO instances like `claims-YYYY-MM-DD-0` through `claims-YYYY-MM-DD-3`.

**Why**: under 5 concurrent runners, a single per-day DO becomes a serialization bottleneck (DO processes requests single-threaded). Sub-sharding distributes the load across N instances.

**Configuration** (`wrangler.toml [vars]`):

```toml
NUM_CLAIM_SHARDS = "4"    # default; set to "1" to disable sub-sharding
```

**Migration compatibility**: session-level operations (`/commit_completed_movies`, `/rollback_staged_movies`, `/sweep_orphan_stages`) fan out to **all sub-shards plus the legacy date-only shard**. This means pre-existing claims written to the old `claims-YYYY-MM-DD` shard are still found during commit/rollback. No manual migration is needed.

**Free-tier impact**: DO instance count increases from 1/day to (1 + N)/day per active date (5 with default settings). This is well within the 500k instance limit but does increase DO request count proportionally for fan-out operations.

### 20.2 Circuit Breaker for Coordinator Degradation (W2.3 + W2.1)

**Before**: coordinator failures logged first 3 at ERROR, then silently fell back to local throttle on every call (each call still attempts the HTTP round-trip and waits for timeout).
**After**: after `_DEGRADE_THRESHOLD` (3) consecutive failures, the circuit **opens** — coordinator calls are skipped entirely for `_recovery_probe_sec` (300s). After the cooldown, a single **half-open probe** tests recovery. Success closes the circuit; failure re-opens it.

**Ops-visible log lines**:

| Log Level | Message | Meaning |
|---|---|---|
| ERROR | `Coordinator unavailable (#N)` | Failure 1–3 (circuit still closed, each call retried) |
| WARNING | `Circuit breaker open: coordinator degraded after N failures` | Circuit opened — coordinator calls skipped for 300s |
| INFO | `Circuit breaker half-open: probing coordinator` | Recovery probe firing |
| INFO | `Circuit breaker closed: coordinator recovered` | Probe succeeded — normal operation resumed |

During degraded mode, **Runner Scale** kicks in: each runner's `TripleWindowThrottle` divides its `long_max` and `extra_max` by the number of active runners (propagated via the last successful heartbeat). This prevents N runners from collectively exceeding the single-runner rate limit while operating without coordinator orchestration.

### 20.3 DO Alarm Batch Limiting (W2.4)

**Before**: the 10-minute GC alarm iterated all claims + failures in one pass. On high-volume days with thousands of accumulated entries, this could exceed the free-tier 30s CPU limit.
**After**: the alarm processes at most **500 entries per invocation**. If more remain, a catch-up alarm fires after 60s. The alarm also correctly re-arms after firing (fixed a bug where `alarmScheduled` was never reset).

**Ops impact**: on days with very high claim volume, you may observe multiple alarm invocations at 60s intervals in the DO logs. This is expected catch-up behavior, not an error.

### 20.4 Constant-Time Token Comparison (W2.5)

`constantTimeEqual` no longer returns early when token lengths differ. The comparison now XORs lengths and iterates to `max(a.length, b.length)`, preventing timing side-channel leakage of the token length.

### 20.5 RunnerRegistry Prune Persistence (W2.6)

`handleActive()` (the `GET /active_runners` handler) now persists state after pruning stale runners. Previously, stale entries were removed from memory but not written back to storage, causing them to reappear on the next DO wake-up.

### 20.6 Stage Timestamp Preservation (W2.7)

When a runner re-stages a movie without holding an active claim (claim TTL elapsed), the original `ts` is now preserved instead of being refreshed to `now`. This prevents indefinite orphan accumulation — `sweep_orphan_stages` can eventually catch truly abandoned stages based on their original timestamp.

### 20.7 Heartbeat Runner Count (W2.1)

The heartbeat response now includes `active_runners_count` (post-prune snapshot). The Python client propagates this to `MovieSleepManager.set_active_runners()`, which scales `TripleWindowThrottle` limits. This field is also present in the `alive=false` (eviction) response path so the evicted runner can still scale its throttle correctly before re-registering.

## 21. ADR-008: Session Reporting, Alerts, and Mutation Dashboard (Phase 1 + 2)

ADR-008 closes the operator-loop gap between Coordinator state and CICD repo state. It is rolled out in two phases on the same Cloudflare Worker; both phases are backward-compatible (old Python clients keep working unchanged).

### 21.1 Why

Two operational pain points motivated this work:

1. **Pipeline failure visibility**: when a runner failed mid-session, the dashboard showed it as "alive then gone" — operators had to cross-reference D1 `ReportSessions` and GitHub Actions logs to learn what failed and why.
2. **No mutation surface**: clearing a bad CF state, banning a flapping proxy, or freezing the pipeline for a release required `wrangler` commands. The dashboard was read-only.

### 21.2 What

**Phase 1 — observability + simple mutations** (no Python consumer dependency):

- Runner-reported session lifecycle. Every `register` / `heartbeat` / `unregister` carries an optional `session` payload (`session_id`, `status`, `write_mode`, `failure_reason`, `report_type`). The Worker upserts it into a `sessions` SQLite table inside `RunnerRegistry`. The dashboard renders three buckets: **Active**, **Recent Failures (24h)**, **Recent Committed**.
- Alerts with webhook dispatch. Three trigger sources emit alerts:
  - `session_failed` — runner reports `status: "failed"`.
  - `ban_spike` — `ban_spike_threshold` bans in 1h for one proxy (hourly bucket idempotency).
  - `login_cooldown` — `GlobalLoginState` enters its P2-C cooldown.
  - Plus operator-triggered `manual_test` (the dashboard's "Test alert webhook" button).
  Each alert is written to `alert_history` and POSTed to every matching webhook configured via `alert_webhooks_json`. Retries: 2 with exponential back-off (1s / 3s), per-webhook 10s timeout. Alert idempotency keys (`sessfail-<session_id>`, `banspike-<proxy_id>-<hour_bucket>`, `logincd-<cooldown_until_ms>`) prevent multiplication when the same event re-fires.
- Dashboard banner shows unacknowledged alerts; **Ack** button flips the `ack` flag (visible in history forever, just suppressed from the banner).
- Mutation buttons that don't need Python consumer work:
  - **Ban proxy** / **Unban proxy** — per-row in the proxy table. Routes through `POST /proxies/ban` (wraps `/report kind=ban`) and `POST /proxies/unban`.
  - **Force re-login** — clears the current cookie via `POST /login/invalidate_force`. The next runner re-logs in.
  - **Pause pipeline 1h / 3h / 6h / 24h** — sets `pipeline_paused_until` in ConfigState. Spider startup honours this and exits 0 with a marker file.
  - **Test alert webhook** — fires `POST /alerts/test` to verify your webhook destination receives events.

**Phase 2 — runtime signals + inline config edit** (relies on the W6.A.1/W6.A.2 Python consumer wiring already shipped):

- **Throttle global x2 / x4** buttons send `POST /signal { kind: "throttle_global", factor, ttl_ms }`. The spider's `_apply_active_signals` reconciles every heartbeat: `MovieSleepManager.set_global_factor(factor)` multiplies every worker's sleep range.
- **Pause all runners** sends `POST /signal { kind: "pause_all", ttl_ms }`. `MovieSleepManager.set_pause_until_ms()` blocks every `sleep()` call until expiry.
- **Resume (clear signals)** sends `POST /signal { kind: "resume" }`. The Worker drops every active signal in one go.
- **Inline config edit** — every config key has an `[edit]` button. PATCHes `/config` in the audit-friendly single-key shape `{ key, value, reason }` so each change leaves one row in `config_audit_log`.

### 21.3 Configuration

New `CONFIG_ALLOWED_KEYS` (PATCH `/config`):

- `alert_webhooks_json` — JSON-encoded `Array<{url, kinds: AlertKind[]}>`. Empty string disables all webhooks. URLs MUST be `https://`.
- `ban_spike_threshold` — integer; default 3 bans / 1h triggers `ban_spike`.
- `pipeline_paused_until` — wall-clock ms-epoch (string). Runners exit at startup when this is in the future.
- `pipeline_pause_reason` — free-form operator note shown to runners that exit due to a pause.

New env var (`wrangler.toml [vars]`): `BAN_SPIKE_THRESHOLD` — fallback when `ban_spike_threshold` ConfigState key isn't set.

Example webhook config:

```bash
# Send only session_failed + login_cooldown to Slack, everything to Discord
curl -X PATCH https://proxy-coordinator.example.workers.dev/config \
  -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
  -H "content-type: application/json" \
  -d '{"key":"alert_webhooks_json","value":"[{\"url\":\"https://hooks.slack.com/...\",\"kinds\":[\"session_failed\",\"login_cooldown\"]},{\"url\":\"https://discord.com/api/webhooks/...\"}]","reason":"alert routing"}'
```

### 21.4 New API Endpoints (Phase 1 + 2)

Read (cookie or Bearer auth):

- `GET /sessions?since_ms=&limit=` — three buckets of session rows.
- `GET /alerts?since_ms=&limit=` — alert history (descending ts).
- `GET /ops/snapshot` — now includes `sessions` and `alerts` blocks.

Mutation (cookie or Bearer auth):

- `POST /alerts/ack` — body `{id}`. Flips `ack=1` on the row.
- `POST /alerts/test` — body `{summary?}`. Records + dispatches a `manual_test` alert.
- `POST /proxies/ban` — body `{proxy_id, ttl_ms?, reason?}`. Wraps `/report kind=ban`.
- `POST /proxies/unban` — body `{proxy_id, reason?}`. Wraps `/report kind=unban`.
- `POST /login/invalidate_force` — reads current version and invalidates. Next runner re-logs in.
- `POST /signal` — see Phase 2 above; already existed (W5.4) but now reachable via dashboard cookie.

### 21.5 Operator SOP

Day-to-day:

- **A pipeline failed at 03:00 UTC and I want to know why** -> open dashboard, scroll to **Sessions -> Recent Failures (24h)**, hover the `failure_reason` column. Click the GH run id to jump to logs.
- **A proxy started banning every request** -> find it in the proxy table, click **Ban** with a TTL (e.g. 6h). The next `/lease` for that proxy returns `banned=true` immediately. Confirm via the Cloudflare logs that `ban_spike` did not also fire (means the threshold is tuned right).
- **Login is failing across all runners** -> click **Force re-login**. The next runner will acquire the lease, log in, and publish a new cookie. Watch the `login` audit log via the History drawer.
- **Need to freeze ingestion for a release** -> click **Pause pipeline · 1h** (or longer). Already-running pipelines finish, but new GitHub Actions runs exit 0 immediately with a marker. Resume manually or wait for TTL.

Phase 2 (runtime signals — affect live runners within one heartbeat ~60s):

- **Cohort is hitting a CF wave** -> **Throttle global x2**, 30 min. Watch the dashboard ban + CF charts come back down. **Resume** when calm.
- **Detected scraper detection** -> **Pause all runners**, 15 min. All workers stop dispatching new requests immediately on next heartbeat. In-flight requests complete normally.

### 21.6 Webhook Payload Format

The Worker POSTs JSON like:

```json
{
  "id": "sessfail-20260516T180000.123456Z-abcd-ef12",
  "kind": "session_failed",
  "ts": 1747416000000,
  "severity": "warning",
  "summary": "Session 20260516T180000.123456Z-abcd-ef12 failed (workflow=DailyIngestion, write_mode=audit, holder=runner-abc123)",
  "details": {
    "session_id": "20260516T180000.123456Z-abcd-ef12",
    "workflow_run_id": "12345678",
    "workflow_name": "DailyIngestion",
    "write_mode": "audit",
    "failure_reason": "spider crash: connection reset",
    "holder_id": "runner-abc123"
  }
}
```

Webhook receivers should look at `kind` to route. Two retries with exponential back-off + 10s per-request timeout — beyond that the alert is dropped from the webhook path (still in `alert_history` on the dashboard).

### 21.7 Rollback

Phase 1 mutations are independent of Phase 2 signals — either can be turned off without affecting the other.

- **Disable webhooks**: PATCH `/config { key: "alert_webhooks_json", value: "" }`. Alerts still land in `alert_history` for the dashboard.
- **Disable session reporting**: roll back the Python client to a pre-ADR-008 build. The Worker drops malformed/missing session payloads silently — old clients are forward-compatible.
- **Disable mutation buttons in dashboard**: delete the `data-op="..."` attributes from `dashboard_html.ts` or revert the file. The endpoints stay; only the UI vanishes.
- **Full revert**: drop the `sessions` + `alert_history` tables (DO storage) and revert the source files. The `wrangler.toml` doesn't need changes — no new DO bindings.

### 21.8 Phase 3 — MovieClaim / WorkDistributor panels + responsive UI

Phase 3 adds three observability surfaces that were already supplied by existing DOs but never rendered on the dashboard. No new bindings, no schema migration — purely consumption of data the Worker already had.

**Today's Claims panel** — reads `GET /movie_claim/stats?date=YYYY-MM-DD` which fans out across every sub-shard of the current Asia/Singapore date and sums the per-shard counters. Six numbers surface on the dashboard:

- `claims_active` — in-flight claims that haven't expired
- `staged_count` — staged completions awaiting commit / rollback (Phase-1 protocol)
- `completed_committed_count` — finished movies for the day (the badge headline)
- `failures_count` — distinct hrefs with at least one recorded failure
- `in_cooldown_count` — hrefs whose `next_attempt_at > now`
- `dead_lettered_count` — hrefs past the `MOVIE_CLAIM_DEAD_LETTER_THRESHOLD`

A high `dead_lettered_count` is the canonical "investigate me" signal — the same hrefs keep failing across runners, so the cluster is reaching its scrape ceiling.

**Work queue panel** — reads `GET /work/stats` which the WorkDistributor DO already exposed. Four numbers:

- `queue_size` — total items (visible + leased)
- `visible` — claimable items not currently leased
- `leased` — items held by an active visibility lease
- `oldest_enqueued_at_ms` — age of the longest-waiting item; >30 min turns red

The dashboard surfaces this even though the spider's `fetch_engine` doesn't yet consume the queue; the WorkDistributor is staged and the panel shows whether anyone is using it.

**Responsive layout** — three changes:

- Drawer width clamped to `min(360px, 95vw)` so it fits inside narrow mobile viewports.
- A `@media (max-width: 480px)` breakpoint shrinks padding, drops the stats grid to 2 columns, makes tables horizontally scrollable, and makes the first column sticky so the proxy identifier stays visible while scrolling.
- uPlot charts now compute their width from `clientWidth` of their `.chart-body` container; a single `ResizeObserver` calls `chart.setSize()` whenever the container width changes. No more 360px hard-coded width.

**Explicitly NOT in Phase 3** (deferred):

- Active proactive proxy health checks — would consume Free-plan outbound traffic.
- KV/D1-backed pool registry for `/add_proxy` / `/remove_proxy` — requires Paid plan.
- Role-based dashboard auth — single `DASHBOARD_PASSWORD` is enough for current ops.
- Switching `fetch_engine` to pull from WorkDistributor — independent decision, panel exists so the data is observable when that switch happens.
- Active cookie probing in GlobalLoginState — relies on the existing spider-triggered re-login path.

### 21.9 Phase 3 Rollback

- **Hide the MovieClaim panel**: delete `id="movie-claim-stats"` and `renderMovieClaimStats` from `dashboard_html.ts`. The `/movie_claim/stats` endpoint stays callable via `curl`.
- **Hide the Work queue panel**: same pattern, delete `id="work-stats"` and `renderWorkStats`.
- **Revert chart sizing**: restore the `width: 360` constant in `chartOptions` and drop the `ResizeObserver` block. Charts go back to fixed-size at the cost of mobile usability.
- **Full revert**: revert `src/movie_claim_state.ts`, `src/index.ts`, and `src/dashboard_html.ts` to pre-Phase-3 commit. No DO storage changes — the `handleClaimStats` method is read-only.
