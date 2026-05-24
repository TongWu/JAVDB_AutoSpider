# IMP-ADR013-01: ADR-013 Phase 1 - Runtime Skeleton And Active Facade

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-013 Phase 1 by introducing `SpiderRuntime`, focused runtime state dataclasses, runtime service slots, active-runtime binding, and legacy `state.py` facade compatibility without changing production behavior.

**Architecture:** `SpiderRunService` creates a `SpiderRuntime` for each run and binds it as the active runtime. `state.py` remains the compatibility surface and only rebinds safe mutable handles plus `runtime_holder_id` in Phase 1; scalar fields with active direct assignment writers stay on the module until Phase 3/4.

**Tech Stack:** Python 3.11, dataclasses, threading, pytest, existing Spider runtime modules.

**Source spec:** [ADR-013](ADR-013-runner-runtime-state-consolidation.md), D1-D7.

**Non-negotiable:** Phase 1 is behavior-preserving. Do not move RunnerRegistry, MovieClaim, sleep, proxy, login, fetch, detail, or report logic yet. Do not introduce a second source of truth for any migrated field.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/spider/runtime/context.py` | New runtime state dataclasses, service slots, and `SpiderRuntime` aggregate. |
| `javdb/spider/runtime/active.py` | Active runtime binding helpers used by `state.py` and tests. |
| `javdb/spider/runtime/state.py` | Legacy facade bindings: `bind_active_runtime`, `get_active_runtime`, selected mutable handle rebinding. |
| `javdb/spider/app/run_service.py` | Create/bind/close `SpiderRuntime` around the existing `main()` flow. |
| `tests/unit/test_spider_runtime_context.py` | New runtime isolation and close-idempotency tests. |
| `tests/unit/test_spider_runtime_state_facade.py` | New legacy facade binding tests. |
| `tests/smoke/test_spider_app_main.py` | Existing smoke coverage for the entrypoint wrapper. |

---

## Task 1: Add Runtime State Dataclasses

**Files:**
- Create: `javdb/spider/runtime/context.py`
- Create: `tests/unit/test_spider_runtime_context.py`

- [ ] **Step 1: Write failing runtime isolation tests**

Create `tests/unit/test_spider_runtime_context.py`:

```python
from __future__ import annotations

from javdb.spider.runtime.context import SpiderRuntime


def test_runtime_instances_do_not_share_detail_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    first.detail.parsed_links.add("/v/first")

    assert "/v/first" in first.detail.parsed_links
    assert second.detail.parsed_links == set()


def test_runtime_instances_do_not_share_login_budget_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    first.login.login_attempts_per_proxy["proxy-a"] = 2
    first.login.login_total_attempts = 2

    assert second.login.login_attempts_per_proxy == {}
    assert second.login.login_total_attempts == 0


def test_runtime_instances_do_not_share_proxy_ban_html_files():
    first = SpiderRuntime()
    second = SpiderRuntime()

    first.proxy.proxy_ban_html_files.append("logs/proxy_ban_a.txt")

    assert first.proxy.proxy_ban_html_files == ["logs/proxy_ban_a.txt"]
    assert second.proxy.proxy_ban_html_files == []


def test_runtime_instances_have_distinct_holder_ids():
    first = SpiderRuntime()
    second = SpiderRuntime()

    assert first.runner_registry.holder_id.startswith("runner-")
    assert second.runner_registry.holder_id.startswith("runner-")
    assert first.runner_registry.holder_id != second.runner_registry.holder_id


def test_runtime_close_is_idempotent_before_services_are_migrated():
    runtime = SpiderRuntime()

    runtime.close()
    runtime.close()

    assert runtime.closed is True
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run:

```bash
pytest tests/unit/test_spider_runtime_context.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.spider.runtime.context'`.

- [ ] **Step 3: Implement runtime dataclasses**

