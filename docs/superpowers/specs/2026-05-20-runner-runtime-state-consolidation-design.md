# Runner Runtime State Consolidation Design

Date: 2026-05-20

## Context

`javdb.spider.runtime.state` is the spider runner's mutable global state
container. It currently owns or coordinates all of these concerns:

- run-scoped detail state such as `parsed_links`;
- proxy state such as proxy pool, CF bypass sticky markers, ban HTML captures,
  and coordinator hooks;
- login state such as refreshed cookies, per-proxy login attempts, and global
  login budget;
- runner-registry state such as `runtime_holder_id`, session payload,
  heartbeat thread, unregister state, config snapshots, and active signals;
- MovieClaim auto mount/unmount state and orphan-stage sweep state;
- request-handler lifecycle;
- sleep/throttle singleton state through `runtime.sleep`;
- compatibility access for callers that import `state.<name>` directly.

This makes `state.py` both a data bag and a lifecycle coordinator. Production
callers in index fetch, detail processing, login coordination, summary
reporting, request fallback, and tests read and mutate this module directly.
That shape is fragile for the upcoming in-process Spider runner work because a
single Python process must be able to create more than one spider runtime in
sequence.

## Goals

- Consolidate Spider runner runtime state around explicit runtime-owned
  objects.
- Keep current production behavior unchanged during the migration.
- Preserve `state.py` as a compatibility facade until callers are migrated.
- Make the final architecture converge to explicit context/services instead of
  permanent module-level globals.
- Support sequential creation of multiple `SpiderRuntime` instances in one
  Python process without reusing run-scoped state.
- Give every rollout phase its own implementation plan.

## Non-Goals

- Do not change GitHub Actions log streaming, stdout footer markers, or session
  lifecycle heartbeat semantics.
- Do not change proxy, CF bypass, login, MovieClaim, WorkDistributor,
  RunnerRegistry, sleep, or failure downgrade behavior.
- Do not support concurrent spider runtimes in one Python process in this ADR.
- Do not let Pipeline/API callers supply a custom runner holder id in this ADR.
- Do not consolidate FastAPI auth globals, API task runner process state, or
  broader application runtime globals in this ADR.
- Do not remove `state.py` compatibility in the first phase.

## Selected Approach

Use staged convergence.

`SpiderRunService` becomes the composition root for Spider runtime state. Each
run creates a `SpiderRuntime` aggregate, binds it as the active runtime for
legacy callers, runs the existing spider flow, then closes and unbinds it.

`SpiderRuntime` owns small state dataclasses and resource services. `state.py`
remains the legacy facade while callers migrate. New implementation code should
prefer explicit runtime/context access and must not add new module-level mutable
state to `state.py`.

The broader runtime-global cleanup is necessary, but it belongs to a follow-up
ADR. That follow-up covers API task runner state, FastAPI auth/runtime
globals, caller-supplied holder ids, and same-process concurrent spider
runtimes.

## Architecture

The target architecture has three layers.

`SpiderRunService`
: Composition root. Creates `SpiderRuntime`, binds it for compatibility, invokes
  the spider run, and closes it.

`SpiderRuntime`
: Owns runtime state and services for one spider run. It is the unit that can be
  created, closed, and tested for isolation.

`javdb.spider.runtime.state`
: Legacy facade during migration. Existing imports such as
  `import javdb.spider.runtime.state as state` continue to work. The facade
  forwards to the active `SpiderRuntime` or preserves existing module fields
  until that specific field is migrated. It must never create a silent second
  source of truth.

The final internal dependency shape converges to explicit context/services:
fetch, detail, session, report, and runtime helpers receive or access runtime
objects directly instead of using the legacy facade.

## Runtime State Boundaries

`DetailRunState`
: Owns detail-stage run data such as `parsed_links`.

`ProxyRunState`
: Owns proxy-related run markers such as `proxy_ban_html_files`,
  `always_bypass_time`, `proxies_requiring_cf_bypass`, the CF bypass lock, and
  active signal ban bookkeeping.

