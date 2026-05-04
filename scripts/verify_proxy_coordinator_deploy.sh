#!/usr/bin/env bash
# verify_proxy_coordinator_deploy.sh — post-deploy smoke check.
#
# Hits the four DO endpoints with a small synthetic payload, then prints
# a pass/fail table.  Intended to be run **after** `wrangler deploy` and
# **before** triggering a real AdHocIngestion, so misconfigured tokens
# / migrations / bindings surface in seconds instead of after a 30-min
# spider run.
#
# Usage:
#   PROXY_COORDINATOR_URL=https://… \
#   PROXY_COORDINATOR_TOKEN=… \
#       ./scripts/verify_proxy_coordinator_deploy.sh
#
# Optional:
#   VERIFY_HOLDER_ID=runner-verify-2026-05-03   # default uses date+pid
#   VERIFY_PROXY_ID=verify-canary               # default same
#
# Exit codes:
#   0 = all four DO classes responded OK
#   1 = at least one endpoint failed (see printed table)
#   2 = misuse / missing required env vars

set -uo pipefail

if [[ -z "${PROXY_COORDINATOR_URL:-}" || -z "${PROXY_COORDINATOR_TOKEN:-}" ]]; then
    echo "ERROR: PROXY_COORDINATOR_URL and PROXY_COORDINATOR_TOKEN are required." >&2
    echo "" >&2
    echo "Run with:" >&2
    echo "  PROXY_COORDINATOR_URL=https://…workers.dev \\" >&2
    echo "  PROXY_COORDINATOR_TOKEN=… \\" >&2
    echo "      ./scripts/verify_proxy_coordinator_deploy.sh" >&2
    exit 2
fi

URL="${PROXY_COORDINATOR_URL%/}"
AUTH="Authorization: Bearer ${PROXY_COORDINATOR_TOKEN}"
CT="content-type: application/json"

HOLDER_ID="${VERIFY_HOLDER_ID:-runner-verify-$(date +%s)-$$}"
PROXY_ID="${VERIFY_PROXY_ID:-verify-canary-$$}"

declare -i fail_count=0

# Helper: hit endpoint, print PASS/FAIL row, increment fail_count on non-2xx.
check() {
    local label="$1"; shift
    local method="$1"; shift
    local path="$1"; shift
    local data="${1:-}"
    local args=(-sS -o /tmp/coord_verify_body.$$ -w '%{http_code}' \
                -H "$AUTH" -H "$CT" -X "$method" "${URL}${path}")
    if [[ -n "$data" ]]; then
        args+=(--data "$data")
    fi
    local code
    code=$(curl "${args[@]}" 2>/dev/null || echo "000")
    if [[ "$code" =~ ^2 ]]; then
        printf "  %-40s  PASS  HTTP %s\n" "$label" "$code"
    else
        printf "  %-40s  FAIL  HTTP %s\n" "$label" "$code"
        printf "      body: %s\n" "$(head -c 200 /tmp/coord_verify_body.$$ 2>/dev/null || echo '<empty>')"
        fail_count+=1
    fi
}

echo "== Proxy coordinator post-deploy smoke check =="
echo "URL:       $URL"
echo "HolderID:  $HOLDER_ID"
echo "ProxyID:   $PROXY_ID"
echo

echo "1) Worker /health (no auth):"
worker_code=$(curl -sS -o /dev/null -w '%{http_code}' "${URL}/health" || echo "000")
if [[ "$worker_code" =~ ^2 ]]; then
    printf "  %-40s  PASS  HTTP %s\n" "/health" "$worker_code"
else
    printf "  %-40s  FAIL  HTTP %s\n" "/health" "$worker_code"
    fail_count+=1
fi
echo

echo "2) ProxyCoordinator (per-proxy):"
check "POST /lease (intended_sleep_ms=0)" POST "/lease" \
    "{\"proxy_id\":\"$PROXY_ID\",\"intended_sleep_ms\":0}"
check "POST /report kind=success latency_ms=42" POST "/report" \
    "{\"proxy_id\":\"$PROXY_ID\",\"kind\":\"success\",\"latency_ms\":42}"
check "POST /report kind=cf"                       POST "/report" \
    "{\"proxy_id\":\"$PROXY_ID\",\"kind\":\"cf\"}"
check "GET  /state (debug dump)"                   GET  "/state?proxy_id=${PROXY_ID}"
echo

echo "3) GlobalLoginState (singleton):"
check "GET  /login_state"                          GET  "/login_state"
check "POST /login_state/record_attempt failure"   POST "/login_state/record_attempt" \
    "{\"holder_id\":\"$HOLDER_ID\",\"proxy_name\":\"$PROXY_ID\",\"outcome\":\"failure\"}"
echo

