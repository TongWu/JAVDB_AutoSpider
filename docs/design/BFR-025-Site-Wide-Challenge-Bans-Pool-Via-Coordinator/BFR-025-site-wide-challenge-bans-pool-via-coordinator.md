# BFR-025: Site-wide challenge bans the whole pool through the coordinator

**Status**: Fixed
**Date**: 2026-08-07
**Severity**: Critical
**Affected**: `javdb/infra/request.py`, `javdb/spider/fetch/fetch_engine.py`, `javdb/spider/runtime/context.py`
**Related**: [BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.md), [ADR-043](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.md), run [31163278517](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31163278517), probe run [31173702032](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31173702032)

---

## Symptom

Daily Ingestion run [31163278517](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31163278517)
(2026-08-07T08:49Z) ran on `1775d7d` — the BFR-024 fix merged and
`CF_BYPASS_VIA_PROXY=True` — and still failed:

```text
17:01:59    FetchEngine   [startup] Proxy 'Hyderabad-ARM1' already banned — skipping worker
   ... (28 lines, every proxy in PROXY_POOL) ...
RuntimeError: FetchEngine: all proxies are banned, cannot start
Spider exited with code 1
```

The index phase had *succeeded* — `Fetched and parsed 10 pages (parallel)`,
29 phase-2 entries discovered. The run died at Phase 2 engine start.

Event counts in that run:

| Log event | Count |
| --- | --- |
| `returned Cloudflare challenge page` | 325 |
| `Site-wide Cloudflare challenge — re-queued without counting toward soft-ban` | 188 |
| `Soft-banned after 2 consecutive None returns` | **2** |

So 26 of the 28 bans had no local origin. A `ProxyUnban` workflow
([31162302129](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31162302129))
had reported `done: 28/28 succeeded` 14 minutes earlier, and the run's own
engine started clean with 28 workers and zero `[startup] ... already banned`
lines. The pool was poisoned from scratch, inside this run, in 11 minutes.

## Root Cause

Two faults, both downstream of BFR-024's fix being incomplete.

**1. The remote ban tier was never exempted.** BFR-024 stopped a site-wide
challenge from producing a *local* soft-ban, but `_get_page_direct` in
`request.py` still called `self._record_cf_event(proxy_name)` on the challenge
branch. `git show 1775d7d` shows `self.last_site_challenge = True` added as a
`+` line directly above an *unchanged* `self._record_cf_event(proxy_name)`:
the accompanying comment reasoned only about `penalty_tracker`, but
`_record_cf_event` fires two sinks — the local pacing tracker **and**
`_on_cf_event`, which `fetch_engine.py` wires to
`coordinator.report_async(proxy_id, "cf")`.

Per ADR-043 D2, the Worker-side Durable Object bans a proxy when
`cfEvents.length >= CF_AUTO_BAN_THRESHOLD` (default 6) **and**
`successEvents.length === 0`, with `CF_BAN_TTL_MS` defaulting to 6 hours (D3).
Under a site-wide wall both conditions hold for every proxy simultaneously:

- 325 challenges across 28 proxies is ~11.6 each, well past the threshold.
- `_fetch_direct` returns on the challenge branch *before* the success report,
  so `successEvents` stays empty.

Each DO bans independently; the next `POST /lease` returns `banned: true`;
`_mirror_remote_ban_locally` in `javdb/spider/runtime/sleep.py` writes it into
the process-global Rust ban manager and **logs nothing on success**. Rust's ban
manager documents its bans as "permanent for the lifetime of the process", so
the remote 6-hour TTL is irrelevant locally — and Phase 1 and Phase 2 share a
process.

ADR-043 **D7 named this exact scenario as out of scope**: "When *every* proxy
fails CF … banning individual proxies is the wrong remedy. This ADR
deliberately does **not** add cross-DO / global circuit-breaking." Its risk
list accepts "A genuine site-wide CF outage auto-bans the whole pool for up to
6 h". BFR-025 is that accepted risk arriving.

**2. The bypass tier was reached only after ~27 failed direct attempts per
page.** In `_simple_process` — the cascade the parallel index/detail paths
actually use (`ParallelFetchBackend.simple`) — the CF-bypass leg sat behind:

