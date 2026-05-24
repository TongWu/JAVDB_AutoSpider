# IMP-ADR013-02: ADR-013 Phase 2 - Registry And MovieClaim Lifecycle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move RunnerRegistry heartbeat/session lifecycle and MovieClaim auto lifecycle from module globals into `SpiderRuntime` owned state/services while preserving old `state.py` entrypoints.

**Architecture:** Existing `state.py` functions become compatibility wrappers around active-runtime methods. The moved method bodies keep the current algorithms, logging, exception swallowing, and fail-open/fail-closed semantics. `RunnerRegistryState` owns thread/session/signal bookkeeping; `MovieClaimRuntimeState` owns MovieClaim mode and lock state.

**Tech Stack:** Python 3.11, threading, atexit, Cloudflare Durable Object clients, pytest.

**Source spec:** [ADR-013](ADR-013-runner-runtime-state-consolidation.md), D6-D9, D11.

**Non-negotiable:** Do not change registry heartbeat cadence, MovieClaim auto mount/unmount behavior, D1 fail-closed behavior, or orphan-stage sweep behavior. This phase is ownership migration only.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/spider/runtime/context.py` | Add runtime methods and state aliases for registry/MovieClaim lifecycle. |
| `javdb/spider/runtime/state.py` | Convert registry/MovieClaim functions to active-runtime compatibility wrappers. |
| `tests/unit/test_setup_runner_registry_client.py` | Keep old wrapper coverage; add runtime-owned assertions. |
| `tests/unit/test_runner_heartbeat_dynamic_interval.py` | Keep cadence and signal feedback coverage through runtime-owned state. |
| `tests/unit/test_setup_movie_claim_client.py` | Keep MovieClaim setup and D1 fail-closed coverage. |
| `tests/unit/test_movie_claim_auto_toggle.py` | Keep MovieClaim auto state-machine coverage. |
| `tests/unit/test_spider_runtime_registry_lifecycle.py` | New explicit `SpiderRuntime.close()` lifecycle tests. |

---

## Task 1: Add Runtime-Owned Registry Lifecycle Tests

**Files:**
- Create: `tests/unit/test_spider_runtime_registry_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create `tests/unit/test_spider_runtime_registry_lifecycle.py`:

```python
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from javdb.proxy.coordinator.runner_registry_client import UnregisterResult
from javdb.spider.runtime.context import SpiderRuntime


def test_runtime_close_stops_heartbeat_and_unregisters_once():
    runtime = SpiderRuntime()
    client = MagicMock()
    client.unregister.return_value = UnregisterResult(
        unregistered=True,
        server_time_ms=1,
    )
    runtime.services.runner_registry_client = client
    runtime.runner_registry.heartbeat_stop.clear()

    runtime.close()
    runtime.close()

    assert runtime.runner_registry.heartbeat_stop.is_set()
    client.unregister.assert_called_once_with(
        runtime.runner_registry.holder_id,
        session=runtime.runner_registry.session,
    )


def test_runtime_close_joins_live_heartbeat_thread():
    runtime = SpiderRuntime()
    stop_seen = {}

    def worker():
        runtime.runner_registry.heartbeat_stop.wait(timeout=2)
        stop_seen["stopped"] = runtime.runner_registry.heartbeat_stop.is_set()

    thread = threading.Thread(target=worker, daemon=True)
    runtime.runner_registry.heartbeat_thread = thread
    thread.start()

    runtime.close()

    assert stop_seen["stopped"] is True
    assert not thread.is_alive()
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run:

```bash
pytest tests/unit/test_spider_runtime_registry_lifecycle.py -v
```

Expected: FAIL because `SpiderRuntime.close()` does not yet unregister or stop heartbeat.

---

## Task 2: Move Registry State Fields Into Runtime

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `javdb/spider/runtime/state.py`

- [ ] **Step 1: Extend `RunnerRegistryState`**

Modify `RunnerRegistryState` in `javdb/spider/runtime/context.py`:

```python
@dataclass
class RunnerRegistryState:
    holder_id: str = field(default_factory=_new_holder_id)
    session: Any = None
    heartbeat_thread: Optional[threading.Thread] = None
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    unregistered: bool = False
    heartbeat_interval_multi_runner_sec: float = 60.0
    heartbeat_interval_single_runner_sec: float = 15.0
    runner_heartbeat_interval_sec: float = 60.0
    last_applied_config_version: int = -1
    signal_banned_proxies: set[str] = field(default_factory=set)
    signal_lock: threading.Lock = field(default_factory=threading.Lock)