echo "4) MovieClaimState (per-day shard):"
TODAY=$(date -u +%Y-%m-%d)
HREF="/v/__verify_canary__"
check "POST /claim_movie (smoke)" POST "/claim_movie" \
    "{\"href\":\"$HREF\",\"holder_id\":\"$HOLDER_ID\",\"ttl_ms\":60000,\"shard_date\":\"$TODAY\"}"
check "POST /release_movie (smoke)" POST "/release_movie" \
    "{\"href\":\"$HREF\",\"holder_id\":\"$HOLDER_ID\",\"shard_date\":\"$TODAY\"}"
echo

echo "5) RunnerRegistry (singleton):"
# `date +%s%3N` works on GNU date (Linux) but BSD date (macOS) leaves %3N as
# the literal string, producing invalid JSON. Use python3 for a portable
# millisecond timestamp; fall back to seconds*1000 if python3 is missing
# (the Worker only uses started_at for ordering, not high-resolution).
if command -v python3 >/dev/null 2>&1; then
    NOW_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
else
    NOW_MS=$(( $(date +%s) * 1000 ))
fi
check "POST /register" POST "/register" \
    "{\"holder_id\":\"$HOLDER_ID\",\"workflow_run_id\":\"verify-$$\",\"workflow_name\":\"local-verify\",\"started_at\":$NOW_MS,\"proxy_pool_hash\":\"verify\",\"page_range\":\"verify\"}"
check "POST /heartbeat"   POST "/heartbeat"   "{\"holder_id\":\"$HOLDER_ID\"}"
check "GET  /active_runners" GET "/active_runners"
check "POST /unregister"  POST "/unregister"  "{\"holder_id\":\"$HOLDER_ID\"}"
echo

# 6) movie_claim_recommended derived field (auto-toggle contract).
# A new Worker MUST include `movie_claim_recommended` and
# `movie_claim_min_runners` on every register/heartbeat response.  Old
# Workers omit them — clients survive but fall back to "single-runner
# safe" defaults; the verify script flags the missing fields as a
# warning so operators know they're on a pre-auto Worker version.
echo "6) Auto-toggle derived fields (movie_claim_recommended, P1-B + P2-E):"
register_body=$(curl -sS -H "$AUTH" -H "$CT" -X POST \
    --data "{\"holder_id\":\"$HOLDER_ID-derived\",\"proxy_pool_hash\":\"verify\"}" \
    "${URL}/register" 2>/dev/null || echo '')
if echo "$register_body" | grep -q '"movie_claim_recommended"'; then
    if echo "$register_body" | grep -q '"movie_claim_min_runners"'; then
        printf "  %-40s  PASS  fields present\n" "register response carries auto-toggle"
    else
        printf "  %-40s  WARN  movie_claim_min_runners missing\n" \
            "register response (partial)"
    fi
else
    printf "  %-40s  WARN  Worker predates auto-toggle PR-1\n" \
        "register response (auto-toggle)"
    echo "      hint: redeploy Worker to pick up MOVIE_CLAIM_MIN_RUNNERS,"
    echo "            then re-run this script.  Until then, set"
    echo "            MOVIE_CLAIM_ENABLED=true to keep claim coordination on."
fi
# Cleanup the derived-field probe runner so it doesn't pollute /active_runners.
curl -sS -o /dev/null -H "$AUTH" -H "$CT" -X POST \
    --data "{\"holder_id\":\"$HOLDER_ID-derived\"}" \
    "${URL}/unregister" >/dev/null 2>&1 || true
echo

if (( fail_count > 0 )); then
    echo "RESULT: $fail_count check(s) failed.  See body lines above."
    exit 1
fi
echo "RESULT: all DO classes responded OK.  Safe to trigger AdHocIngestion."
echo
echo "Next steps:"
echo "  1. Trigger an AdHocIngestion run with PROXY_COORDINATOR_URL set."
echo "  2. In its logs, expect FOUR initialised lines:"
echo "       - Proxy coordinator client initialised: base_url=…"
echo "       - Login-state client initialised: base_url=…"
echo "       - Movie-claim client initialised: base_url=…, mode=auto|force_on"
echo "         (only if MOVIE_CLAIM_ENABLED in {auto, true} AND /health is up)"
echo "       - Runner-registry client initialised: base_url=…"
echo "     In auto mode, additional INFO lines on cohort transitions:"
echo "       - movie-claim auto: mounted (active_runners >= threshold)"
echo "       - movie-claim auto: unmounted (active_runners < threshold)"
echo "  3. Roll back options:"
echo "     * Soft (keep DO running): GH Variables → MOVIE_CLAIM_ENABLED=false"
echo "     * Legacy P1-B always-on:  GH Variables → MOVIE_CLAIM_ENABLED=true"
echo "     * Full disable:           delete the PROXY_COORDINATOR_URL Variable;"
echo "       the next run should log \"Proxy coordinator not configured … using"
echo "       local throttling only\" and behave identically to the pre-DO baseline."
exit 0