Create `javdb/spider/runtime/context.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import uuid
from typing import Any, Optional

from javdb.spider.runtime.config import LOGIN_ATTEMPTS_PER_PROXY_LIMIT, PROXY_POOL


def _new_holder_id() -> str:
    return f"runner-{uuid.uuid4().hex[:16]}"


@dataclass
class DetailRunState:
    parsed_links: set[str] = field(default_factory=set)


@dataclass
class ProxyRunState:
    proxy_ban_html_files: list[str] = field(default_factory=list)
    always_bypass_time: Optional[int] = None
    proxies_requiring_cf_bypass: dict[str, float] = field(default_factory=dict)
    cf_bypass_lock: threading.Lock = field(default_factory=threading.Lock)
    signal_banned_proxies: set[str] = field(default_factory=set)
    signal_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class LoginRunState:
    login_attempted: bool = False
    refreshed_session_cookie: Optional[str] = None
    logged_in_proxy_name: Optional[str] = None
    current_login_state_version: Optional[int] = None
    login_attempts_per_proxy: dict[str, int] = field(default_factory=dict)
    login_failures_per_proxy: dict[str, int] = field(default_factory=dict)
    login_total_attempts: int = 0
    login_total_budget: int = field(
        default_factory=lambda: (
            len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT
            if PROXY_POOL else 0
        )
    )
    login_budget_deducted_proxies: set[str] = field(default_factory=set)
    login_budget_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class RunnerRegistryState:
    holder_id: str = field(default_factory=_new_holder_id)
    session: Any = None
    heartbeat_thread: Optional[threading.Thread] = None
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    unregistered: bool = False
    last_applied_config_version: int = -1


@dataclass
class MovieClaimRuntimeState:
    client_pending: Any = None
    client_public: Any = None
    mode: str = "off"
    intended_mode: str = "off"
    last_recommended: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    swept_at_exit: bool = False


@dataclass
class SleepRuntimeState:
    penalty_tracker: Any = None
    triple_window_throttle: Any = None
    dual_window_throttle: Any = None
    movie_sleep_mgr: Any = None


@dataclass
class RuntimeServices:
    proxy_pool: Any = None
    request_handler: Any = None
    proxy_coordinator: Any = None
    login_state_client: Any = None
    movie_claim_client: Any = None
    runner_registry_client: Any = None
    recommend_proxy_policy: Any = None
    work_distributor_client: Any = None


@dataclass
class SpiderRuntime:
    detail: DetailRunState = field(default_factory=DetailRunState)
    proxy: ProxyRunState = field(default_factory=ProxyRunState)
    login: LoginRunState = field(default_factory=LoginRunState)
    runner_registry: RunnerRegistryState = field(default_factory=RunnerRegistryState)
    movie_claim: MovieClaimRuntimeState = field(default_factory=MovieClaimRuntimeState)
    sleep: SleepRuntimeState = field(default_factory=SleepRuntimeState)
    services: RuntimeServices = field(default_factory=RuntimeServices)
    closed: bool = False
    _close_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def close(self) -> None:
        with self._close_lock:
            if self.closed:
                return
            self.closed = True
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/unit/test_spider_runtime_context.py -v
```

Expected: PASS.

---

## Task 2: Add Active Runtime Binding

**Files:**
- Create: `javdb/spider/runtime/active.py`
- Modify: `javdb/spider/runtime/state.py`
- Create: `tests/unit/test_spider_runtime_state_facade.py`

- [ ] **Step 1: Write failing facade binding tests**

Create `tests/unit/test_spider_runtime_state_facade.py`:

```python
from __future__ import annotations

import javdb.spider.runtime.state as state
from javdb.spider.runtime.context import SpiderRuntime


def test_bind_active_runtime_rebinds_mutable_detail_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    state.bind_active_runtime(first)
    state.parsed_links.add("/v/first")
    assert first.detail.parsed_links == {"/v/first"}

    state.bind_active_runtime(second)
    assert state.parsed_links is second.detail.parsed_links
    assert state.parsed_links == set()


def test_bind_active_runtime_rebinds_proxy_ban_html_files():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)
    state.proxy_ban_html_files.append("logs/proxy_ban.txt")

    assert runtime.proxy.proxy_ban_html_files == ["logs/proxy_ban.txt"]


def test_bind_active_runtime_exposes_runtime_holder_id():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)

    assert state.runtime_holder_id == runtime.runner_registry.holder_id


def test_clear_active_runtime_leaves_facade_importable():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)
    state.clear_active_runtime(runtime)

    assert state.get_active_runtime() is None
    assert isinstance(state.parsed_links, set)
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run:

```bash
pytest tests/unit/test_spider_runtime_state_facade.py -v
```

Expected: FAIL with `AttributeError: module 'javdb.spider.runtime.state' has no attribute 'bind_active_runtime'`.

- [ ] **Step 3: Implement `active.py`**

Create `javdb/spider/runtime/active.py`:

```python
from __future__ import annotations

import threading
from typing import Optional

from javdb.spider.runtime.context import SpiderRuntime

_active_runtime: Optional[SpiderRuntime] = None
_active_lock = threading.RLock()


def bind_active_runtime(runtime: SpiderRuntime) -> SpiderRuntime:
    global _active_runtime
    with _active_lock:
        _active_runtime = runtime
        return runtime


def get_active_runtime() -> Optional[SpiderRuntime]:
    with _active_lock:
        return _active_runtime


def clear_active_runtime(runtime: SpiderRuntime | None = None) -> None:
    global _active_runtime
    with _active_lock:
        if runtime is None or _active_runtime is runtime:
            _active_runtime = None
```

- [ ] **Step 4: Add `state.py` facade binding helpers**

Modify `javdb/spider/runtime/state.py` near the existing mutable globals:

```python
from javdb.spider.runtime.active import (  # noqa: E402
    bind_active_runtime as _bind_active_runtime,
    clear_active_runtime as _clear_active_runtime,
    get_active_runtime,
)
from javdb.spider.runtime.context import SpiderRuntime  # noqa: E402


