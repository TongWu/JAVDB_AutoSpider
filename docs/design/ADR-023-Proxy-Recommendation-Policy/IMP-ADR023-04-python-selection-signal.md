# IMP-ADR023-04: ADR-023 Phase 4 - Python Selection Signal Deepening

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-023 Phase 4 by introducing a Python-side `ProxySelectionSignal` module that owns score adapter selection, freshness handling, per-proxy fallback, and runtime lifecycle without changing proxy selection behavior.

**Architecture:** Keep `ProxyPool` as the final selector. Add `javdb.proxy.selection.ProxySelectionSignal` as a runner-local health-provider adapter that presents the existing `score_for(proxy_name) -> Optional[float]` contract to `ProxyPool`. The signal composes a primary `/recommend_proxy` policy adapter and an optional fallback callable for coordinator lease-health scores. Runtime construction lives behind `ProxySelectionSignal.from_runtime_config(...)`, while tests inject fake adapters and callables directly.

**Tech Stack:** Python, pytest, existing `javdb.proxy` and `javdb.spider.runtime` modules.

**Source spec:** [ADR-023](ADR-023-proxy-recommendation-policy.md), Phase 4; depends on [IMP-ADR023-01](IMP-ADR023-01-shadowscore-confidence-fields.md), [IMP-ADR023-02](IMP-ADR023-02-policy-rollout-flag.md), and [IMP-ADR023-03](IMP-ADR023-03-observability-rollout-hardening.md) for Worker-side recommendation behavior.

**Non-negotiable:** This phase must not change the public `ProxyPool` interface, must not change `ProxyPool` selection math, must not consume `model_score` or `rank_score` in Python, must not change `/lease`, `/report`, `/recommend_proxy`, Worker ranking, ban, or cooldown semantics, and `ProxySelectionSignal.score_for()` must fail open.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/proxy/selection/__init__.py` | Export `ProxySelectionSignal`. |
| Create | `javdb/proxy/selection/signal.py` | Compose primary recommendation policy and fallback health callable behind the `score_for()` contract. |
| Create | `tests/unit/test_proxy_selection_signal.py` | Unit tests for fallback order, fail-open behavior, label, and lifecycle. |
| Modify | `javdb/spider/runtime/context.py` | Replace direct recommendation-policy wiring with `ProxySelectionSignal.from_runtime_config(...)` and store lifecycle in `RuntimeServices`. |
| Modify | `javdb/spider/runtime/state.py` | Preserve legacy global proxy-pool setup behavior while routing through the new selection signal where practical. |
| Modify | `tests/unit/test_spider_runtime_context.py` | Assert runtime service ownership and close lifecycle for `proxy_selection_signal`. |
| Modify if needed | `tests/unit/test_recommend_proxy_policy.py` | Keep lower-level policy tests focused on `/recommend_proxy` cache behavior if wiring expectations move upward. |
| Modify if needed | `tests/unit/test_proxy_pool.py` | Only update if existing tests assume the old direct policy provider wiring. |

## Scope Boundaries

- Do not rename or remove `ProxyPool.get_next_proxy()`.
- Do not change `ProxyPool.set_health_provider(provider)`.
- Do not move clamp, NaN, or exception normalization out of `ProxyPool._safe_health_score()`.
- Do not change weighted-random selection, cooldown, ban handling, or neutral-score behavior.
- Do not retain stale `/recommend_proxy` scores inside the new signal.
- Do not make Python depend on ADR-023 `model_score`, `rank_score`, `confidence`, or `reason_code`.
- Do not add a new external service, storage table, Worker endpoint, or migration.
- Do not widen this phase into a full proxy-selection refactor.

## Locked Design Decisions

- `ProxySelectionSignal` owns only the selection signal, not full proxy selection.
- `score_for(proxy_name) -> Optional[float]` is the only per-selection interface exposed to `ProxyPool`.
- Lifecycle and observability are object-level concerns: `start()`, `close()`, and `label`.
- Fallback is evaluated per proxy:
  1. fresh `/recommend_proxy` score from the primary policy adapter
  2. fallback callable, normally `ProxyCoordinatorClient.get_proxy_health_score`
  3. `None`, which `ProxyPool` interprets as neutral `0.5`
- `score_for()` catches primary and fallback exceptions and returns the best available fallback result, never raising to `ProxyPool`.
- `ProxySelectionSignal.from_runtime_config(...)` is the production construction seam; unit tests construct the object with fakes.
- `SpiderRuntime.services` owns the signal lifecycle through `proxy_selection_signal`, replacing the less precise `recommend_proxy_policy` service field.

---

## Task 1: Pin Selection Signal Behavior With Tests

**Files:**
- Create: `tests/unit/test_proxy_selection_signal.py`

- [ ] **Step 1: Add fake adapters**

Create tiny fakes inside the test file rather than importing Worker-facing clients:

```python
class FakePrimary:
    label = "fake-primary"

    def __init__(self, scores=None, *, raises=False):
        self.scores = scores or {}
        self.raises = raises
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def score_for(self, proxy_name):
        if self.raises:
            raise RuntimeError("primary failed")
        return self.scores.get(proxy_name)