```

- [ ] **Step 2: Bind old registry globals to runtime state**

Update `_sync_legacy_globals_from_runtime()` in `state.py`:

```python
def _sync_legacy_globals_from_runtime(runtime: SpiderRuntime) -> None:
    global parsed_links, proxy_ban_html_files, runtime_holder_id
    global _runner_session, _runner_heartbeat_thread, _runner_unregistered
    global _runner_heartbeat_stop, _last_applied_config_version
    global _signal_banned_proxies, _signal_lock

    parsed_links = runtime.detail.parsed_links
    proxy_ban_html_files = runtime.proxy.proxy_ban_html_files
    runtime_holder_id = runtime.runner_registry.holder_id
    _runner_session = runtime.runner_registry.session
    _runner_heartbeat_thread = runtime.runner_registry.heartbeat_thread
    _runner_heartbeat_stop = runtime.runner_registry.heartbeat_stop
    _runner_unregistered = runtime.runner_registry.unregistered
    _last_applied_config_version = runtime.runner_registry.last_applied_config_version
    _signal_banned_proxies = runtime.runner_registry.signal_banned_proxies
    _signal_lock = runtime.runner_registry.signal_lock
```

- [ ] **Step 3: Keep wrapper compatibility**

After moving a registry function body into a runtime method, leave a wrapper in
`state.py` with the same public name:

```python
def setup_runner_registry_client():
    runtime = get_active_runtime()
    if runtime is not None:
        return runtime.setup_runner_registry_client()
    return _setup_runner_registry_client_legacy()
```

Use this wrapper pattern for:

- `set_active_runner_session`
- `_runner_heartbeat_loop`
- `_unregister_runner_at_exit`
- `_apply_config_snapshot`
- `_apply_active_signals`
- `_maybe_honour_pipeline_pause`
- `setup_runner_registry_client`

When a wrapper still needs module-level fallback for tests that do not bind a
runtime, preserve the existing body by renaming it to `_name_legacy`. Do not
duplicate logic between runtime and legacy paths after Phase 2 is complete;
tests should bind a runtime where they exercise the new owner.

---

## Task 3: Move MovieClaim State Into Runtime

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `javdb/spider/runtime/state.py`
- Modify: `tests/unit/test_setup_movie_claim_client.py`
- Modify: `tests/unit/test_movie_claim_auto_toggle.py`

- [ ] **Step 1: Extend `MovieClaimRuntimeState`**

Modify `MovieClaimRuntimeState` in `context.py`:

```python
@dataclass
class MovieClaimRuntimeState:
    client_pending: Any = None
    client_public: Any = None
    mode: str = MOVIE_CLAIM_MODE_OFF
    intended_mode: str = MOVIE_CLAIM_MODE_OFF
    last_recommended: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    swept_at_exit: bool = False
    sweep_at_exit_older_than_ms: int = 6 * 60 * 60 * 1000
```

Import `MOVIE_CLAIM_MODE_OFF` from `javdb.proxy.coordinator.movie_claim_client`
at the top of `context.py`.

- [ ] **Step 2: Bind old MovieClaim globals to runtime state**

Update `_sync_legacy_globals_from_runtime()` in `state.py`:

```python
def _sync_legacy_globals_from_runtime(runtime: SpiderRuntime) -> None:
    global global_movie_claim_client
    global _movie_claim_client_pending, _movie_claim_mode
    global _movie_claim_intended_mode, _movie_claim_last_recommended
    global _movie_claim_lock, _movie_claim_swept_at_exit

    # Keep existing bindings from earlier tasks here.
    global_movie_claim_client = runtime.movie_claim.client_public
    _movie_claim_client_pending = runtime.movie_claim.client_pending
    _movie_claim_mode = runtime.movie_claim.mode
    _movie_claim_intended_mode = runtime.movie_claim.intended_mode
    _movie_claim_last_recommended = runtime.movie_claim.last_recommended
    _movie_claim_lock = runtime.movie_claim.lock
    _movie_claim_swept_at_exit = runtime.movie_claim.swept_at_exit