def _sync_legacy_globals_from_runtime(runtime: SpiderRuntime) -> None:
    global parsed_links, proxy_ban_html_files, runtime_holder_id
    parsed_links = runtime.detail.parsed_links
    proxy_ban_html_files = runtime.proxy.proxy_ban_html_files
    runtime_holder_id = runtime.runner_registry.holder_id


def bind_active_runtime(runtime: SpiderRuntime) -> SpiderRuntime:
    bound = _bind_active_runtime(runtime)
    _sync_legacy_globals_from_runtime(bound)
    return bound


def clear_active_runtime(runtime: SpiderRuntime | None = None) -> None:
    _clear_active_runtime(runtime)
```

Do not rebind scalar fields such as `always_bypass_time`,
`login_total_attempts`, or `current_login_state_version` in Phase 1. Those
fields still have direct assignment writers and remain module-owned until
Phase 3/4.

- [ ] **Step 5: Verify facade tests pass**

Run:

```bash
pytest tests/unit/test_spider_runtime_state_facade.py -v
```

Expected: PASS.

---

## Task 3: Bind Runtime In `SpiderRunService`

**Files:**
- Modify: `javdb/spider/app/run_service.py`
- Modify: `tests/smoke/test_spider_app_main.py`

- [ ] **Step 1: Add a service-level binding test**

Extend `tests/smoke/test_spider_app_main.py` with a test that patches the
existing `main()` function so the test does not run a real spider:

```python
from __future__ import annotations

import javdb.spider.runtime.state as state
from javdb.spider.app import run_service


def test_spider_run_service_binds_and_clears_runtime(monkeypatch):
    observed = {}

    def fake_main():
        runtime = state.get_active_runtime()
        observed["runtime"] = runtime
        observed["holder_id"] = state.runtime_holder_id
        return 0

    monkeypatch.setattr(run_service, "main", fake_main)

    result = run_service.SpiderRunService().run()

    assert result == 0
    assert observed["runtime"] is not None
    assert observed["holder_id"] == observed["runtime"].runner_registry.holder_id
    assert state.get_active_runtime() is None
    assert observed["runtime"].closed is True
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run:

```bash
pytest tests/smoke/test_spider_app_main.py::test_spider_run_service_binds_and_clears_runtime -v
```

Expected: FAIL because `SpiderRunService.run()` does not bind a runtime yet.

- [ ] **Step 3: Update `SpiderRunService.run()`**

Modify `javdb/spider/app/run_service.py`:

```python
class SpiderRunService:
    """Application-service wrapper for the spider runtime."""

    def run(self):
        from javdb.spider.runtime.context import SpiderRuntime
        from javdb.spider.runtime import state as runtime_state

        runtime = SpiderRuntime()
        runtime_state.bind_active_runtime(runtime)
        try:
            return main()
        finally:
            try:
                runtime.close()
            finally:
                runtime_state.clear_active_runtime(runtime)
```

- [ ] **Step 4: Verify service binding test passes**

Run:

```bash
pytest tests/smoke/test_spider_app_main.py::test_spider_run_service_binds_and_clears_runtime -v
```

Expected: PASS.

---

## Task 4: Phase 1 Gate

- [ ] Run focused runtime tests:

```bash
pytest tests/unit/test_spider_runtime_context.py tests/unit/test_spider_runtime_state_facade.py tests/smoke/test_spider_app_main.py -v
```

- [ ] Run existing state-heavy tests to confirm compatibility:

```bash
pytest tests/unit/test_setup_runner_registry_client.py tests/unit/test_runner_heartbeat_dynamic_interval.py tests/unit/test_setup_movie_claim_client.py tests/unit/test_movie_claim_auto_toggle.py -v
```

- [ ] Run smoke coverage for existing Spider behavior:

```bash
pytest tests/smoke/test_spider_detail_runner.py tests/smoke/test_spider_app_main.py -v
```

- [ ] Confirm `rg -n "state\\.always_bypass_time\\s*=" javdb tests -g '*.py'` still shows fields intentionally left module-owned in Phase 1.

- [ ] Confirm no migrated mutable handle has two sources of truth:

```bash
python - <<'PY'
import javdb.spider.runtime.state as state
from javdb.spider.runtime.context import SpiderRuntime
r = SpiderRuntime()
state.bind_active_runtime(r)
assert state.parsed_links is r.detail.parsed_links
assert state.proxy_ban_html_files is r.proxy.proxy_ban_html_files
assert state.runtime_holder_id == r.runner_registry.holder_id
state.clear_active_runtime(r)
PY
```

- [ ] Commit:

```bash
git add javdb/spider/runtime/context.py javdb/spider/runtime/active.py javdb/spider/runtime/state.py javdb/spider/app/run_service.py tests/unit/test_spider_runtime_context.py tests/unit/test_spider_runtime_state_facade.py tests/smoke/test_spider_app_main.py
git commit -m "refactor(runtime): add spider runtime facade"
```
