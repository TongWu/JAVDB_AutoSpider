# IMP-025: ADR-013 Phase 3 - Explicit Runtime Callers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move production Spider callers away from implicit module-level runtime state and toward explicit `SpiderRuntime` / state / service access.

**Architecture:** Keep `state.py` compatibility for unmigrated tests, but production code paths should receive runtime-owned dependencies through function arguments, backend constructors, or `SpiderRunService` composition. `runtime.sleep` compatibility names remain, but production callers use `runtime.sleep` objects from `SpiderRuntime`.

**Tech Stack:** Python 3.11, dataclasses, pytest, existing Spider fetch/detail/login/proxy modules.

**Source spec:** [ADR-013](../adr/ADR-013-runner-runtime-state-consolidation.md), D2-D5, D10-D11.

**Non-negotiable:** Do not change fetch, parsing, login, sleep, proxy, MovieClaim, WorkDistributor, reporting, or stdout behavior. Each production caller migration must have a test proving old behavior still works.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/spider/runtime/context.py` | Add setup methods and convenience accessors used by production callers. |
| `javdb/spider/runtime/state.py` | Keep compatibility wrappers while production code moves away from direct field use. |
| `javdb/spider/runtime/sleep.py` | Add runtime-owned sleep factory/accessor and keep compatibility names. |
| `javdb/spider/runtime/proxy_state.py` | Move proxy mutators to accept runtime state or resolve active runtime explicitly. |
| `javdb/spider/app/run_service.py` | Pass runtime through index/detail/report orchestration. |
| `javdb/spider/fetch/index.py` | Accept runtime argument and pass it to sequential/parallel paths. |
| `javdb/spider/fetch/index_parallel.py` | Accept runtime and pass runtime-owned backend state. |
| `javdb/spider/fetch/fallback.py` | Use runtime services/state instead of direct `state.*` reads in production path. |
| `javdb/spider/fetch/session.py` | Use `LoginRunState` and runtime services for login counters and clients. |
| `javdb/spider/fetch/login_coordinator.py` | Use runtime login state/services instead of module globals. |
| `javdb/spider/fetch/fetch_engine.py` | Accept runtime services for coordinator/sleep/proxy access. |
| `javdb/spider/fetch/backend.py` | Extend backend contract only where runtime state must be surfaced. |
| `javdb/spider/detail/runner.py` | Accept runtime and use runtime-owned detail/MovieClaim/WorkDistributor state. |
| `javdb/spider/detail/parallel_mode.py` | Pass runtime to `ParallelFetchBackend`. |
| `javdb/spider/detail/sequential_mode.py` | Pass runtime to sequential backend helpers. |
| `javdb/spider/runtime/report.py` | Accept runtime for summary proxy stats and ban HTML reporting. |
| `tests/unit/test_spider_runtime_explicit_callers.py` | New architecture tests proving migrated production modules do not direct-mutate `state.py`. |

---

## Task 1: Add Runtime-Owned Sleep Factory

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `javdb/spider/runtime/sleep.py`
- Create: `tests/unit/test_spider_runtime_explicit_callers.py`

- [ ] **Step 1: Write failing sleep ownership test**

Create `tests/unit/test_spider_runtime_explicit_callers.py`:

```python
from __future__ import annotations

from javdb.spider.runtime.context import SpiderRuntime
from javdb.spider.runtime.sleep import ensure_sleep_runtime


def test_each_runtime_gets_its_own_sleep_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    ensure_sleep_runtime(first)
    ensure_sleep_runtime(second)

    assert first.sleep.movie_sleep_mgr is not second.sleep.movie_sleep_mgr
    assert first.sleep.penalty_tracker is not second.sleep.penalty_tracker
    assert first.sleep.triple_window_throttle is not second.sleep.triple_window_throttle
