# ADR-043: Coordinator-Side Proxy Ban Sharing and CF Auto-Ban

**Status:** Proposed
**Date:** 2026-05-31
**Author:** Ted
**Related Implementation Plans:**
[IMP-ADR043-01](IMP-ADR043-01-cf-auto-ban-worker.md) (Phase 1 — Worker-side CF auto-ban escalation),
[IMP-ADR043-02](IMP-ADR043-02-bfr009-ban-dispatch-and-hardban-ttl.md) (Phase 2 — fix BFR-009 Rust→Python ban dispatch + hard-ban DO sharing)

## Context

When a proxy stops working, two distinct failure modes need cross-runner
handling, and today **neither is shared correctly across GitHub Actions runners**.

### Failure mode 1 — persistently failing the CF wall (transient)

A proxy's exit IP gets singled out by Cloudflare and keeps hitting the CF
challenge while other proxies still pass.

- On a CF challenge the spider calls `mark_proxy_cf_bypass(...)`
  (`javdb/spider/runtime/proxy_state.py:114`), which routes the proxy through the
  CF-bypass service but **keeps it in rotation**. A proxy that can never clear CF
  stays selectable and is retried on every row — the likely cause of multi-hour
  `Database Migration` runs (backfill/align fetch live JavDB detail pages through
  the proxy pool).
- A CF→ban path exists (`cf_bypass_ban_threshold = 6` consecutive CF-bypass
  failures → `ProxyBannedError`, `javdb/infra/request.py:1416`), but its counter
  `cf_bypass_failure_count` is **global across proxies and resets on any
  success**, so in a large pool with intermittent success it rarely fires — a
  genuinely walled proxy may never get banned.

### Failure mode 2 — explicit JavDB IP ban (hard, ~week-long)

JavDB serves a text-only ban page ("banned your access" / "管理員禁止了你的訪問",
`html/ban.html`) when it bans an IP. This is detected by `is_ban_page(...)` →
`ProxyBannedError` (`javdb/infra/request.py:856`, `:1037`). Operationally this
ban lasts on the order of a **week** (~7 days, JavDB-side policy).

### Why nothing is shared — BFR-009

Both ban paths funnel into `proxy_pool.ban_proxy(...)` /
`get_ban_manager().add_ban(...)`, which in production are the **Rust**
pool/ban-manager. Per [BFR-009](../BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/BFR-009-rust-pool-ban-dispatch.md):
the remote-ban hook `set_remote_ban_hook(client.mark_proxy_banned)` **is
registered** (`context.py:880`, `state.py:722`), but `_dispatch_remote_ban` is
only called by the *Python* pool/ban-manager. The Rust `ban_proxy` / `add_ban`
(inside the PyO3 extension) **cannot reach** the Python hook, so
`mark_proxy_banned()` → Worker DO broadcast **never fires in production**. Every
production ban — CF-threshold *and* hard JavDB ban — is recorded **locally
only**. This work fixes BFR-009 head-on (see D8).

### What already works (and what's missing)

- **Consumption is fine.** On every `lease`, the client reads `banned` /
  `banned_until` from the Worker, and the runner mirrors a remote ban into local
  state via `_mirror_remote_ban_locally(...)` (`javdb/spider/runtime/sleep.py:800`)
  so the next selection skips it. The broken link is the **publish** side.
- **Per-proxy CF events already reach the Worker** via
  `report_async(proxy_id, "cf")` (`context.py:984`, `state.py:2016`); the DO
  accumulates them in `CoordinatorState.cfEvents[]` (pruned to `penaltyWindowSec`,
  default 300 s) — a **cross-runner aggregated** signal. But the Worker **never
  escalates `cfEvents` into a ban**; `bannedUntil` is set only by an explicit
  `kind="ban"` report.
- **TTL mismatch.** Even with BFR-009 fixed, the DO default ban TTL is
  `DEFAULT_BAN_TTL_MS = 3 days` — *shorter* than JavDB's ~7-day hard ban. A proxy
  un-bans in the DO after 3 days and is re-selected while still JavDB-banned for
  several more days, re-hitting the ban page.

## Decision

Make the coordinator the cross-runner source of truth for proxy bans, with the
ban TTL matched to the **real-world recovery window** of the underlying cause:

1. **CF-wall persistent → Worker-side short auto-ban (6 h).** The DO escalates
   its aggregated `cfEvents` (with a zero-success guard) into a short
   `bannedUntil`. Independent of BFR-009. → IMP-ADR043-01.
2. **All production bans → actually published to the DO**, by fixing BFR-009 with
   a Rust→Python ban-dispatch callback. The hard JavDB ban uses an **8-day** DO
   TTL; the dispatch carries the ban *cause* so the DO maps cause→TTL. →
   IMP-ADR043-02.
