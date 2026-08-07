# BFR-024: Managed-challenge blind spot burns the whole proxy pool

**Status**: Fixed
**Date**: 2026-08-07
**Severity**: Critical
**Affected**: `javdb/infra/request.py`, `javdb/spider/html_validators.py`, `javdb/spider/fetch/fetch_engine.py`
**Related**: [ADR-043](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.md), runs [31106061181](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31106061181) / [31124515791](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31124515791)

---

## Verification

`TestIngestion` on the fix branch ([31144998987](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31144998987)),
run while port 8000 was still unreachable:

```
⚠ RequestHandl  [Direct] Proxy=Hyderabad-ARM1 returned Cloudflare challenge page (size=5841 bytes)
  FetchEngine   [page-1][worker=Hyderabad-ARM1] Site-wide Cloudflare challenge — re-queued without counting toward soft-ban (1/28 proxies)
⚠ RequestHandl  [CF Bypass] Proxy=Johannesburg-ARM1: service unreachable at http://129.xxx.xxx.230:8000 (ConnectTimeout) — bypass disabled for this proxy for the rest of the run
```

The challenge is now visible in the log at INFO level, no proxy is banned for
it, and each unreachable bypass is probed once instead of once per fallback
step. Proxies that failed for genuinely proxy-specific reasons (Jeddah's
`BoringSSL SSL_connect: Connection closed abruptly`) were still soft-banned,
which is the intended discrimination.

## Symptom

Daily Ingestion failed on 2026-08-06 and 2026-08-07 with zero entries parsed and
every proxy banned:

```
──── 📊 OVERALL SUMMARY ─────────────────────
   pages    1-10
   found    0
   parsed   0
ProxyPool     Proxy pool · available=9/28 · cooldown=0 · banned=19 · no-proxy=false
Report        Currently banned proxies: 28 [session-scoped]
✗ Report      Spider produced NO results and proxy ban(s) were detected — all proxies may be blocked.
Spider exited with code 2
```

All 28 proxies were banned within ~8 minutes. The INFO-level log showed only
`CF Bypass initial attempt failed` and `Process returned None` — none of the
warnings that the CF bypass path emits when it actually receives a bad
response, which is what made the cause invisible.

A DEBUG re-run ([31141477733](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31141477733))
produced the two missing facts:

```
[Direct Proxy=Hyderabad-ARM1][curl_cffi] HTTP 403 Forbidden (body 5841 bytes)
[CF Bypass Proxy=Jeddah-ARM1] ConnectTimeout: HTTPConnectionPool(host='144.x.x.88', port=8000):
    Connection to ... timed out. (connect timeout=60)
```

## Root Cause

Three independent faults lined up. Only the first was external.

**1. Infrastructure (external trigger).** javdb.com turned on a Cloudflare
managed challenge site-wide, and at the same time port 8000 stopped accepting
connections on all 28 proxy hosts. `ConnectTimeout` rather than
`ConnectionRefused` means the packets were dropped by a firewall, not that the
bypass process had died. Verified independently: from an unrelated host,
`12300` (squid) accepts connections while `8000` does not.

**2. The challenge detector only knew the old page.** `request.py` identified a
Cloudflare wall as:

```python
is_turnstile = 'Security Verification' in html_content and 'turnstile' in html_content.lower()
```

The managed-challenge interstitial javdb now serves contains neither string —
it is a 5.4 KB body carrying `Just a moment...` and a
`/cdn-cgi/challenge-platform/` script. Only the first is *discriminating* —
see the Fix section — and `Just a moment` appeared nowhere in the codebase. Consequently every downstream branch keyed on `is_turnstile` went
dark: no `refresh_bypass_cache()` (the only path that re-acquires
`cf_clearance`), no diagnostic warning, and `is_cf_bypass_failure()` returned
`False` because the body is over the 1500-byte cap. The design flaw is that the
predicate encoded *one rendering* of a challenge rather than the property that
matters ("Cloudflare is standing in front of the page").

**3. Every failure was charged to the proxy.** `_EngineWorker` soft-bans a
proxy after 2 consecutive `None` returns, and `_get_page_with_cf_bypass()`
raises `ProxyBannedError` after `cf_bypass_ban_threshold` exhausted cascades.
Both treat "this fetch failed" as "this proxy is bad". Neither cause here was
proxy-specific: a site-wide challenge is served to every egress IP, and an
unreachable bypass service is a property of the host's firewall. The pool was
destroyed for a condition no proxy could influence, and the run reported
"all proxies may be blocked" — pointing the operator at exactly the wrong
subsystem.

The 60-second connect timeout amplified all of it: each fallback step blocked
for a full minute against a port that would never answer, so workers exhausted
their retries long before any proxy could be tried a second time.

## Fix

