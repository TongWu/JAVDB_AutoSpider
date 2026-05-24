# ADR-013: Runner Runtime State Consolidation

**Status**: Accepted - implementation pending
**Date**: 2026-05-20
**Deciders**: Runner runtime state brainstorming and grill session
**Related Implementation Plans**: [IMP-ADR013-01](IMP-ADR013-01-runner-runtime-phase1-skeleton-facade.md) (Phase 1 - runtime skeleton and active facade), [IMP-ADR013-02](IMP-ADR013-02-runner-runtime-phase2-registry-movieclaim.md) (Phase 2 - registry and MovieClaim lifecycle), [IMP-ADR013-03](IMP-ADR013-03-runner-runtime-phase3-explicit-callers.md) (Phase 3 - explicit production callers), [IMP-ADR013-04](IMP-ADR013-04-runner-runtime-phase4-legacy-facade-removal.md) (Phase 4 - legacy facade freeze/removal)

## Outstanding Work

- Phase 1 - introduce `SpiderRuntime`, small runtime state objects, runtime service slots, and active-runtime facade binding while preserving old `state.*` callers.
- Phase 2 - move RunnerRegistry heartbeat/session lifecycle and MovieClaim auto lifecycle under runtime-owned state/services.
- Phase 3 - migrate sleep, proxy, request, login, fetch, detail, and report production callers toward explicit runtime/context access.
- Phase 4 - freeze or remove legacy direct-mutation `state.py` compatibility and document the final explicit-runtime usage rule.

---

## Context

`javdb.spider.runtime.state` is currently the spider runner's mutable global
state container. It owns or coordinates:

- `parsed_links` and other detail-run state;
- proxy pool, CF bypass sticky markers, proxy-ban HTML captures, and coordinator
  hooks;
- refreshed login cookies, login attempt counters, and login budget;
- `runtime_holder_id`, RunnerRegistry heartbeat thread, session payload,
  unregister state, config snapshots, and active signals;
- MovieClaim auto mount/unmount state and orphan-stage sweep state;
- request handler lifecycle;
- compatibility access for callers importing `state.<name>` directly.

`runtime.sleep` also exposes module-level singleton state
(`penalty_tracker`, `triple_window_throttle`, `movie_sleep_mgr`) that belongs to
the runner runtime lifecycle.

This shape worked while every Spider run happened in a fresh process. It is
fragile for the in-process Spider runner direction because the same Python
process must be able to create multiple Spider runtimes sequentially without
reusing run-scoped state.

## Non-Negotiable Runtime Invariant

This ADR is behavior-preserving.

The migration must not change production proxy, login, MovieClaim,
RunnerRegistry, WorkDistributor, sleep/throttle, GitHub Actions logging, stdout
footer, session heartbeat, D1 fail-closed, or failure downgrade semantics.

## Decision

### D1. Introduce `SpiderRuntime`

`SpiderRunService` becomes the composition root for Spider runtime state.

At run start it creates a `SpiderRuntime`, binds it as the active runtime for
legacy callers, runs the existing Spider flow, then closes and unbinds the
runtime. `SpiderRuntime.close()` is explicit and idempotent.

### D2. Use Small Runtime State Objects

`SpiderRuntime` owns focused state objects:

- `DetailRunState`
- `ProxyRunState`
- `LoginRunState`
- `RunnerRegistryState`
- `MovieClaimRuntimeState`
- `SleepRuntimeState`

These are not a single large "context bag". The aggregate exists only so run
creation, close, and compatibility binding have one owner.

### D3. Keep Resource Services Separate From Plain State

Resource objects live in runtime-owned services, not in plain state dataclasses:

- proxy pool;
- request handler;
- proxy coordinator client;
- login-state client;
- MovieClaim client;
- runner-registry client;
- recommend proxy policy;
- work distributor client.

The owning runtime is responsible for setup, shutdown, and close semantics.

### D4. Preserve `state.py` As A Temporary Active-Runtime Facade

`javdb.spider.runtime.state` remains available during migration.

Existing reads and mutations such as `state.parsed_links.clear()`,
`state.global_proxy_pool`, and `state.runtime_holder_id` continue to work until
the final phase explicitly freezes or removes that compatibility surface.

The facade must not create two sources of truth. A migrated field either points
at the active runtime's object, or it remains an unmigrated module field until
production writers have been moved.

### D5. Do Not Rely On Module Assignment Magic In Phase 1

Phase 1 does not depend on intercepting arbitrary `state.foo = value` module
assignment.