```python
if ctx.queue_pressure == 'low' and not task.login_only:
    active = ctx._worker._active_workers
    if len(task.failed_proxies) < max(1, active - 1):
        return None
```

`_queue_pressure` returns `'low'` when `qsize <= 1 and active > 2`. Ten index
tasks against 28 workers means the queue is empty essentially always, so each
page had to fail the direct path on nearly every proxy before it was allowed
one bypass attempt. That is where the 325 challenges came from (~32 per page),
and every one of them was a CF event feeding Fault 1.

The sticky-bypass escape hatch could not help: `_should_shortcircuit_cf`
requires both `always_bypass_time` (the `--always-bypass-time` CLI flag, unset
by default) **and** `_cf_bypass_since`, which is only set by `_mark_cf_bypass()`
*after* a bypass success — a chicken-and-egg gate.

The design flaw is that the cascade encoded a fixed cost model — direct is
cheap, bypass is expensive, so try direct first and fall back reluctantly — as
if it were a constant. Under a site-wide challenge that model inverts: direct
is guaranteed to fail for every proxy, and the bypass tier is the only one that
can answer.

## Evidence

A diagnostic sweep (`apps/cli/ops/cf_bypass_probe.py`, workflow run
[31173702032](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31173702032))
probed all 28 proxies through the `CF_BYPASS_VIA_PROXY` topology. Identical
result on every proxy:

| Probe | Result |
| --- | --- |
| `DIRECT javdb.com` | 403, 5890 bytes, `<title>Just a moment...</title>` |
| `GET :8000/` | 404, 43 bytes, `{"status": "error", "message": "not found"}` |
| `GET :8000/html?url=` | **200, 61070 bytes, contains `movie-list`** |

So javdb was serving the managed challenge to 28/28 egress IPs with no
exceptions, and the bypass service answered every one of them with a real page.
The run failed with a fully working bypass tier it had been architecturally
prevented from using.

## Fix

- **`request.py`** — `_record_cf_event` gains a keyword-only
  `site_wide: bool = False`. When set, the local `penalty_tracker` still fires
  (pacing is correct either way) but the per-proxy coordinator report is
  replaced by a new `on_site_challenge()` callback. All six challenge-triggered
  call sites — the five in the CF-bypass fallback cascade plus the one in
  `_get_page_direct` — pass `site_wide=True`. The seventh call site, the
  exhausted-cascade report that runs *after* the challenge early-out, is
  deliberately unchanged: reaching it means the failure was not a challenge.
- **`request.py`** — `RequestHandler.__init__` gains `on_site_challenge`,
  documented as the run-level counterpart to the per-proxy `on_cf_event`.
- **`context.py`** — `ProxyRunState.site_challenge_active: bool`, the live
  counterpart to the latched `site_challenge_seen`.
- **`context.py`** — the sequential path's shared handler
  (`SpiderRuntime._init_request_handler`) wires `_global_site_challenge_cb`,
  which sets both flags and makes no coordinator report. That path has no mode
  switch to drive, but without the latch a fully walled `--sequential` run
  would exit 0 with a header-only CSV instead of failing.
- **`fetch_engine.py`** — `_site_challenge_cb` is wired unconditionally (not
  gated on a coordinator being configured) and sets both flags, logging once
  per transition.
- **`fetch_engine.py`** — new `_EngineWorker._should_prefer_bypass()` (sticky
  window OR live site challenge) replaces `_should_shortcircuit_cf()` at both
  cascade heads, and new `_mark_site_recovered()` clears the live flag when a
  direct fetch succeeds.
- **`fetch_engine.py`** — under bypass-first the direct leg still runs as the
  fallback, so it doubles as the recovery probe. Both cascades
  (`WorkerContext.fetch` and `_simple_process`) invert symmetrically.

- **`fetch_engine.py`** — the recovery probe is evaluated *before* the sticky
  `--always-bypass-time` short-circuit. The sticky window is unconditional (and
  permanent at `0`), so checking it first meant `_bypass_first_streak` never
  advanced and the periodic direct re-test never ran: a run that entered sticky
  mode stayed on the slower tier for its whole life even after javdb dropped
  the wall.