**Infrastructure**: port 8000 was never meant to be reachable on the proxy's
public IP — every bypass service (the previous Camoufox-based one and its
FlareSolverr replacement alike) intentionally binds to loopback only, since
the service itself carries no auth. Opening it to the public internet would
have been a real regression, not a fix. The actual gap was that none of the
three ingestion workflows ever set `CF_BYPASS_VIA_PROXY`, so the runner
defaulted to dialling `{proxy_ip}:8000` directly instead of tunnelling the
request *through* the proxy to `127.0.0.1:8000` — the exact use case that
flag exists for (see `docs/handbook/*/self-hoster/cloudflare-bypass.md`).
Squid's ACL already allowed the `to_localhost` forwarding this needs. Fixed
by setting the `CF_BYPASS_VIA_PROXY` repo variable to `true` and wiring
`VAR_CF_BYPASS_VIA_PROXY: ${{ vars.CF_BYPASS_VIA_PROXY || 'False' }}` into
all three workflows (`TestIngestion.yml`, `DailyIngestion.yml`,
`AdHocIngestion.yml`) — zero changes needed on any of the 34 proxy hosts.

The code changes below make the same failure survivable and legible even when
a proxy's bypass service is genuinely unreachable for some other reason:

- **`html_validators.py`** — new `is_cf_challenge_page()`, matching
  `just a moment...` and the legacy `security verification`. It deliberately
  does **not** match `/cdn-cgi/challenge-platform/`: that was the first
  attempt, but Cloudflare injects the beacon script into every response on a
  bot-management zone, so it false-positived on genuine pages (a real
  CF-Bypass response carrying full movie-list content was discarded as a
  challenge). It also excludes the Cloudflare *block* page (error 1020),
  which IS IP-specific and must keep counting against the proxy.
- **`request.py`** — all three challenge predicates (`_fetch_direct`,
  `_process_html`, `_fetch_with_cf_bypass`) now route through that one
  function, so a future challenge-page change is a one-line fix rather than
  three. The 403 branches of both `_do_request` and `_do_request_curl_cffi`
  skip the health-failure record for a challenge — the curl_cffi one is the
  path that actually fires, since `_fetch_direct` prefers it.
- **`request.py`** — `_get_page_direct` no longer calls
  `mark_failure_and_switch()` after a challenge. That call charges a failure
  to the pool (`PROXY_POOL_MAX_FAILURES` → ban), and the next proxy would be
  served the same challenge anyway.
- **`request.py`** — the CF bypass request uses a 5 s connect timeout (60 s
  read), and a failed connect adds the service URL to a per-handler
  `_bypass_unreachable` set. Subsequent pages skip that proxy's bypass
  immediately instead of re-paying the timeout; `refresh_bypass_cache()`
  honours the same set. One WARNING is emitted per proxy when this happens.
- **`request.py`** — a 403 whose body is a challenge page no longer records a
  health failure via `_record_request_complete()` (it would deflate every
  proxy's coordinator score equally), and an exhausted fallback cascade that
  ends on a challenge page sets `last_site_challenge` and returns before the
  ban check, alongside the existing maintenance-page / login-page early-outs.
  The penalty tracker (`_record_cf_event`) still fires — that one is a pacing
  signal, not a blame signal.
- **`fetch_engine.py`** — the worker reads `handler.last_site_challenge` and,
  when set, re-queues without incrementing `_consecutive_none_count`. The task
  still accumulates `failed_proxies` and terminates as `all_proxies_failed`
  once the pool is exhausted, so a fully-challenged site ends the run promptly
  instead of looping — but with an intact pool and an accurate log line.

- **`context.py` / `report.py`** — `ProxyRunState.site_challenge_seen` records
  that a site-wide challenge occurred, and the summary report exits 2 when a
  run produced zero entries with that flag set. Necessary because the old
  loud failure ("NO results and proxy ban(s) were detected") was reached
  *through* the ban path: removing the ban also removed the failure signal.
  The first verification run of this fix exited 0 with a header-only CSV,
  which for DailyIngestion would mean an auto-commit and a success email while
  the site was fully walled off.

## Side Effects

- **A fully-challenged site now fails with `all_proxies_failed` instead of
  `all_proxies_banned`.** It still exits 2 with zero entries, but via the new
  `site_challenge_seen` check rather than the ban check; the attribution, the
  message, and the surviving pool state all change.
- **Wall-clock under a total outage is comparable, not worse.** Measured on
  TestIngestion (3 pages): 12m50s to a clean exit versus 15m11s to the
  banned-out failure before the fix, because the 5 s connect timeout saves
  more than the extra proxy attempts cost.
- **`_record_request_complete` no longer sees challenge 403s**, so the
  coordinator's `failureEvents` will read lower during a site-wide challenge.
  This is the intended correction — those samples measured javdb's WAF, not
  proxy quality.
- **`_bypass_unreachable` is per-`RequestHandler`, i.e. per worker, and never
  expires within a run.** A bypass service restarted mid-run stays skipped
  until the next run. Accepted: the alternative (re-probing) reintroduces the
  stall this fix removes, and runs are short.
- `is_cf_challenge_page()` matches on substrings, so a JavDB page that ever
  quoted the literal text `just a moment...` would be misclassified as a
  challenge. Judged not worth guarding against.

## Follow-Up

- [ ] Consider a startup reachability sweep over all proxies' `:8000` so the
      run logs a single "bypass unavailable on N/28 proxies" summary up front
      rather than discovering it lazily.
- [ ] Alert when `_bypass_unreachable` covers the whole pool — that state is
      silent capacity loss even when the run happens to succeed.
- [ ] `SiteContractSentinel` should assert that javdb.com is reachable without
      a challenge, so this is caught by the canary before the daily run.