```

- [ ] **Step 2: Run test and verify the expected failure**

Run:

```bash
pytest tests/unit/test_spider_runtime_explicit_callers.py::test_each_runtime_gets_its_own_sleep_state -v
```

Expected: FAIL with `ImportError` for `ensure_sleep_runtime`.

- [ ] **Step 3: Implement sleep runtime factory**

Add to `javdb/spider/runtime/sleep.py`:

```python
def ensure_sleep_runtime(runtime):
    if runtime.sleep.penalty_tracker is None:
        runtime.sleep.penalty_tracker = PenaltyTracker()
    if runtime.sleep.triple_window_throttle is None:
        runtime.sleep.triple_window_throttle = TripleWindowThrottle()
    runtime.sleep.dual_window_throttle = runtime.sleep.triple_window_throttle
    if runtime.sleep.movie_sleep_mgr is None:
        runtime.sleep.movie_sleep_mgr = MovieSleepManager(
            _BASE_MIN,
            _BASE_MAX,
            penalty_tracker=runtime.sleep.penalty_tracker,
            throttle=runtime.sleep.triple_window_throttle,
        )
    return runtime.sleep
```

Keep existing module-level `penalty_tracker`, `triple_window_throttle`, and
`movie_sleep_mgr` compatibility names until Phase 4.

- [ ] **Step 4: Verify sleep ownership test passes**

Run:

```bash
pytest tests/unit/test_spider_runtime_explicit_callers.py::test_each_runtime_gets_its_own_sleep_state -v
```

Expected: PASS.

---

## Task 2: Pass Runtime Through `run_service`

**Files:**
- Modify: `javdb/spider/app/run_service.py`
- Modify: `javdb/spider/fetch/index.py`
- Modify: `javdb/spider/detail/runner.py`
- Modify: `javdb/spider/runtime/report.py`

- [ ] **Step 1: Add runtime parameter assertions in focused tests**

Add tests to `tests/unit/test_spider_runtime_explicit_callers.py`:

```python
from javdb.spider.app import run_service
from javdb.spider.runtime.context import SpiderRuntime


def test_run_service_passes_runtime_to_index_fetch(monkeypatch):
    runtime = SpiderRuntime()
    observed = {}

    def fake_fetch_all_index_pages(*args, **kwargs):
        observed["runtime"] = kwargs["runtime"]
        return {
            "all_index_results_phase1": [],
            "all_index_results_phase2": [],
            "any_proxy_banned": False,
            "use_proxy": False,
            "use_cf_bypass": False,
            "csv_path": "reports/out.csv",
            "last_valid_page": 0,
        }

    monkeypatch.setattr(run_service, "fetch_all_index_pages", fake_fetch_all_index_pages)

    # Invoke the smallest helper introduced during implementation rather than
    # full _main; the helper must contain the index-fetch call.
    run_service._fetch_index_for_runtime(
        runtime=runtime,
        session=object(),
        start_page=1,
        end_page=1,
        parse_all=False,
        phase_mode="all",
        custom_url=None,
        ignore_release_date=False,
        use_proxy=False,
        use_cf_bypass=False,
        max_consecutive_empty=3,
        output_csv="out.csv",
        output_dated_dir="reports",
        csv_path="reports/out.csv",
        user_specified_output=False,
        parsed_movies_history_phase1={},
        parsed_movies_history_phase2={},
        use_parallel=False,
    )

    assert observed["runtime"] is runtime
```

- [ ] **Step 2: Extract helper and pass runtime**

Create a helper in `run_service.py`:

```python
def _fetch_index_for_runtime(*, runtime, **kwargs):
    return fetch_all_index_pages(runtime=runtime, **kwargs)
```

Update the existing `_main()` call to use:

```python
idx_result = _fetch_index_for_runtime(
    runtime=state.get_active_runtime(),
    session=session,
    start_page=start_page,
    end_page=end_page,
    parse_all=parse_all,
    phase_mode=phase_mode,
    custom_url=custom_url,
    ignore_release_date=ignore_release_date,
    use_proxy=use_proxy,
    use_cf_bypass=use_cf_bypass,
    max_consecutive_empty=max_consecutive_empty,
    output_csv=output_csv,
    output_dated_dir=output_dated_dir,
    csv_path=csv_path,
    user_specified_output=bool(args.output_file),
    parsed_movies_history_phase1=parsed_movies_history_phase1,
    parsed_movies_history_phase2=parsed_movies_history_phase2,
    use_parallel=use_parallel,
)
```

- [ ] **Step 3: Update callee signatures**

Add `runtime` keyword-only parameters to production functions:

```python
def fetch_all_index_pages(*, runtime, session, start_page: int, ...):
    ...