```

When runtime methods mutate MovieClaim state, update both runtime fields and
legacy globals before returning so old direct reads observe the same value.

- [ ] **Step 3: Move these functions under runtime ownership**

Move the existing function bodies without strategy changes:

- `_apply_movie_claim_recommendation`
- `_next_heartbeat_interval`
- `setup_movie_claim_client`
- `enforce_movie_claim_for_d1`
- `_sweep_movie_claim_stages_at_exit`
- `_movie_claim_sweep_shard_dates`

Keep wrapper functions in `state.py` with the same names. Each wrapper delegates
to active runtime when bound and uses the legacy body only when no runtime is
bound.

- [ ] **Step 4: Preserve existing tests through wrappers**

Run:

```bash
pytest tests/unit/test_setup_movie_claim_client.py tests/unit/test_movie_claim_auto_toggle.py -v
```

Expected: PASS.

---

## Task 4: Implement Explicit Runtime Close For Registry

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `javdb/spider/runtime/state.py`

- [ ] **Step 1: Implement close logic in `SpiderRuntime.close()`**

Update `SpiderRuntime.close()`. Add `import contextlib` to
`javdb/spider/runtime/context.py`.

```python
def close(self) -> None:
    with self._close_lock:
        if self.closed:
            return
        self.closed = True

    self.runner_registry.heartbeat_stop.set()
    thread = self.runner_registry.heartbeat_thread
    if (
        thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ):
        thread.join(timeout=5.0)

    client = self.services.runner_registry_client
    if client is not None and not self.runner_registry.unregistered:
        with contextlib.suppress(Exception):
            client.unregister(
                self.runner_registry.holder_id,
                session=self.runner_registry.session,
            )
            self.runner_registry.unregistered = True
        with contextlib.suppress(Exception):
            client.close()
```

Production logging and exception detail remain in the moved registry lifecycle
methods. `close()` is intentionally best-effort and idempotent.

- [ ] **Step 2: Verify runtime lifecycle tests pass**

Run:

```bash
pytest tests/unit/test_spider_runtime_registry_lifecycle.py -v
```

Expected: PASS.

---

## Task 5: Phase 2 Gate

- [ ] Run registry tests:

```bash
pytest tests/unit/test_setup_runner_registry_client.py tests/unit/test_runner_heartbeat_dynamic_interval.py tests/unit/test_runner_registry_client_phase1_session.py tests/unit/test_apply_config_snapshot.py tests/unit/test_apply_active_signals.py -v
```

- [ ] Run MovieClaim tests:

```bash
pytest tests/unit/test_setup_movie_claim_client.py tests/unit/test_movie_claim_auto_toggle.py tests/unit/test_detail_runner_movie_claim.py tests/unit/test_detail_runner_work_distributor.py -v
```

- [ ] Run runtime lifecycle tests:

```bash
pytest tests/unit/test_spider_runtime_context.py tests/unit/test_spider_runtime_state_facade.py tests/unit/test_spider_runtime_registry_lifecycle.py -v
```

- [ ] Confirm no behavior knobs changed:

```bash
python - <<'PY'
import javdb.spider.runtime.state as state
assert state._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC == 60.0
assert state._HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC == 15.0
assert state._RUNNER_HEARTBEAT_INTERVAL_SEC == state._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC
PY
```

- [ ] Commit:

```bash
git add javdb/spider/runtime/context.py javdb/spider/runtime/state.py tests/unit/test_setup_runner_registry_client.py tests/unit/test_runner_heartbeat_dynamic_interval.py tests/unit/test_setup_movie_claim_client.py tests/unit/test_movie_claim_auto_toggle.py tests/unit/test_spider_runtime_registry_lifecycle.py
git commit -m "refactor(runtime): own registry and movie claim state"
```