`LoginRunState`
: Owns login lifecycle and budget data: `login_attempted`,
  `refreshed_session_cookie`, `logged_in_proxy_name`,
  `current_login_state_version`, `login_attempts_per_proxy`,
  `login_failures_per_proxy`, `login_total_attempts`, `login_total_budget`,
  `_login_budget_deducted_proxies`, and the login budget lock. The budget is
  keyed by proxy names but its source of truth belongs to login state.

`RunnerRegistryState`
: Owns runner identity and registry lifecycle: `holder_id`, active session
  payload, heartbeat thread, stop event, unregister flag, heartbeat cadence
  values, last applied config version, and active signal reconciliation state.

`MovieClaimRuntimeState`
: Owns MovieClaim auto-toggle state: public and pending client references, mode,
  intended mode, last recommendation, lock, exit sweep flag, and sweep cutoff.
  It is modeled separately even though registry heartbeats drive the
  recommendation updates.

`SleepRuntimeState`
: Owns runtime sleep/throttle instances: `PenaltyTracker`,
  `TripleWindowThrottle`, `dual_window_throttle`, and `MovieSleepManager`.

Resource services are not stored as plain state fields. Proxy pool, request
handler, coordinator clients, recommend proxy policy, and work distributor
client are runtime-owned services with explicit setup and close semantics.

## Compatibility Facade

During migration, `state.py` must keep current public spellings available:

- `state.parsed_links`
- `state.proxy_ban_html_files`
- `state.global_proxy_pool`
- `state.global_request_handler`
- `state.global_proxy_coordinator`
- `state.global_login_state_client`
- `state.global_movie_claim_client`
- `state.global_runner_registry_client`
- `state.global_recommend_proxy_policy`
- `state.global_work_distributor_client`
- `state.runtime_holder_id`
- login counters and CF bypass fields
- setup and helper functions such as `setup_proxy_pool`,
  `initialize_request_handler`, `get_page`, `set_active_runner_session`,
  `proxy_needs_cf_bypass`, and `mark_proxy_cf_bypass`

The facade must support existing direct reads and direct mutations during the
transition. For mutable objects such as sets and dicts, the facade may expose
the active runtime's object. For assignment-style compatibility such as
`state.always_bypass_time = value`, an implementation phase must either keep
that field unmigrated until its production writers are changed, or install an
explicit module facade/proxy that forwards assignment to the active runtime.
The migration must not split one logical field between a module variable and a
runtime field.

After each migration phase, old `state.*` callers remain valid until the final
phase explicitly removes or freezes that surface.

## Lifecycle

`SpiderRunService.run()` creates a `SpiderRuntime` at run start. The runtime
generates a fresh `RunnerRegistryState.holder_id` for that run. The existing
CLI and GitHub Actions behavior remains unchanged because production currently
runs one spider invocation per process.

`SpiderRuntime.close()` is explicit and idempotent. It stops the registry
heartbeat, unregisters the runner, closes clients/policies that own resources,
and clears active runtime binding. `atexit` remains as a best-effort fallback
for abnormal shutdown, but normal lifecycle ownership moves to
`SpiderRuntime.close()`.

This ADR guarantees same-process sequential isolation. It does not guarantee
same-process concurrent runtimes. Concurrent runtimes require removing active
runtime facade dependencies or using a concurrency-aware binding strategy, and
that belongs to a follow-up ADR.

## Behavior Invariants

This is a structural migration. It must preserve existing production behavior:

- GitHub Actions live logs and stdout footer markers remain unchanged.
- `SPIDER_OUTPUT_CSV`, `SPIDER_DEDUP_CSV`, `SPIDER_SESSION_ID`, and
  `SPIDER_STAT_*` compatibility remains unchanged.
- Frontend/API task log streaming is not affected.
- Proxy pool setup, proxy coordinator setup, recommend proxy policy setup, and
  fail-open behavior remain unchanged.
- CF bypass sticky behavior and remote CF bypass mirroring remain unchanged.
- Login budget, login-state coordinator, and parallel login routing remain
  unchanged.
