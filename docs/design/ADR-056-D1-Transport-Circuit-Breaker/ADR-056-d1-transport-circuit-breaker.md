# ADR-056: D1 transport circuit breaker for transient-outage resilience

**Status:** Accepted
**Date:** 2026-06-15
**Author:** Ted
**Related Implementation Plans:** [IMP-ADR056-01](IMP-ADR056-01-d1-transport-circuit-breaker.md) (Phase 1 — breaker + bounded wait + tests)
**D1 Write Class:** n/a  <!-- transport resilience only; introduces no new D1 writes -->

## Context

A single transient Cloudflare D1 outage takes down an entire pipeline run with no
graceful handling. On 2026-06-15 ([run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092),
see [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.md))
a batched history stage-write flush hit `HTTP 500 (code 7500, "internal error")`.
The D1 port retried 5× with exponential backoff (`D1_MAX_RETRIES=5`, base 1s, cap
30s ≈ ~31s total), but D1 returned 500 for the whole window, so `D1TransientError`
propagated and crashed the spider (exit 1). The whole Daily Ingestion run failed
and the session was rolled back.

Today's behaviour has three weaknesses:

1. **No coordination across workers.** D1 connections are thread-local
   ([_db_connection.py](../../../javdb/storage/db/_db_connection.py) `threading.local()`),
   so each of the ~8 spider workers holds its own `D1AccessPort` with its own
   retry state. During an outage all of them independently burn retry budget
   against an already-struggling D1, then fail one by one.
2. **Retry window too short for a real brownout.** ~31s of per-statement retry
   cannot ride out a multi-minute Cloudflare incident.
3. **No headroom is used.** The Daily Ingestion jobs set **no `timeout-minutes`**
   (GitHub Actions default = 6h) and a normal run takes ~6 min, so there is ample
   wall-clock budget to *wait out* a transient brownout — but nothing does.

We want a transient infrastructure blip to **not** take down the whole pipeline,
while keeping behaviour predictable and reusing the existing fail-fast + rollback
path as the terminal outcome.

## Decision

Add a **process-global circuit breaker at the D1 transport layer**. Every D1 HTTP
POST consults a single shared breaker. On a sustained outage the breaker trips,
**pauses all D1 access** (any thread issuing a POST blocks), a single prober
health-polls D1 with `SELECT 1`, and on recovery the breaker closes and all
waiters resume. If D1 does not recover within a bounded window, the breaker
raises a terminal error that flows through the existing crash → rollback path.

This is **transport hardening, not graceful degradation**: a run never "succeeds
with deferred writes". It either completes normally (possibly after a pause) or
fails fast and rolls back — same terminal semantics as today, just resilient to
transient brownouts.

### Design Decisions

D1. **Per-D1-database breaker in the transport layer** — A registry of
`D1CircuitBreaker` instances (`threading.Lock` + `threading.Condition`) keyed by
**D1 endpoint URL** lives in `javdb/storage/` and is consulted by
`D1AccessPort._post`. It is **not** per-port state (connections are thread-local,
so all ports for the same database across threads share one breaker), and **not**
a single global object spanning all three D1 databases (history / reports /
operations): they fail independently, so a `history` outage must not be declared
recovered by a healthy `reports` probe. Keying by endpoint guarantees the elected
prober always health-checks the database that actually tripped. It covers **every**
D1 caller (spider, pipeline, qb, rollback CLIs) uniformly with no per-CLI work.

D2. **Consecutive-failure trip** — A shared counter increments on each transient
5xx (any thread) and resets on any success. Reaching `D1_BREAKER_TRIP_THRESHOLD`
(default 3) trips `CLOSED → OPEN`. This trips quickly when D1 is broadly down,
pausing the fleet instead of letting each statement exhaust its own retries. A
strict consecutive count (not a time window) keeps the logic simple and
lock-friendly; it is a heuristic, tuned by the threshold.

D3. **Inline prober election** — The first thread to observe `OPEN` becomes the
prober: it sleeps `D1_BREAKER_PROBE_INTERVAL_SEC` (default 5s) and issues
`SELECT 1`. No background daemon thread is spawned — all workers are paused
anyway, so one of them probing while the rest wait on the `Condition` is the
natural, thread-minimal design. After `D1_BREAKER_HALF_OPEN_SUCCESSES` (default 1)
successful probes the breaker goes `HALF_OPEN → CLOSED` and `notify_all()` wakes
every waiter.

D4. **Bounded OPEN → terminal fail-fast + rollback (recoverable for daemons)** —
While `OPEN`, waiters block up to `D1_BREAKER_MAX_OPEN_SEC` (default 900 = 15 min)
measured from `opened_at`. If D1 has not recovered by the deadline the breaker
enters `TERMINAL`, wakes all waiters, and they (and the prober) raise
`D1CircuitOpenError`. This propagates to crash the run, and the existing
`cleanup-on-failure` job rolls the session back — predictable, reuses proven
machinery, and never holds a runner for the full 6h. **`TERMINAL` is not a
permanent sink:** a one-shot run (spider / CLI / Actions) crashes on the raise and
exits, so it never re-arms; but a long-lived process (e.g. the `apps/api` backend
on a D1 backend) survives the raise, and its cached process-global breaker would
otherwise reject that database forever. To avoid that self-lock, a `TERMINAL`
breaker re-arms to `CLOSED` on the next `acquire()` once another
`D1_BREAKER_MAX_OPEN_SEC` cooldown has elapsed from `terminal_at`, giving a
recovered D1 a fresh attempt (it re-trips and re-probes from scratch if still
down). The cooldown never elapses within a crashing run, so one-shot fail-fast is
unchanged.