```

```python
def process_detail_entries(*, runtime, backend: FetchBackend, entries: list[dict], ...):
    ...
```

```python
def generate_summary_report(*, runtime, phase_mode, parse_all, ...):
    ...
```

Keep old compatibility wrappers only for tests that call these functions
directly. Direct production calls from `run_service.py` must pass runtime.

- [ ] **Step 4: Verify focused runtime-passing test**

Run:

```bash
pytest tests/unit/test_spider_runtime_explicit_callers.py::test_run_service_passes_runtime_to_index_fetch -v
```

Expected: PASS.

---

## Task 3: Migrate Proxy And Request Service Access

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `javdb/spider/runtime/state.py`
- Modify: `javdb/spider/runtime/proxy_state.py`
- Modify: `javdb/spider/fetch/fallback.py`
- Modify: `javdb/spider/fetch/fetch_engine.py`
- Modify: `tests/unit/test_sleep_with_coordinator.py`
- Modify: `tests/unit/test_engine.py`

- [ ] **Step 1: Add service accessor methods**

Add to `SpiderRuntime` in `context.py`:

```python
@property
def proxy_pool(self):
    return self.services.proxy_pool


@proxy_pool.setter
def proxy_pool(self, value):
    self.services.proxy_pool = value


@property
def request_handler(self):
    return self.services.request_handler


@request_handler.setter
def request_handler(self, value):
    self.services.request_handler = value
```

- [ ] **Step 2: Convert `setup_proxy_pool()` and `initialize_request_handler()`**

Move the existing `state.setup_proxy_pool()` body into
`SpiderRuntime.setup_proxy_pool(self, use_proxy)`. Keep the same branch order,
log messages, atexit registration, callback wiring, and exception handling.
Apply these exact ownership substitutions while moving the body:

| Current module field | Runtime-owned replacement |
|---|---|
| `global_proxy_pool` | `self.services.proxy_pool` |
| `global_proxy_coordinator` | `self.services.proxy_coordinator` |
| `global_login_state_client` | `self.services.login_state_client` |
| `global_movie_claim_client` | `self.movie_claim.client_public` and `self.services.movie_claim_client` |
| `global_runner_registry_client` | `self.services.runner_registry_client` |
| `global_recommend_proxy_policy` | `self.services.recommend_proxy_policy` |
| `global_work_distributor_client` | `self.services.work_distributor_client` |

Move the existing `state.initialize_request_handler()` body into
`SpiderRuntime.initialize_request_handler(self)`. Keep the same
`RequestConfig` construction and callbacks. Apply these ownership
substitutions:

| Current module field | Runtime-owned replacement |
|---|---|
| `global_request_handler` | `self.services.request_handler` |
| `global_proxy_pool` | `self.services.proxy_pool` |
| `global_proxy_coordinator` | `self.services.proxy_coordinator` |

Do not create empty methods during this step. The moved runtime methods must be
complete before the `state.py` wrappers delegate to them.

- [ ] **Step 3: Keep `state.py` wrappers**

```python
def setup_proxy_pool(use_proxy) -> None:
    runtime = get_active_runtime()
    if runtime is not None:
        runtime.setup_proxy_pool(use_proxy)
        _sync_legacy_globals_from_runtime(runtime)
        return None
    return _setup_proxy_pool_legacy(use_proxy)


def initialize_request_handler():
    runtime = get_active_runtime()
    if runtime is not None:
        runtime.initialize_request_handler()
        _sync_legacy_globals_from_runtime(runtime)
        return None
    return _initialize_request_handler_legacy()