- RunnerRegistry register, heartbeat, re-register, unregister, pause handling,
  config snapshot application, and active signal reconciliation remain
  unchanged.
- MovieClaim auto mount/unmount, D1 fail-closed behavior, failure cooldown, and
  orphan-stage sweep remain unchanged.
- WorkDistributor enablement, enqueue/pull/complete/release behavior, and
  fail-open behavior remain unchanged.
- `runtime.sleep` throttling, volume multiplier, global throttle signal,
  pause-all signal, and degraded-mode runner scaling remain unchanged.
- A single CLI/subprocess spider invocation behaves the same before and after
  each phase.

## Rollout

The ADR produces four implementation plans.

### Phase 1: Runtime Skeleton And Active Facade

Implementation plan: `IMP-023`.

Create `SpiderRuntime`, small state dataclasses, runtime-owned service slots,
`bind_active_runtime()`, `get_active_runtime()`, and compatibility facade
coverage. Existing production callers continue using `state.py`.

Required checks:

- legacy `state.*` reads and writes still work;
- two sequential `SpiderRuntime` instances do not share `parsed_links`;
- two sequential runtimes do not share login budget state;
- two sequential runtimes do not share proxy ban HTML captures;
- two sequential runtimes receive distinct holder ids;
- no migrated field has two independent sources of truth.

### Phase 2: Registry And MovieClaim Lifecycle

Implementation plan: `IMP-024`.

Move runner registry lifecycle and MovieClaim auto lifecycle into
runtime-owned state/services. `state.py` setup helpers remain as facade entry
points.

Required checks:

- heartbeat loop still feeds MovieClaim recommendations;
- heartbeat stop/unregister is explicit and idempotent;
- `SpiderRuntime.close()` can be called multiple times safely;
- D1 fail-closed behavior remains unchanged;
- MovieClaim orphan sweep remains best-effort and idempotent.

### Phase 3: Production Caller Migration

Implementation plan: `IMP-025`.

Move production callers in sleep, proxy, request, login, fetch, detail, and
report code toward explicit runtime/context access. `state.py` still exists for
tests and unmigrated compatibility.

Required checks:

- fetch/detail/login/proxy coordinator tests still pass;
- production modules no longer reach through `state.py` for fields migrated in
  this phase;
- sleep singleton behavior is backed by the active runtime, not by a second
  module-level instance;
- GitHub Actions stdout/log behavior remains unchanged.

### Phase 4: Legacy Facade Freeze Or Removal

Implementation plan: `IMP-026`.

Delete or freeze direct-mutation compatibility once production callers have
migrated. The final state must converge to explicit runtime/context access. Any
remaining public facade entries must be intentionally documented compatibility
APIs rather than accidental globals.

Required checks:

- production code no longer directly mutates legacy `state.py` fields;
- allowed compatibility entries are documented;
- deleted compatibility entries have tests proving the new explicit path;
- documentation names the final runtime/context usage rule.

## Testing Strategy

Each implementation plan must include focused unit tests for the phase and run
the existing related suites. The minimum suites are:

- state facade and runtime isolation tests;
- runner registry setup and heartbeat tests;
- MovieClaim auto-toggle and setup tests;
- apply-config-snapshot and active-signal tests;
- sleep/coordinator tests;
- fetch engine and login coordinator tests;
- detail runner MovieClaim and WorkDistributor tests;
- spider smoke tests that cover old `state.*` compatibility.

Full GitHub Actions workflow dry runs and broader API task runner E2E checks
are bake activities or follow-up ADR work. They should not block the first
structural phase unless that phase changes workflow-visible behavior.

## Future ADR Candidates

These items are intentionally outside this ADR:

- API task runner and FastAPI runtime global consolidation;
- caller-supplied runner holder id for Pipeline/API integrations;
- same-process concurrent spider runtime support;
- replacing active-runtime facade dependencies with fully explicit dependency
  injection across all internal modules;
- broader workflow/API runtime lifecycle management.