D5. **Terminal error bypasses the recovery outbox (does not worsen [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.md))**
— `D1CircuitOpenError` subclasses `D1Error` but is **not** a `D1TransientError`,
so `D1AccessPort.flush()`'s `except D1TransientError` recovery-queue handler does
not capture it. The terminal failure therefore does not durably queue writes for
later replay, so it cannot create a recovery-outbox-replay-after-rollback orphan.
BFR-020 remains a separate fix, but the breaker also sharply reduces how often the
recovery-outbox path is hit at all.

D6. **Inner per-statement retry retained as a thin layer** — `_post_with_retry`
keeps the existing exponential backoff (`D1_MAX_RETRIES=5`, `Retry-After`
honoured) so momentary 1–2-failure blips are absorbed without tripping the
breaker. The only tweak is a slightly longer backoff cap for `code 7500` internal
errors. The breaker is the primary safety net; the retry loop is the first line.

D7. **Enabled by default** — `D1_CIRCUIT_BREAKER_ENABLED` defaults to `true` in
code. The breaker is inert unless the D1 transport path actually returns 5xx, and
under non-D1 backends (`STORAGE_BACKEND=sqlite`) there are no D1 POSTs, so it is a
no-op there. Tests that need it off set the env var explicitly.

D8. **Probe bypass + body-consistent success check** — The prober's `SELECT 1` is
issued via a dedicated `_probe_d1` bypass that skips `breaker.acquire()`, so the
health check cannot deadlock on the open breaker. The probe applies the **same**
success criterion as `_post` — HTTP 200 **and** a JSON body with `success == true`
— so an application-level D1 fault (HTTP 200 + `success=false`) does not falsely
close the breaker and wake the fleet into renewed failures.

## Consequences

### Positive

- A transient D1 brownout up to ~15 min no longer fails the run; the pipeline
  pauses and resumes automatically.
- Coordinated pause: one prober instead of ~8 workers hammering a struggling D1.
- Universal — every D1 caller (spider + all CLIs) gets the resilience for free,
  no per-call-site changes.
- Terminal outcome is unchanged (fail-fast + rollback), so monitoring/rollback
  SOPs stay valid; only the timing shifts (fails after a genuine sustained outage,
  not a momentary blip).
- Does not deepen the BFR-020 orphan hazard (D5).

### Negative

- Workers block *inside* the D1 call during a pause; there is no spider-level
  "paused N workers, resuming at HH:MM" UX unless the breaker logs it (it does).
- A long pause (minutes) may let proxy login state / `JAVDB_SESSION_COOKIE` and
  MovieClaim / WorkDistributor leases go stale; the spider must tolerate resume.
  **Tracked as an implementation-time verification gate** (IMP risk R1/R2), not a
  mandatory resume-revalidation feature in this ADR.
- New module-global mutable state + `Condition` — needs careful thread-safety and
  test coverage.
- Single-threaded CLIs (e.g. rollback during cleanup) could block up to
  `MAX_OPEN_SEC`; acceptable, and tunable per-context via the env var.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 ✅ (delivered 2026-06-19) | [IMP-ADR056-01](IMP-ADR056-01-d1-transport-circuit-breaker.md) | `D1CircuitBreaker` per-DB registry + state machine, `_post` integration, `D1CircuitOpenError`, env knobs read at construction (default on), inner-retry tweak for 7500, observability (logs + aggregated `d1_port_summary` metrics), unit + port-level + concurrency tests, `vars` wiring across **all** D1 workflows | BFR-020 orphan fix (separate); optional spider-side explicit worker-pool pause UX (Approach B); resume-time login/lease revalidation (only if R1/R2 verification shows it is needed) |

## References

- [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.md) — the failure that motivated this; orphan hazard kept separate (D5)
- [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md) — the D1 Access Port that owns transport, retry/backoff, and the recovery facade this breaker wraps
- [run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092) — the triggering transient `HTTP 500 (code 7500)`

## Status Log

- 2026-06-15: Proposed
- 2026-06-19: **Accepted.** Phase 1 ([IMP-ADR056-01](IMP-ADR056-01-d1-transport-circuit-breaker.md)) delivered — breaker registry + state machine, `_post_with_retry`/`_probe_d1` integration, `D1CircuitOpenError`, construction-time env knobs (default on), 7500 backoff floor, aggregated `circuit_breaker` metrics in `d1_port_summary.json`, 17 passing breaker tests, and `D1_CIRCUIT_BREAKER_ENABLED` wired across all 12 D1 workflows (recovery jobs capped at 120s).
  - **R1 (login/session resume) — verified safe:** `JAVDB_SESSION_COOKIE` is a static credential replayed per request (`javdb/infra/request.py` sets the `_jdb_session` Cookie header on every fetch) and login is validated lazily via page fetches, so a ≤15-min pause cannot outlive client-side login state; server-side session TTLs far exceed the max-open window. No resume-revalidation feature needed.
  - **R2 (MovieClaim / WorkDistributor lease) — verified safe:** the coordinator clients are best-effort fail-open (`javdb/spider/detail/runner.py` catches `MovieClaimUnavailable` → per-process dedup fallback; login-state/session paths also fail open). Default MovieClaim TTL is 30 min (> the 15-min max-open), so default claims survive a max pause; a lease expiring mid-pause at worst causes a history-deduped duplicate fetch. No lease-renewal feature needed. The roadmap's deferred items (BFR-020 orphan fix, Approach-B pause UX) stand.
- 2026-06-19: PR-review follow-up (PR #240, Codex P2) — made `TERMINAL` recoverable so a long-lived process (`apps/api` on D1) no longer self-locks after one >15-min outage: a `TERMINAL` breaker re-arms to `CLOSED` after one more `D1_BREAKER_MAX_OPEN_SEC` cooldown (one-shot fail-fast unchanged — see amended D4). Added `rearms` metric + deterministic re-arm test.