- **`fetch_engine.py`** — the low-queue-pressure shortcut stands down while
  `site_challenge_active` is set. Reaching it under a wall means a recovery
  probe's direct leg just failed; re-queuing to another proxy only buys another
  failed direct attempt, because the wall is not proxy-specific.
- **`fetch_engine.py` / `context.py`** — `SITE_CHALLENGE_REQUEUE_LIMIT = 8`
  caps the site-wide-challenge re-queue loop. The streak lives on
  `ProxyRunState` (shared by every worker, guarded by `site_challenge_lock`)
  and resets on any successful fetch; on the 8th consecutive empty proxy the
  task ends as `site_challenge_exhausted` instead of sweeping the remaining
  pool. Previously an ad-hoc page burned 80 minutes re-queuing across 28
  proxies that were all answering the same wall.
- **`request.py`** — the "bypass unreachable" cache became a 60-second TTL
  (`BYPASS_UNREACHABLE_TTL`) instead of a whole-run latch, because these
  failures flap (see Evidence). A lapsed entry is dropped on read and the host
  is re-probed; a failed re-probe reopens the window.
- **`request.py`** — `_masked_bypass_base()` reads the port back off the
  resolved service URL, so a host remapped by `CF_BYPASS_PORT_MAP` is logged at
  the port actually dialled rather than the pool-wide default.
- **`apps/cli/ops/cf_bypass_probe.py`** — the probe follows the run's own
  `CF_BYPASS_VIA_PROXY` instead of pinning the tunnelled topology, and
  `_validate_target` now rejects an out-of-range or non-numeric port.
- **11 workflows** — `VAR_CF_BYPASS_PORT_MAP_JSON` is wired into every
  config-generating step; without it the generated `config.py` fell back to an
  empty map and every remapped host was dialled on the default port.

## Side Effects

- **The coordinator's per-proxy `cfEvents` no longer include site-wide
  challenges**, so `penalty_factor` will read lower during an outage. This is
  the intended correction — those samples measured javdb's WAF, not proxy
  quality — but it also means the coordinator's cross-runner pacing no longer
  backs off on a site-wide wall. Local `penalty_tracker` pacing is unaffected.
- **The mode is a single run-level boolean shared by all workers**, so one
  worker's observation flips the whole run. That is deliberate (the condition
  is site-wide), but it means one false positive from `is_cf_challenge_page`
  would send the entire run through the slower bypass tier until a direct fetch
  succeeds.
- **A run gives up on a walled page after 8 proxies, not 28.** A genuinely
  partial outage — where the 9th proxy would have succeeded — now loses that
  page. The trade is deliberate: the streak resets on any success, so a pool
  that is mostly working never reaches the cap.
- **Under bypass-first, a task whose bypass fails still pays a direct
  attempt.** Wall-clock per failed task is therefore unchanged; what drops is
  the number of *guaranteed*-failed direct attempts on the happy path.
- **`site_challenge_active` is not persisted across runs**, so every run
  re-discovers the wall with one challenged direct fetch.

## Follow-Up

[BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.md)'s
open items carry forward unchanged. In addition:

- [ ] `_should_prefer_bypass` is per-run; a per-host variant would let a
      partially-walled site keep using direct where it works.
- [ ] The coordinator Worker (separate repo
      [`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator))
      still has no global circuit breaker; ADR-043 D7 remains open. A cross-DO
      "site-wide" signal would let it distinguish a WAF event from a bad proxy
      on its own.
- [x] `javdb/spider/auth/login.py` `_is_cloudflare_challenge` now shares
      `is_cf_challenge_page` with the fetch layer, and `_attempt_cf_warmup`
      tunnels through the proxy via `_build_bypass_proxies` instead of dialling
      the runner's own loopback (commit `a208076`).
- [x] `javdb/spider/fetch/fallback.py` no longer gates the sequential path's CF
      bypass on `is_cf_bypass_reachable()` in pool mode or under
      `CF_BYPASS_VIA_PROXY`; the probe is kept only for genuinely local
      deployments (commit `dba22ce`).