```

If the production implementation delegates to `RecommendProxyPolicy.shutdown()` instead of `close()`, either make the fake expose both methods or have the signal support both names.

- [ ] **Step 2: Primary score wins**

Assert that a fresh primary score is returned and the fallback callable is not invoked:

```python
signal = ProxySelectionSignal(
    primary=FakePrimary({"A": 0.8}),
    fallback_score_for=lambda name: pytest.fail("fallback should not be called"),
)

assert signal.score_for("A") == 0.8
```

- [ ] **Step 3: Missing or stale primary falls back per proxy**

Use a primary fake that returns `None` for one proxy while returning a score for another. Assert the fallback is called only for the missing proxy and that the fallback value is returned for that proxy.

- [ ] **Step 4: Exceptions fail open**

Cover both exception paths:
- primary raises, fallback returns a score
- primary raises, fallback raises, signal returns `None`

Do not assert clamping or NaN normalization here; `ProxyPool._safe_health_score()` already owns that behavior.

- [ ] **Step 5: Lifecycle and label are observable**

Assert:
- `signal.start()` delegates to the primary adapter when present.
- `signal.close()` delegates to `close()` or `shutdown()` when present.
- `signal.label` is stable and describes the active chain, for example `recommend_proxy+coordinator_health` in production and a deterministic value for injected fakes.

- [ ] **Step 6: Run the new failing tests**

```bash
pytest tests/unit/test_proxy_selection_signal.py -v
```

Expected result before implementation: import failure or test failures for missing `javdb.proxy.selection.ProxySelectionSignal`.

## Task 2: Implement `ProxySelectionSignal`

**Files:**
- Create: `javdb/proxy/selection/__init__.py`
- Create: `javdb/proxy/selection/signal.py`

- [ ] **Step 1: Add the module export**

`javdb/proxy/selection/__init__.py` should export only the public signal object:

```python
from .signal import ProxySelectionSignal

__all__ = ["ProxySelectionSignal"]
```

- [ ] **Step 2: Implement the injected constructor**

Keep the constructor narrow and test-friendly:

```python
class ProxySelectionSignal:
    def __init__(
        self,
        *,
        primary: object | None = None,
        fallback_score_for: Callable[[str], Optional[float]] | None = None,
        label: str | None = None,
    ) -> None:
        ...