```

- [ ] **Step 4: Replace production `state.global_proxy_pool` reads**

In production paths that now receive `runtime`, replace:

```python
state.global_proxy_pool
```

with:

```python
runtime.services.proxy_pool
```

This applies to `fetch/fallback.py`, `fetch/fetch_engine.py`, and
`runtime/report.py`. Keep test compatibility through `state.py` wrappers.

- [ ] **Step 5: Run proxy/request tests**

Run:

```bash
pytest tests/unit/test_engine.py tests/unit/test_sleep_with_coordinator.py tests/unit/test_request_handler.py tests/unit/test_proxy_pool.py -v
```

Expected: PASS.

---

## Task 4: Migrate Login And Detail State Access

**Files:**
- Modify: `javdb/spider/fetch/session.py`
- Modify: `javdb/spider/fetch/login_coordinator.py`
- Modify: `javdb/spider/detail/runner.py`
- Modify: `tests/unit/test_login_coordinator_park.py`
- Modify: `tests/smoke/test_spider_detail_runner.py`

- [ ] **Step 1: Replace production login globals**

In functions that receive runtime, replace these `state.*` fields with
`runtime.login.*`:

- `login_attempted`
- `refreshed_session_cookie`
- `logged_in_proxy_name`
- `current_login_state_version`
- `login_attempts_per_proxy`
- `login_failures_per_proxy`
- `login_total_attempts`
- `login_total_budget`
- `_login_budget_deducted_proxies`

When a function is still used by old tests without runtime, add a keyword
argument with explicit fallback:

```python
def perform_login(..., runtime=None):
    runtime = runtime or state.get_active_runtime()
    login_state = runtime.login if runtime is not None else state
```

Production callers must pass runtime; fallback exists only for compatibility.

- [ ] **Step 2: Replace production detail globals**

In `detail/runner.py`, use:

```python
detail_state = runtime.detail
movie_claim_state = runtime.movie_claim
services = runtime.services
```

Replace production reads of:

- `state.parsed_links`
- `state.global_movie_claim_client`
- `state.global_work_distributor_client`
- `state.runtime_holder_id`

with runtime-owned fields. Keep wrapper behavior for direct tests until
Phase 4.

- [ ] **Step 3: Run login/detail tests**

Run:

```bash
pytest tests/unit/test_login_coordinator_park.py tests/unit/test_detail_runner_movie_claim.py tests/unit/test_detail_runner_work_distributor.py tests/smoke/test_spider_detail_runner.py -v
```

Expected: PASS.

---

## Task 5: Phase 3 Gate

- [ ] Run all focused runtime tests:

```bash
pytest tests/unit/test_spider_runtime_context.py tests/unit/test_spider_runtime_state_facade.py tests/unit/test_spider_runtime_registry_lifecycle.py tests/unit/test_spider_runtime_explicit_callers.py -v
```

- [ ] Run fetch/detail/login/proxy coordinator suites:

```bash
pytest tests/unit/test_engine.py tests/unit/test_login_coordinator_park.py tests/unit/test_sleep_with_coordinator.py tests/unit/test_detail_runner_movie_claim.py tests/unit/test_detail_runner_work_distributor.py tests/unit/test_setup_runner_registry_client.py tests/unit/test_setup_movie_claim_client.py -v
```

- [ ] Run spider smoke suites:

```bash
pytest tests/smoke/test_spider.py tests/smoke/test_spider_detail_runner.py tests/smoke/test_spider_app_main.py -v
```

- [ ] Confirm production modules have no newly migrated direct field reads:

```bash
rg -n "state\\.(parsed_links|global_proxy_pool|global_request_handler|global_movie_claim_client|global_work_distributor_client|runtime_holder_id|login_total_attempts|login_total_budget)" javdb/spider -g '*.py'
```

Expected: output is limited to `javdb/spider/runtime/state.py` and documented compatibility fallbacks.

- [ ] Commit:

```bash
git add javdb/spider/runtime/context.py javdb/spider/runtime/state.py javdb/spider/runtime/sleep.py javdb/spider/runtime/proxy_state.py javdb/spider/app/run_service.py javdb/spider/fetch javdb/spider/detail javdb/spider/runtime/report.py tests
git commit -m "refactor(runtime): pass spider runtime through production callers"
```