Mutable object fields can be rebound to the active runtime during
`bind_active_runtime()`. Scalar fields with active direct-assignment writers
remain module fields until their writers are migrated or an explicit proxy
mechanism is intentionally introduced in Phase 3.

### D6. Move Runner Identity To Runtime Ownership

`runtime_holder_id` becomes `RunnerRegistryState.holder_id`, generated when a
`SpiderRuntime` is created. Existing CLI/GitHub Actions behavior remains the
same because current production uses one Spider invocation per process.

Allowing Pipeline/API callers to supply holder ids is follow-up ADR scope.

### D7. Guarantee Sequential Runtime Isolation, Not Concurrency

This ADR guarantees that the same Python process can create multiple
`SpiderRuntime` instances sequentially without reusing run-scoped state.

It does not support concurrent Spider runtimes in the same process. Concurrent
runtimes require removing active-runtime facade dependencies or using a
concurrency-aware binding strategy, and belong to a follow-up ADR.

### D8. Runtime Owns Heartbeat And `atexit` Is A Fallback

`RunnerRegistryState` owns the heartbeat thread, stop event, unregister flag,
session payload, heartbeat cadence, last applied config version, and active
signal bookkeeping.

`SpiderRuntime.close()` stops heartbeat and unregisters explicitly. `atexit`
remains as a best-effort safety net for abnormal shutdown, not the normal
lifecycle path.

### D9. Model MovieClaim Separately

MovieClaim auto state is modeled as `MovieClaimRuntimeState`, even though
RunnerRegistry heartbeat responses drive its recommendation updates.

This keeps the MovieClaim state machine distinct from registry transport and
prevents the registry state from becoming another global bucket.

### D10. Runtime-Owned Sleep State

`penalty_tracker`, `triple_window_throttle`, `dual_window_throttle`, and
`movie_sleep_mgr` are runner runtime state.

During migration, `runtime.sleep` may keep compatibility names, but production
callers must converge on explicit runtime-owned sleep state.

### D11. Preserve Runtime Behavior Invariants

The following behavior must remain unchanged after each phase:

- GitHub Actions live logs and stdout footer markers.
- `SPIDER_OUTPUT_CSV`, `SPIDER_DEDUP_CSV`, `SPIDER_SESSION_ID`, and
  `SPIDER_STAT_*`.
- Frontend/API task log streaming.
- Proxy pool setup, proxy coordinator setup, recommend proxy policy setup, and
  fail-open behavior.
- CF bypass sticky behavior and remote CF bypass mirroring.
- Login budget, login-state coordinator, and parallel login routing.
- RunnerRegistry register, heartbeat, re-register, unregister, pause handling,
  config snapshot application, and active signal reconciliation.
- MovieClaim auto mount/unmount, D1 fail-closed behavior, failure cooldown, and
  orphan-stage sweep.
- WorkDistributor enablement, enqueue/pull/complete/release, and fail-open
  behavior.
- `runtime.sleep` throttling, volume multiplier, global throttle signal,
  pause-all signal, and degraded-mode runner scaling.

### D12. One ADR, Four Phase Plans

This ADR rolls out through four independent implementation plans:

- [IMP-ADR013-01](IMP-ADR013-01-runner-runtime-phase1-skeleton-facade.md)
- [IMP-ADR013-02](IMP-ADR013-02-runner-runtime-phase2-registry-movieclaim.md)
- [IMP-ADR013-03](IMP-ADR013-03-runner-runtime-phase3-explicit-callers.md)
- [IMP-ADR013-04](IMP-ADR013-04-runner-runtime-phase4-legacy-facade-removal.md)

Each phase has its own test gate and can bake separately.

## Future ADR Scope

The following work is explicitly deferred to the next ADR:

- API task runner and FastAPI runtime global consolidation.
- Caller-supplied runner holder ids for Pipeline/API integrations.
- Same-process concurrent Spider runtime support.
- Full dependency injection across all internal Spider modules.
- Broader workflow/API runtime lifecycle management.

## Consequences

### Positive

- Spider runtime state has an explicit owner.
- Sequential in-process Spider runs can be isolated.
- Future Pipeline in-process Spider work has a concrete runtime boundary.
- Tests can create and close runtime instances without relying on import-time
  module globals.
- The migration converges toward explicit context/services instead of permanent
  compatibility wrappers.

### Negative

- The migration requires several phases because production code and tests
  currently rely on direct `state.*` access.
- The compatibility facade adds short-term complexity.
- Scalar direct assignment cannot be safely migrated until callers are moved or
  a deliberate module proxy strategy is introduced.

### Neutral

- This ADR does not reduce the amount of runtime behavior. It changes ownership
  and lifecycle boundaries first.