3. **Keep the runner-local ban** as the within-run / fail-open tier of a
   two-tier model.

### Design Decisions

**D1. CF trigger point — `handleReport`, `kind="cf"` branch.** After the CF event
is pushed into `cfEvents`, evaluate the escalation rule. Event-driven, per-proxy
by construction (one DO per proxy).

**D2. CF escalation rule — sustained CF with zero success.** Within
`penaltyWindowSec` (default 300 s):

```
cfEvents.length     >= CF_AUTO_BAN_THRESHOLD   (default 6)
AND successEvents.length === 0
=> state.bannedUntil = max(state.bannedUntil ?? 0, now + CF_BAN_TTL_MS)
```

The **zero-success guard** is the precision core: a proxy that occasionally fails
(failures interleaved with successes) never trips; only a proxy that clears
**nothing** in the whole window is banned. This also absorbs brief site-wide
wobble that recovers quickly. The threshold of 6 sits above the top throttle
penalty tier (4 events ⇒ ×2.0) and anchors to the Python `cf_bypass_ban_threshold = 6`.

**D3. CF short TTL.** `CF_BAN_TTL_MS` defaults to **6 hours** (`21_600_000`). CF
IP-reputation blocks clear within minutes to hours, and a banned proxy emits no
further success reports, so it can **only** recover by TTL expiry — hence short.

**D4. Monotonic-max with existing bans.** Reuse the existing monotonic-max policy
(`proxy_coordinator.ts:355`): a longer ban (e.g. an 8-day hard ban) is never
shortened by a 6-hour CF ban, and vice-versa.

**D5. Tag the ban source for observability.** Record the ban *cause* (`cf_auto`,
`javdb_hardban`, `manual`) — surfaced via the DO's analytics / event-log (and/or a
lightweight `bannedReason` marker) so dashboards and `/do/state` can tell them
apart. Exact mechanism is an IMP detail.

**D6. CF auto-ban default ON, with kill-switch.** `CF_AUTO_BAN_ENABLED` defaults
to **true**; ops can disable instantly via `wrangler.toml [vars]` without a code
change. New env vars follow the existing `loadXxx(env)` pattern in `types.ts`:

| Env Var | Default | Meaning |
| --- | --- | --- |
| `CF_AUTO_BAN_ENABLED` | `true` | Master switch / kill-switch for CF escalation |
| `CF_AUTO_BAN_THRESHOLD` | `6` | CF events in the penalty window before banning |
| `CF_BAN_TTL_MS` | `21600000` (6 h) | CF short-ban duration |

The CF window is **reused** from `penaltyWindowSec` (300 s) — no new constant.

**D7. Out of scope — site-wide CF outage.** When *every* proxy fails CF (global CF
tightening or bypass-service outage), banning individual proxies is the wrong
remedy. This ADR deliberately does **not** add cross-DO / global circuit-breaking.
Cheap insurance retained: the zero-success guard, plus the 6-hour CF TTL so even a
wrongly mass-banned pool self-heals within 6 hours. A true global stop-loss is
separate future work.

**D8. Fix BFR-009 via a Rust→Python ban-dispatch callback (Approach 1).** Add a
`set_ban_dispatch(callback)` to the Rust pool/ban-manager, invoked on each *newly
recorded* ban (mirroring the Python `newly_banned` dedup), wired from the same
runtime setup that registers `set_remote_ban_hook`. Approach 1 (vs the lighter
Python-entry-point wrapper, BFR-009 Approach 2) is chosen because the Rust pool
also records bans **internally** on auto-drain / proxy-switch
(`rust_core/src/proxy/pool.rs:485`, `:692`) that never pass through a Python entry
point; only a Rust→Python callback guarantees **every** ban is published. This
closes BFR-009 for **all** ban causes, not just the JavDB hard ban.

**D9. Hard-ban DO TTL = 8 days, dispatch carries the ban cause.** The
ban-dispatch callback forwards the ban *cause* so the DO selects the TTL by cause
rather than always using the 3-day default. The JavDB hard ban (`is_ban_page`)
uses `HARD_BAN_TTL_MS`, default **8 days** (one day of margin over JavDB's
observed ~7-day window, to avoid un-banning a beat early and re-hitting the ban
page). The default is documented as an **operator-observed external value**
(JavDB-side policy), env-tunable — not a hardcoded site constant we control. The
CF-threshold `ProxyBannedError` ("CF bypass failed N consecutive times") maps to
the CF cause → 6 h, consistent with D2's Worker-side escalation.

| Ban cause | DO TTL | Env Var | Rationale |
| --- | --- | --- | --- |
| CF-wall persistent (transient) | 6 h | `CF_BAN_TTL_MS` | IP reputation recovers fast |
| **JavDB explicit IP ban (hard)** | **8 days** | `HARD_BAN_TTL_MS` | matches JavDB's ~7-day window + margin |
| Manual / operator / other | 3 days | `BAN_TTL_MS` (existing default) | unchanged |