```

The primary adapter only needs optional `start()`, `close()` or `shutdown()`, `score_for(proxy_name)`, and optional `label`.

- [ ] **Step 3: Implement `score_for()` as a fail-open chain**

Required order:
1. If primary exists, call `primary.score_for(proxy_name)`.
2. If the primary returns a non-`None` value, return it unchanged.
3. If the primary returns `None` or raises, call `fallback_score_for(proxy_name)` when provided.
4. If fallback returns a value, return it unchanged.
5. If fallback is missing or raises, return `None`.

Do not clamp values. Do not reject NaN. Do not convert `None` to `0.5`.

- [ ] **Step 4: Implement lifecycle**

`start()` should call `primary.start()` when present. `close()` should call `primary.close()` when present, otherwise `primary.shutdown()` when present. Both methods must fail open and log at debug/warning level rather than raising during runtime cleanup.

- [ ] **Step 5: Implement production construction**

Add `ProxySelectionSignal.from_runtime_config(...)` to create:
- `RecommendProxyClient` for `/recommend_proxy`
- `RecommendProxyPolicy` as the primary adapter
- fallback callable from the runtime `ProxyCoordinatorClient.get_proxy_health_score` when a coordinator client exists

The factory should preserve current configuration sources and defaults from `SpiderRuntime.setup_proxy_pool()`:
- endpoint URL
- HTTP timeout
- refresh interval
- cache TTL
- whether recommendation policy is enabled

If the existing config says recommendation policy is disabled or no endpoint is configured, the factory may return a signal with no primary and only fallback, or return `None` if there is no meaningful provider. Choose the smallest behavior-preserving shape and document it in a code comment if non-obvious.

- [ ] **Step 6: Run selection signal tests**

```bash
pytest tests/unit/test_proxy_selection_signal.py -v
```

## Task 3: Move Runtime Lifecycle Ownership To `RuntimeServices`

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `tests/unit/test_spider_runtime_context.py`

- [ ] **Step 1: Rename the service field**

Replace `RuntimeServices.recommend_proxy_policy` with `RuntimeServices.proxy_selection_signal`.

Keep the old field only if tests or external callers require a temporary compatibility alias. If a compatibility alias is needed, mark it internal and route it to the new field so there is one lifecycle owner.

- [ ] **Step 2: Update runtime close behavior**

`SpiderRuntime.close()` should close `proxy_selection_signal` if present. Prefer `close()` over `shutdown()` at the runtime boundary.

- [ ] **Step 3: Update runtime tests**

Add or update tests to assert that:
- `SpiderRuntime.services.proxy_selection_signal` is set when recommendation/fallback health provider wiring is active.
- `SpiderRuntime.close()` closes the signal exactly once.
- `SpiderRuntime.close()` remains safe when the signal is absent or its close path fails open.

- [ ] **Step 4: Run runtime tests**

```bash
pytest tests/unit/test_spider_runtime_context.py -v
```

## Task 4: Replace Direct Proxy-Pool Wiring In Runtime Setup

**Files:**
- Modify: `javdb/spider/runtime/context.py`
- Modify: `tests/unit/test_spider_runtime_context.py`
- Modify if needed: `tests/unit/test_recommend_proxy_policy.py`
- Modify if needed: `tests/unit/test_proxy_pool.py`

- [ ] **Step 1: Replace direct policy construction**

In `SpiderRuntime.setup_proxy_pool()`, replace inline construction of `RecommendProxyClient` and `RecommendProxyPolicy` with:

```python
signal = ProxySelectionSignal.from_runtime_config(...)
signal.start()
pool.set_health_provider(signal.score_for)
self.services.proxy_selection_signal = signal
```

The public `ProxyPool` calls remain unchanged.

- [ ] **Step 2: Remove runtime-level fallback branching**

Fallback from `/recommend_proxy` to `ProxyCoordinatorClient.get_proxy_health_score` should live inside `ProxySelectionSignal`, not in `SpiderRuntime.setup_proxy_pool()`.

- [ ] **Step 3: Keep behavior-compatible disabled states**

When no recommendation endpoint and no coordinator fallback are available, do not install a health provider. Preserve current neutral selection behavior.

- [ ] **Step 4: Avoid duplicate shutdown registration**

If `SpiderRuntime.close()` now owns lifecycle, do not add a second runtime-level `atexit.register(policy.shutdown)` path. Keep atexit only where legacy global state still needs it.

- [ ] **Step 5: Run related tests**

```bash
pytest tests/unit/test_spider_runtime_context.py -v
pytest tests/unit/test_recommend_proxy_policy.py tests/unit/test_proxy_pool.py -v
```

## Task 5: Preserve Legacy Global Proxy Setup

**Files:**
- Modify: `javdb/spider/runtime/state.py`
- Modify if needed: `tests/unit/test_spider_runtime_context.py`

- [ ] **Step 1: Inspect `_setup_proxy_pool_legacy()`**

Confirm whether the legacy path still installs `RecommendProxyPolicy.score_for` directly on the global pool.

- [ ] **Step 2: Route through the new signal when practical**

If the legacy path still has enough context to build the same adapter chain, create a `ProxySelectionSignal` there too and install `signal.score_for`.

- [ ] **Step 3: Preserve safe shutdown for global state**

If the legacy path owns a module-global signal, register its `close()` with atexit or keep the existing cleanup path adapted to the new object. Do not leak a thread from `RecommendProxyPolicy`.

- [ ] **Step 4: Keep compatibility explicit**

If `_setup_proxy_pool_legacy()` cannot cleanly use the new signal without broad state changes, leave the direct policy wiring in place and add a short TODO that points to this IMP. Do not block Phase 4 runtime deepening on a large legacy cleanup.

## Task 6: Documentation And Workflow Review

**Files:**
- Modify if needed: `CONTEXT.md`
- Modify if needed: docs under `docs/handbook/{en,zh}/`
- Review: `.github/workflows/`

- [ ] **Step 1: Confirm domain language**

Ensure `CONTEXT.md` defines Selection Signal as runner-local score-source vocabulary and states that `ProxyPool` remains the selector.

- [ ] **Step 2: Review workflow impact**

Because this phase touches Python runtime wiring but not CLI flags, workflow inputs, secrets, or job topology, no workflow edit is expected. Still inspect `.github/workflows/` references to proxy recommendation or proxy coordinator before closing the task.

Suggested check:

```bash
rg -n "recommend_proxy|ProxyCoordinator|PROXY_COORDINATOR|proxy" .github/workflows
```

- [ ] **Step 3: Review handbook impact**

No user-facing usage change is expected. If implementation changes logs, config names, env vars, CLI behavior, or operator troubleshooting steps, update paired English and Chinese handbook docs in the same change.

## Task 7: Final Verification

- [ ] **Step 1: Run targeted unit tests**

```bash
pytest tests/unit/test_proxy_selection_signal.py -v
pytest tests/unit/test_spider_runtime_context.py -v
pytest tests/unit/test_recommend_proxy_policy.py tests/unit/test_proxy_pool.py -v
```

- [ ] **Step 2: Run import and terminology checks**

```bash
rg -n "recommend_proxy_policy|proxy_selection_signal|ProxySelectionSignal|Selection Signal" \
  javdb/proxy javdb/spider/runtime tests/unit docs/design/ADR-023-Proxy-Recommendation-Policy CONTEXT.md
```

- [ ] **Step 3: Run whitespace validation**

```bash
git -c filter.lfs.required=false diff --check
```

## Acceptance Criteria

- `javdb.proxy.selection.ProxySelectionSignal` exists and is unit-tested.
- `ProxySelectionSignal.score_for()` implements fresh-primary, per-proxy fallback, and fail-open behavior.
- `ProxyPool` public interface and selection math are unchanged.
- Python continues to consume the existing recommendation `score`, not ADR-023 shadow or rank fields.
- `SpiderRuntime.services.proxy_selection_signal` owns lifecycle and runtime close cleanup.
- Legacy global proxy setup remains behavior-compatible.
- Targeted tests and `git -c filter.lfs.required=false diff --check` pass.