**D10. Keep the runner-local ban — two-tier model.** The local ban is *not*
redundant once D8 lands; it covers what the DO cannot:

- **Same-run immediacy** — this runner stops using the proxy synchronously on
  detection; the DO ban only takes effect on a peer's *next* lease.
- **Fail-open** — when the coordinator is degraded/unavailable (`sleep.py:813`
  circuit breaker), local bans are the only enforcement.
- **Coordinator-less runs** — local-only deployments rely on it entirely.
- **It is the mirror substrate** — `_mirror_remote_ban_locally()` writes remote
  bans *into* the local ban manager, which is what selection reads
  (`proxy.banned`).

Within-run permanence is **intentional**: the local mirror bans for the session
(`_SESSION_BAN_COOLDOWN = 86400 * 365`), so a 6-hour DO ban keeps a proven-bad
proxy out for the rest of the *current* run too (with D2's zero-success guard, the
proxy genuinely cleared nothing). The short DO TTL governs **cross-run /
cross-runner** recovery, not same-run revival. Local (within-run + fail-open) and
DO (cross-runner, real-world TTL) are complementary, not duplicative — no change
to the local ban duration is needed.

## Consequences

### Positive

- Persistently-CF-walled proxies are removed from rotation **across all runners**
  for a short window, ending the "every row retries the same dead proxy" grind
  behind multi-hour migrations.
- CF detection uses the **cross-runner aggregated** signal (the Worker sees all
  runners) — far stronger than the brittle single-runner global counter.
- **BFR-009 is closed for all ban causes** (D8): every production ban — CF and
  hard — is finally broadcast cross-runner, including Rust-internal switch/drain
  bans.
- Hard JavDB bans share at a TTL that **matches reality** (8 days), so peers don't
  prematurely un-ban and re-hit the ban page.
- IMP-01 (CF auto-ban) is Worker-only and **ships independently** of the
  Rust-touching BFR-009 fix (IMP-02).
- Not part of the ADR-017 dual-backend overlap surface (coordinator-internal; no
  D1 / auth / API-shape change) — the web repo's TS backend needs no sync.

### Negative

- **IMP-02 touches the Rust crate (PyO3)** — `set_ban_dispatch` callback plumbing
  + threading the ban *cause* through the dispatch (hook signature grows beyond
  `Callable[[str], None]`). Larger blast radius than a Python-only change.
- This ADR **couples a bug fix (BFR-009) into a feature ADR** — intentional, at
  the user's request, since they share one root cause; tracked as a distinct IMP
  with its own verification gates.
- Once dispatch fires, a misbehaving runner can broadcast a ban cross-runner;
  bounded by monotonic-max, conservative per-cause TTLs, and the zero-success
  guard on the CF path.
- CF auto-ban is **default-on**; a mis-tuned threshold could ban healthy proxies
  for 6 h (mitigated by the zero-success guard, threshold above max penalty tier,
  short TTL, and the kill-switch).
- A genuine **site-wide** CF outage auto-bans the whole pool for up to 6 h (D7 —
  out of scope; self-heals in 6 h).

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR043-01](IMP-ADR043-01-cf-auto-ban-worker.md) | Worker CF escalation in `handleReport`, 3 env-var loaders in `types.ts`, `wrangler.toml [vars]`, vitest coverage, ban-source observability, handbook `proxy-coordinator.md` (en/zh) env docs | — |
| Phase 2 | [IMP-ADR043-02](IMP-ADR043-02-bfr009-ban-dispatch-and-hardban-ttl.md) | BFR-009 fix (Rust `set_ban_dispatch` callback → Python hook, covering Rust-internal ban sites), ban-cause threading, `HARD_BAN_TTL_MS` (8 d) DO mapping, production-entry-point dispatch test, BFR-009 status → Fixed | Cross-DO global CF-outage circuit-breaker (D7); `set_remote_unban_hook` surface re-eval |

## References

- [BFR-009 — Rust Pool Cross-Runner Ban Dispatch](../BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/BFR-009-rust-pool-ban-dispatch.md) — the dispatch gap this ADR closes (D8); fixed by IMP-ADR043-02
- [ADR-041 — Rust Fallback Policy](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) — made BFR-009 visible (Rust-Required)
- [ADR-023 — Proxy Recommendation Policy](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md) — proxy health signals (CF/success/failure events) in the DO
- Coordinator repo: [`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator) — `src/proxy_coordinator.ts`, `src/types.ts`
- `docs/handbook/en/self-hoster/proxy-coordinator.md` — operator-facing env reference

## Status Log

- 2026-05-31: Proposed
- 2026-06-01: Phase 2 implemented; BFR-009 closed.
