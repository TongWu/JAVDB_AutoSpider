# D1 Transport Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-056](ADR-056-d1-transport-circuit-breaker.md) (the approved design), [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.md) (motivating failure), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md) (D1 Access Port)

**Goal:** Make a transient Cloudflare D1 outage pause-and-resume the pipeline instead of crashing it, by adding a per-D1-database circuit breaker at the transport layer that fails fast (→ existing rollback) only after a bounded, genuinely-sustained outage.

**Architecture:** A per-D1-database `D1CircuitBreaker` registry (keyed by endpoint URL; `threading.Lock` + `Condition`) is consulted by `D1AccessPort._post_with_retry`. Sustained transient 5xx against one database (across all its thread-local ports) trip that database's breaker to OPEN; its callers block while one elected prober health-polls `SELECT 1` on the **same** endpoint; recovery closes it and wakes everyone; exceeding the max-open window raises a terminal `D1CircuitOpenError` that propagates to the existing crash → `cleanup-on-failure` rollback path.

**Tech Stack:** Python 3.11, `threading`, `requests`, `pytest`. Pure-Python transport layer — no Rust needed. (If `pytest` import fails because the repo `.venv` is broken, run with the anaconda interpreter + `PYTHONPATH=javdb/rust_core/python` per the local test runbook; Rust is NOT required for these tests.)

---

## Design Decisions Reference (from ADR-056)

- **D1** Per-D1-database breaker registry keyed by endpoint URL in the transport layer (connections are thread-local → per-port state can't coordinate; a single global object can't tell history-down from reports-up).
- **D2** Consecutive-failure trip: shared counter `++` on transient 5xx, reset on any success, trip at `D1_BREAKER_TRIP_THRESHOLD` (3).
- **D3** Inline prober: first thread to observe OPEN probes `SELECT 1` every `D1_BREAKER_PROBE_INTERVAL_SEC` (5s); after `D1_BREAKER_HALF_OPEN_SUCCESSES` (1) → CLOSED + `notify_all`.
- **D4** Bounded OPEN: `D1_BREAKER_MAX_OPEN_SEC` (900) from `opened_at` → TERMINAL → `D1CircuitOpenError` → fail-fast + rollback.
- **D5** `D1CircuitOpenError(D1Error)` is **not** `D1TransientError` → recovery-outbox handler skips it → does not worsen BFR-020.
- **D6** Inner retry retained; only tweak = longer backoff cap for `code 7500`.
- **D7** `D1_CIRCUIT_BREAKER_ENABLED` defaults `true`, read at **construction time** (not import time); inert under non-D1 backend / no 5xx.
- **D8** Probe uses a `_probe_d1` bypass that skips `breaker.acquire()` AND applies `_post`'s success check (HTTP 200 + JSON `success == true`).

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `javdb/storage/d1_client.py` | D1 error taxonomy + env constants | Modify — add `D1CircuitOpenError`, breaker env constants |
| `javdb/storage/d1_circuit_breaker.py` | Per-DB breaker registry + state machine + metrics aggregation | **Create** |
| `javdb/storage/d1_port.py` | Transport: integrate breaker into `_post_with_retry`, add `_probe_d1`, 7500 backoff tweak, summary metrics | Modify |
| `tests/unit/test_d1_circuit_breaker.py` | State-machine + thread-safety unit tests | **Create** |
| `tests/unit/test_d1_port_circuit_breaker.py` | Port-level integration + regression (flag-off) | **Create** |
| `.github/workflows/*.yml` (all D1 workflows) | Wire `D1_CIRCUIT_BREAKER_ENABLED` (+ recovery-context `D1_BREAKER_MAX_OPEN_SEC`) via `${{ vars.* }}` | Modify |
| `docs/handbook/en/ops/d1-rollback.md` + `troubleshooting.md` (+ paired `zh/`) | Document the breaker + env vars | Modify |
| `config.py.example` | Document new `D1_*` env vars (if D1 flags are listed there) | Modify (conditional) |

---

### Task 1: Add `D1CircuitOpenError` and breaker env constants

**Files:**
- Modify: `javdb/storage/d1_client.py:150-152` (after `D1PermanentError`)
- Modify: `javdb/storage/d1_client.py:67-69` (after the existing `_MAX_RETRIES` block)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_d1_circuit_breaker.py` with just this import-shape test first:

```python
from javdb.storage.d1_client import D1CircuitOpenError, D1Error, D1TransientError


def test_circuit_open_error_is_terminal_not_transient():
    """D5: must NOT be a D1TransientError, so flush()'s recovery handler skips it."""
    err = D1CircuitOpenError("D1 unavailable for 900s")
    assert isinstance(err, D1Error)
    assert not isinstance(err, D1TransientError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_d1_circuit_breaker.py::test_circuit_open_error_is_terminal_not_transient -v`
Expected: FAIL with `ImportError: cannot import name 'D1CircuitOpenError'`

- [ ] **Step 3: Add the error class**

In `javdb/storage/d1_client.py`, immediately after `class D1PermanentError` (line 150-151):

```python
class D1CircuitOpenError(D1Error):
    """Terminal: D1 stayed unavailable past the circuit breaker's max-open window.

    Deliberately NOT a D1TransientError (ADR-056 D5): D1AccessPort.flush()'s
    ``except D1TransientError`` recovery-outbox handler must not capture it, so
    the run fails fast into the existing cleanup-on-failure rollback rather than
    durably queueing writes for a later replay (which would risk BFR-020).
    """
```

- [ ] **Step 4: Add the `_env_bool` helper**

In `javdb/storage/d1_client.py`, after line 69 (`_RETRY_MAX_SLEEP_SEC = ...`):

```python


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

> **Do NOT** define the breaker knobs as import-time module constants here
> (review P2). They are read **at breaker-construction time** inside
> `get_circuit_breaker()` (Task 2), so `reset_circuit_breaker()` + a changed env
> var actually take effect — required by the flag-off regression test (Task 5) and
> by per-workflow overrides (Task 8). Defaults live in that constructor call:
> enabled `True` (D7), threshold `3`, probe interval `5.0s`, max-open `900s`,
> half-open successes `1`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_d1_circuit_breaker.py::test_circuit_open_error_is_terminal_not_transient -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/d1_client.py tests/unit/test_d1_circuit_breaker.py
git commit -m "feat(db): add D1CircuitOpenError + circuit-breaker env constants (ADR-056)"
```

---

### Task 2: Create the `D1CircuitBreaker` state machine

**Files:**
- Create: `javdb/storage/d1_circuit_breaker.py`
- Test: `tests/unit/test_d1_circuit_breaker.py`

- [ ] **Step 1: Write the failing tests (state machine, deterministic clock)**

Append to `tests/unit/test_d1_circuit_breaker.py`:

```python
import pytest

from javdb.storage.d1_circuit_breaker import D1CircuitBreaker
from javdb.storage.d1_client import D1CircuitOpenError


class _Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def _breaker(clock, **overrides):
    kwargs = dict(
        enabled=True, trip_threshold=3, probe_interval_sec=5.0,
        max_open_sec=900, half_open_successes=1,
        monotonic=clock.monotonic, sleep=clock.sleep,
    )
    kwargs.update(overrides)
    return D1CircuitBreaker(**kwargs)


def test_trips_after_threshold_consecutive_failures():
    b = _breaker(_Clock())
    b.record_failure(); b.record_failure()
    assert b.state == "closed"          # 2 < 3
    b.record_failure()
    assert b.state == "open"            # 3 == threshold


def test_success_resets_failure_counter():
    b = _breaker(_Clock())
    b.record_failure(); b.record_failure()
    b.record_success()
    b.record_failure(); b.record_failure()
    assert b.state == "closed"          # never reached 3 consecutive


def test_disabled_breaker_never_trips():
    b = _breaker(_Clock(), enabled=False)
    for _ in range(10):
        b.record_failure()
    assert b.state == "closed"


def test_probe_success_closes_and_resumes():
    clock = _Clock()
    b = _breaker(clock)
    b.record_failure(); b.record_failure(); b.record_failure()
    assert b.state == "open"
    # First thread to acquire while OPEN becomes prober; a healthy probe closes it.
    b.acquire(probe_fn=lambda: True)
    assert b.state == "closed"


def test_bounded_open_raises_terminal_after_max_open():
    clock = _Clock()
    b = _breaker(clock, max_open_sec=10, probe_interval_sec=5.0)
    b.record_failure(); b.record_failure(); b.record_failure()
    # Probe always fails; prober advances the clock via sleep() until deadline.
    with pytest.raises(D1CircuitOpenError):
        b.acquire(probe_fn=lambda: False)
    assert b.state == "terminal"


def test_closed_breaker_acquire_is_immediate():
    b = _breaker(_Clock())
    b.acquire(probe_fn=lambda: pytest.fail("probe must not run when CLOSED"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_d1_circuit_breaker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.storage.d1_circuit_breaker'`

- [ ] **Step 3: Implement the breaker module**

Create `javdb/storage/d1_circuit_breaker.py`:

```python
"""Per-D1-database circuit breaker for the transport layer (ADR-056).

Connections are thread-local (javdb/storage/db/_db_connection.py), so breakers
are module-level singletons keyed by D1 endpoint URL (one per database), shared
by every D1AccessPort for that database across all worker threads. Each breaker
coordinates one pause/probe/resume cycle for its database during a transient
Cloudflare D1 outage, and fails fast after a bounded max-open window. Keying by
endpoint ensures a history outage is never closed by a healthy reports probe.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from javdb.infra.logging import get_logger
from javdb.storage.d1_client import (
    D1CircuitOpenError,
    _env_bool,
    _env_float,
    _env_int,
)

logger = get_logger(__name__)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"
TERMINAL = "terminal"


class D1CircuitBreaker:
    def __init__(
        self,
        *,
        enabled: bool,
        trip_threshold: int,
        probe_interval_sec: float,
        max_open_sec: int,
        half_open_successes: int,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._enabled = enabled
        self._trip_threshold = max(1, trip_threshold)
        self._probe_interval_sec = probe_interval_sec
        self._max_open_sec = max_open_sec
        self._half_open_successes = max(1, half_open_successes)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._prober_active = False
        self._half_open_ok = 0
        self._metrics = {
            "trips": 0,
            "open_seconds_total": 0.0,
            "probes": 0,
            "resumes": 0,
            "terminal_opens": 0,
        }

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def enabled(self) -> bool:
        return self._enabled

    def metrics_snapshot(self) -> dict:
        with self._lock:
            return dict(self._metrics)

    # ── failure / success accounting ──────────────────────────────────────
    def record_failure(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            if self._state in (OPEN, TERMINAL):
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._trip_threshold:
                self._trip_locked()

    def record_success(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._consecutive_failures = 0
            if self._state == HALF_OPEN:
                self._half_open_ok += 1
                if self._half_open_ok >= self._half_open_successes:
                    self._close_locked()

    # ── gate consulted before every POST ──────────────────────────────────
    def acquire(self, probe_fn: Callable[[], bool]) -> None:
        if not self._enabled:
            return
        with self._lock:
            if self._state == CLOSED:
                return
            if self._state == TERMINAL:
                raise D1CircuitOpenError(self._terminal_message())
            become_prober = not self._prober_active
            if become_prober:
                self._prober_active = True
        if become_prober:
            self._run_probe_loop(probe_fn)
            with self._lock:
                if self._state == TERMINAL:
                    raise D1CircuitOpenError(self._terminal_message())
            return
        # Waiter: block until the prober closes the breaker or declares terminal.
        with self._lock:
            while self._state != CLOSED:
                if self._state == TERMINAL:
                    raise D1CircuitOpenError(self._terminal_message())
                self._cond.wait(timeout=self._probe_interval_sec)

    # ── prober loop (runs OUTSIDE the lock while sleeping / probing) ───────
    def _run_probe_loop(self, probe_fn: Callable[[], bool]) -> None:
        while True:
            with self._lock:
                if self._monotonic() - self._opened_at >= self._max_open_sec:
                    self._to_terminal_locked()
                    return
            self._sleep(self._probe_interval_sec)
            try:
                ok = bool(probe_fn())
            except Exception:  # noqa: BLE001 — any probe error == still down
                ok = False
            with self._lock:
                self._metrics["probes"] += 1
                if ok:
                    if self._state == OPEN:
                        self._state = HALF_OPEN
                        self._half_open_ok = 0
                    self._half_open_ok += 1
                    if self._half_open_ok >= self._half_open_successes:
                        self._close_locked()
                        return
                elif self._state == HALF_OPEN:
                    self._state = OPEN
                    self._half_open_ok = 0

    # ── locked transitions (caller holds self._lock) ──────────────────────
    def _trip_locked(self) -> None:
        self._state = OPEN
        self._opened_at = self._monotonic()
        self._half_open_ok = 0
        self._metrics["trips"] += 1
        logger.warning(
            "D1 circuit OPEN after %d consecutive 5xx — pausing all D1 access; "
            "probing every %.1fs; will fail run if not recovered within %ds",
            self._consecutive_failures, self._probe_interval_sec,
            self._max_open_sec,
        )

    def _close_locked(self) -> None:
        open_for = self._monotonic() - self._opened_at
        self._metrics["open_seconds_total"] += open_for
        self._metrics["resumes"] += 1
        self._state = CLOSED
        self._consecutive_failures = 0
        self._half_open_ok = 0
        self._prober_active = False
        self._cond.notify_all()
        logger.info("D1 recovered after %.1fs — resuming", open_for)

    def _to_terminal_locked(self) -> None:
        self._metrics["open_seconds_total"] += self._monotonic() - self._opened_at
        self._metrics["terminal_opens"] += 1
        self._state = TERMINAL
        self._prober_active = False
        self._cond.notify_all()
        logger.error(
            "D1 unavailable for %ds — failing run for rollback (ADR-056)",
            self._max_open_sec,
        )

    def _terminal_message(self) -> str:
        return (
            f"D1 circuit breaker terminal: D1 did not recover within "
            f"{self._max_open_sec}s"
        )


_REGISTRY_LOCK = threading.Lock()
_BREAKERS: dict[str, D1CircuitBreaker] = {}


def get_circuit_breaker(key: str = "default") -> D1CircuitBreaker:
    """Return the breaker for D1 endpoint *key* (one per D1 database).

    Keyed by the port's endpoint URL so a history-DB outage is never declared
    recovered by a healthy reports-DB probe (ADR-056 D1). Env vars are read HERE,
    at construction time, so reset_circuit_breaker() + a changed env take effect.
    """
    breaker = _BREAKERS.get(key)
    if breaker is None:
        with _REGISTRY_LOCK:
            breaker = _BREAKERS.get(key)
            if breaker is None:
                breaker = D1CircuitBreaker(
                    enabled=_env_bool("D1_CIRCUIT_BREAKER_ENABLED", True),
                    trip_threshold=_env_int("D1_BREAKER_TRIP_THRESHOLD", 3),
                    probe_interval_sec=_env_float(
                        "D1_BREAKER_PROBE_INTERVAL_SEC", 5.0
                    ),
                    max_open_sec=_env_int("D1_BREAKER_MAX_OPEN_SEC", 900),
                    half_open_successes=_env_int(
                        "D1_BREAKER_HALF_OPEN_SUCCESSES", 1
                    ),
                )
                _BREAKERS[key] = breaker
    return breaker


def reset_circuit_breaker() -> None:
    """Test-only: drop all per-endpoint breakers so the next get_* rebuilds from env."""
    with _REGISTRY_LOCK:
        _BREAKERS.clear()


def aggregate_metrics() -> dict:
    """Sum breaker metrics across all per-endpoint breakers (for the summary)."""
    with _REGISTRY_LOCK:
        breakers = list(_BREAKERS.values())
    agg = {
        "trips": 0,
        "open_seconds_total": 0.0,
        "probes": 0,
        "resumes": 0,
        "terminal_opens": 0,
    }
    for breaker in breakers:
        snapshot = breaker.metrics_snapshot()
        for metric_key in agg:
            agg[metric_key] += snapshot.get(metric_key, 0)
    return agg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_d1_circuit_breaker.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/d1_circuit_breaker.py tests/unit/test_d1_circuit_breaker.py
git commit -m "feat(db): add D1CircuitBreaker transport state machine (ADR-056)"
```

---

### Task 3: Add a concurrency test (one prober, others wait, all resume)

**Files:**
- Test: `tests/unit/test_d1_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_d1_circuit_breaker.py`:

```python
import threading as _threading


def test_concurrent_acquire_elects_single_prober_then_all_resume():
    # Real clock here (we exercise threads, not the deterministic clock).
    b = D1CircuitBreaker(
        enabled=True, trip_threshold=1, probe_interval_sec=0.01,
        max_open_sec=60, half_open_successes=1,
    )
    probe_calls = {"n": 0}
    probe_gate = _threading.Event()

    def probe():
        probe_calls["n"] += 1
        # Stay "down" until the test opens the gate, so waiters pile up.
        return probe_gate.is_set()

    b.record_failure()  # threshold=1 → OPEN immediately
    assert b.state == "open"

    resumed = []

    def worker():
        b.acquire(probe_fn=probe)
        resumed.append(_threading.get_ident())

    threads = [_threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    # Let the prober spin a few times while D1 is still "down".
    _threading.Event().wait(0.1)
    probe_gate.set()  # D1 recovers
    for t in threads:
        t.join(timeout=5)

    assert len(resumed) == 6          # everyone resumed
    assert b.state == "closed"
    # Exactly one thread probed (others waited on the Condition).
    assert b.metrics_snapshot()["trips"] == 1
    assert b.metrics_snapshot()["resumes"] == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_d1_circuit_breaker.py::test_concurrent_acquire_elects_single_prober_then_all_resume -v`
Expected: PASS (implementation from Task 2 already satisfies it). If it hangs, the prober election or `notify_all` is wrong — fix before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_d1_circuit_breaker.py
git commit -m "test(db): cover concurrent prober election for D1 circuit breaker"
```

---

### Task 4: Integrate the breaker into `D1AccessPort` + add `_probe_d1`

**Files:**
- Modify: `javdb/storage/d1_port.py:28-38` (imports), `:404-424` (`_post_with_retry`), and add `_probe_d1`
- Test: `tests/unit/test_d1_port_circuit_breaker.py`

- [ ] **Step 1: Write the failing port-level test**

Create `tests/unit/test_d1_port_circuit_breaker.py`:

```python
import pytest

from javdb.storage import d1_circuit_breaker
from javdb.storage.d1_circuit_breaker import D1CircuitBreaker
from javdb.storage.d1_port import D1AccessPort, D1PortConfig as PortConfig

_TEST_URL = "https://example/query"


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = payload or {"success": True, "result": [{"meta": {"changes": 0}, "results": [{"ok": 1}]}]}
        self.text = ""

    def json(self):
        return self._payload


def _make_port(post_request):
    return D1AccessPort(
        url=_TEST_URL,
        headers={"Authorization": "Bearer x"},
        config=PortConfig(timeout=1, batch_limit=50, max_retries=5,
                          retry_base_sec=0, retry_max_sleep_sec=0),
        post_request=post_request,
        sleep=lambda _dt: None,  # no real backoff sleep
        jitter=lambda: 0.0,
    )


def _register_breaker(**kwargs):
    """Register a deterministic breaker under the test endpoint key (_TEST_URL)."""
    breaker = D1CircuitBreaker(**kwargs)
    d1_circuit_breaker._BREAKERS[_TEST_URL] = breaker
    return breaker


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch):
    # Deterministic, fast breaker registered for the test endpoint each test.
    monkeypatch.setenv("D1_CIRCUIT_BREAKER_ENABLED", "true")
    _register_breaker(
        enabled=True, trip_threshold=2, probe_interval_sec=0,
        max_open_sec=60, half_open_successes=1,
    )
    yield
    d1_circuit_breaker.reset_circuit_breaker()


def test_breaker_trips_then_probe_recovers_and_statement_succeeds():
    calls = {"n": 0}

    def post_request(url, headers, json, timeout):
        sql = json.get("sql")
        if sql == "SELECT 1":
            return _Resp(200)               # health probe succeeds → close
        calls["n"] += 1
        if calls["n"] <= 2:
            return _Resp(500, {"success": False, "errors": [{"code": 7500, "message": "internal error"}]})
        return _Resp(200)                   # the real statement now succeeds

    port = _make_port(post_request)
    # Fixture breaker: trip_threshold=2; port max_retries=5. The first two real
    # POSTs 500 → 2 failures → breaker trips OPEN mid-retry-loop; the next
    # acquire() probes SELECT 1 (healthy) → CLOSED → the retry's POST returns 200.
    cursors = port.execute("INSERT INTO t VALUES (1)", ())
    assert cursors and cursors[0].rowcount == 0
    assert calls["n"] == 3
    assert d1_circuit_breaker.get_circuit_breaker(_TEST_URL).state == "closed"
```

> NOTE: this relies on `max_retries` (5) > `trip_threshold` (2) so the breaker
> trips *before* the retry loop exhausts — otherwise the statement would raise
> `D1TransientError` before any `acquire()` could pause and probe it. Preserve
> that invariant if you retune the fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py -v`
Expected: FAIL — `_post_with_retry` does not yet consult the breaker (no probe issued; `D1TransientError` raised).

- [ ] **Step 3: Add the breaker import**

In `javdb/storage/d1_port.py`, add the breaker import after the existing
`d1_client` import block (lines 28-38). The terminal `D1CircuitOpenError` is
**not** imported here — the breaker raises it and `d1_port` just lets it
propagate, so importing it would be an unused-import lint failure.

```python
from javdb.storage.d1_circuit_breaker import aggregate_metrics, get_circuit_breaker
```

- [ ] **Step 4: Wire the breaker into `_post_with_retry`**

Replace `_post_with_retry` (`javdb/storage/d1_port.py:404-424`) with:

```python
    def _post_with_retry(self, body: dict[str, Any]) -> list[D1Cursor]:
        breaker = get_circuit_breaker(self._url)  # one breaker per D1 database
        last_exc: D1TransientError | None = None
        attempts = max(1, self._config.max_retries)
        for attempt in range(attempts):
            # Pause here if the breaker is OPEN; raise D1CircuitOpenError if the
            # outage outlasts the bounded max-open window (ADR-056 D4).
            breaker.acquire(self._probe_d1)
            try:
                result = self._post(body)
                breaker.record_success()
                if attempt > 0:
                    self._summary["retry_successes"] += 1
                return result
            except D1PermanentError:
                self._summary["permanent_errors"] += 1
                raise
            except D1TransientError as exc:
                self._summary["transient_errors"] += 1
                breaker.record_failure()
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                self._summary["retries"] += 1
                self._sleep(self._compute_backoff(attempt, exc))
        assert last_exc is not None
        raise last_exc
```

- [ ] **Step 5: Add the `_probe_d1` bypass method**

Add this method to `D1AccessPort` (place it right after `_post_with_retry`):

```python
    def _probe_d1(self) -> bool:
        """Health probe used by the circuit breaker (ADR-056 D8).

        Issues a bare ``SELECT 1`` directly via the transport, bypassing
        ``_post_with_retry`` / ``breaker.acquire`` so it cannot deadlock on the
        open breaker. Applies the SAME success criterion as ``_post`` — HTTP 200
        AND a JSON body with ``success == true`` — so an application-level D1
        fault (HTTP 200 + ``success=false``) does NOT falsely declare recovery
        and wake the fleet into renewed failures.
        """
        try:
            response = self._post_request(
                self._url,
                headers=self._headers,
                json={"sql": "SELECT 1", "params": []},
                timeout=self._config.timeout,
            )
            if getattr(response, "status_code", None) != 200:
                return False
            payload = response.json()
        except Exception:  # noqa: BLE001 — any error (incl. non-JSON) == still down
            return False
        return bool(isinstance(payload, dict) and payload.get("success"))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add javdb/storage/d1_port.py tests/unit/test_d1_port_circuit_breaker.py
git commit -m "feat(db): consult D1 circuit breaker in _post_with_retry (ADR-056)"
```

---

### Task 5: Port-level trip + terminal + flag-off regression

**Files:**
- Test: `tests/unit/test_d1_port_circuit_breaker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_d1_port_circuit_breaker.py`:

```python
from javdb.storage.d1_client import D1CircuitOpenError, D1TransientError


def test_terminal_open_raises_circuit_open_error():
    # Breaker that goes terminal immediately (max_open_sec=0).
    _register_breaker(
        enabled=True, trip_threshold=1, probe_interval_sec=0,
        max_open_sec=0, half_open_successes=1,
    )

    def post_request(url, headers, json, timeout):
        return _Resp(500, {"success": False, "errors": [{"code": 7500, "message": "internal error"}]})

    port = _make_port(post_request)
    with pytest.raises(D1CircuitOpenError):
        port.execute("INSERT INTO t VALUES (1)", ())


def test_circuit_open_error_not_caught_by_recovery_handler():
    """D5: D1CircuitOpenError must not be a D1TransientError."""
    assert not issubclass(D1CircuitOpenError, D1TransientError)


def test_flag_off_is_identical_to_today(monkeypatch):
    # Breaker disabled via env → rebuilt fresh (env read at construction time, not
    # import time) → a transient 500 burst exhausts retries and raises
    # D1TransientError exactly as before the breaker existed (regression gate).
    monkeypatch.setenv("D1_CIRCUIT_BREAKER_ENABLED", "false")
    d1_circuit_breaker.reset_circuit_breaker()  # drop the fixture's enabled breaker

    def post_request(url, headers, json, timeout):
        return _Resp(500, {"success": False, "errors": [{"code": 7500, "message": "internal error"}]})

    port = _make_port(post_request)
    with pytest.raises(D1TransientError):
        port.execute("INSERT INTO t VALUES (1)", ())
    breaker = d1_circuit_breaker.get_circuit_breaker(_TEST_URL)
    assert breaker.enabled is False                     # construction-time env read honoured
    assert breaker.metrics_snapshot()["probes"] == 0    # never probed when disabled
```

- [ ] **Step 2: Run tests to verify behavior**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py -v`
Expected: PASS (all). `test_flag_off_is_identical_to_today` is the **regression gate** proving the breaker is a no-op when disabled.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_d1_port_circuit_breaker.py
git commit -m "test(db): cover D1 breaker terminal path + flag-off regression"
```

---

### Task 6: Longer backoff cap for `code 7500` internal errors (D6)

**Files:**
- Modify: `javdb/storage/d1_port.py:488-508` (`_compute_backoff`)
- Test: `tests/unit/test_d1_port_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_d1_port_circuit_breaker.py`:

```python
def test_internal_error_7500_gets_longer_backoff_floor():
    port = _make_port(lambda *a, **k: _Resp(200))
    exc = D1TransientError("D1 API returned HTTP 500: ... internal error ...")
    exc.is_internal_error = True
    # With base/jitter zeroed in the fixture config, a plain transient would be
    # ~0s on attempt 0; the 7500 floor must lift it to a non-trivial wait.
    delay = port._compute_backoff(0, exc)
    assert delay >= 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py::test_internal_error_7500_gets_longer_backoff_floor -v`
Expected: FAIL (`AttributeError: is_internal_error` or `delay` near 0).

- [ ] **Step 3: Tag 7500 internal errors at raise time**

In `javdb/storage/d1_client.py`, add the attribute to `D1TransientError` (after line 147 `retry_after`):

```python
    is_internal_error: bool = False
```

In `javdb/storage/d1_port.py` `_post` (the `if status == 429 or 500 <= status < 600:` block at line 441-448), set the flag when the body carries code 7500 / "internal error":

```python
        if status == 429 or 500 <= status < 600:
            err = D1TransientError(
                f"D1 API returned HTTP {status}: {response.text[:500]}"
            )
            if "internal error" in (response.text or "").lower():
                err.is_internal_error = True
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                err.retry_after = retry_after
            raise err
```

- [ ] **Step 4: Apply the longer floor in `_compute_backoff`**

In `javdb/storage/d1_port.py` `_compute_backoff` (lines 505-508), add an internal-error floor next to the export-lock floor:

```python
        base = self._config.retry_base_sec * (2**attempt)
        if exc.is_export_lock:
            base = max(base, _EXPORT_LOCK_BACKOFF_FLOOR_SEC)
        if getattr(exc, "is_internal_error", False):
            base = max(base, _INTERNAL_ERROR_BACKOFF_FLOOR_SEC)
        return min(base, self._config.retry_max_sleep_sec) + self._jitter()
```

Add the constant import to the existing `d1_client` import block in `d1_port.py` and define it in `d1_client.py` after `_EXPORT_LOCK_BACKOFF_FLOOR_SEC` (line 130):

```python
# d1_client.py
_INTERNAL_ERROR_BACKOFF_FLOOR_SEC = _env_float("D1_INTERNAL_ERROR_FLOOR_SEC", 2.0)
```

> NOTE: the `min(base, retry_max_sleep_sec)` cap still applies, so the fixture's `retry_max_sleep_sec=0` would clamp the floor to 0. Set `retry_max_sleep_sec=30` in this one test's port (construct a local `PortConfig`) so the floor is observable; keep base/jitter at 0.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py::test_internal_error_7500_gets_longer_backoff_floor -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/d1_client.py javdb/storage/d1_port.py tests/unit/test_d1_port_circuit_breaker.py
git commit -m "feat(db): longer backoff floor for D1 7500 internal errors (ADR-056)"
```

---

### Task 7: Surface breaker metrics in `d1_port_summary.json`

**Files:**
- Modify: `javdb/storage/d1_port.py:373-393` (`write_summary`)
- Test: `tests/unit/test_d1_port_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_d1_port_circuit_breaker.py`:

```python
import json as _json


def test_summary_includes_circuit_breaker_section(tmp_path):
    breaker = _register_breaker(
        enabled=True, trip_threshold=1, probe_interval_sec=0,
        max_open_sec=60, half_open_successes=1,
    )
    breaker.record_failure()  # threshold=1 → trips → 1 trip recorded
    port = _make_port(lambda *a, **k: _Resp(200))
    target = tmp_path / "d1_port_summary.json"
    port.write_summary(target)
    payload = _json.loads(target.read_text())
    assert "circuit_breaker" in payload
    assert payload["circuit_breaker"]["trips"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py::test_summary_includes_circuit_breaker_section -v`
Expected: FAIL — no `circuit_breaker` key.

- [ ] **Step 3: Merge the breaker snapshot into the summary**

In `javdb/storage/d1_port.py` `write_summary`, after `aggregate = _with_derived_summary(aggregate)` and before `target.write_text(...)`:

```python
            # Circuit breakers are per-D1-database and NOT delta-aggregated;
            # write the absolute SUM across all endpoints (ADR-056). Different
            # logical DBs share this one summary file, so a per-endpoint snapshot
            # would clobber — aggregate_metrics() sums the registry instead.
            aggregate["circuit_breaker"] = aggregate_metrics()
```

> NOTE: `circuit_breaker` is a nested object, not a counter — do NOT add it to `_SUMMARY_COUNTER_KEYS` (those are delta-aggregated and would corrupt a nested dict).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_d1_port_circuit_breaker.py::test_summary_includes_circuit_breaker_section -v`
Expected: PASS

- [ ] **Step 5: Run the full breaker suite**

Run: `python3 -m pytest tests/unit/test_d1_circuit_breaker.py tests/unit/test_d1_port_circuit_breaker.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/d1_port.py tests/unit/test_d1_port_circuit_breaker.py
git commit -m "feat(db): emit circuit-breaker metrics into d1_port_summary (ADR-056)"
```

---

### Task 8: Wire `D1_CIRCUIT_BREAKER_ENABLED` into ALL D1 workflows

Because the breaker defaults ON in code (D7), a repo var only takes effect where it
is actually passed into the job `env:`. **Every** workflow that touches D1 must pass
it — otherwise the "disable fleet-wide" lever is a no-op and recovery jobs can hang
the full 15 min (review P2).

**Files:** every workflow under `.github/workflows/` with a D1-aware job `env:` block
— at minimum `DailyIngestion.yml`, `AdHocIngestion.yml`, `RollbackD1.yml`,
`StaleSessionCleanup.yml`, `WeeklyDedup.yml` (confirm the full set in Step 1; also
check `Migration.yml`, `TestIngestion.yml`, `QBFileFilter.yml`).

- [ ] **Step 1: Enumerate the D1 workflows to edit**

Run: `grep -rl "D1_RECOVERY_OUTBOX_ENABLED\|STORAGE_BACKEND" .github/workflows/`
These are the workflows with D1-aware job `env:` blocks — edit every one.

- [ ] **Step 2: Add the enable var beneath every existing D1-flag block**

In each file, for every job `env:` block containing
`D1_RECOVERY_OUTBOX_ENABLED: ${{ vars.D1_RECOVERY_OUTBOX_ENABLED || 'false' }}`,
add directly beneath it:

```yaml
      D1_CIRCUIT_BREAKER_ENABLED: ${{ vars.D1_CIRCUIT_BREAKER_ENABLED || 'true' }}
```

Default `'true'` matches the code default; a repo `vars.D1_CIRCUIT_BREAKER_ENABLED`
set to `false` then disables the breaker everywhere at once.

- [ ] **Step 3: Shorten the breaker wait for recovery / cleanup contexts**

Rollback and cleanup jobs must NOT block for the full 15 min when D1 is down — they
exist to recover from a failure, so a failed run should surface quickly. In these
job `env:` blocks specifically, also add a short max-open override:
- `RollbackD1.yml` (all jobs)
- `StaleSessionCleanup.yml` (all jobs)
- `DailyIngestion.yml` → the `cleanup-on-failure` job only

```yaml
      D1_BREAKER_MAX_OPEN_SEC: ${{ vars.D1_BREAKER_MAX_OPEN_SEC_RECOVERY || '120' }}
```

(Leave ingestion jobs on the 900s default — they have 6h headroom; recovery jobs cap
at ~2 min.)

- [ ] **Step 4: Verify every touched YAML is well-formed**

Run:
```bash
for f in $(grep -rl "D1_CIRCUIT_BREAKER_ENABLED" .github/workflows/); do
  python3 -c "import yaml; yaml.safe_load(open('$f')); print('ok', '$f')"
done
```
Expected: `ok <file>` for each.

- [ ] **Step 5: Confirm parity — enable var present wherever the recovery flag is**

Run:
```bash
for f in $(grep -rl "D1_RECOVERY_OUTBOX_ENABLED" .github/workflows/); do
  a=$(grep -c "D1_RECOVERY_OUTBOX_ENABLED" "$f"); b=$(grep -c "D1_CIRCUIT_BREAKER_ENABLED" "$f")
  echo "$f recovery=$a breaker=$b"; [ "$a" = "$b" ] || echo "  MISMATCH in $f"
done
```
Expected: `breaker` count == `recovery` count in every file; no `MISMATCH` line.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/
git commit -m "ci(workflows): enable D1 circuit breaker across all D1 workflows (ADR-056)"
```

---

### Task 9: Documentation (handbook en + zh, config example)

**Files:**
- Modify: `docs/handbook/en/ops/d1-rollback.md` + `docs/handbook/zh/ops/d1-rollback.md`
- Modify: `docs/handbook/en/self-hoster/configuration.md` + `docs/handbook/zh/self-hoster/configuration.md`
- Modify (conditional): `config.py.example`

- [ ] **Step 1: Document the breaker + env vars (EN)**

In `docs/handbook/en/ops/d1-rollback.md`, add a short "Transient D1 outage handling (circuit breaker)" subsection: what trips it, the pause/probe/resume behaviour, that a sustained outage past `D1_BREAKER_MAX_OPEN_SEC` fails fast into the normal rollback, and the env-var table:

| Env var | Default | Meaning |
| --- | --- | --- |
| `D1_CIRCUIT_BREAKER_ENABLED` | `true` | Master switch |
| `D1_BREAKER_TRIP_THRESHOLD` | `3` | Consecutive transient 5xx to trip |
| `D1_BREAKER_PROBE_INTERVAL_SEC` | `5.0` | `SELECT 1` probe interval |
| `D1_BREAKER_MAX_OPEN_SEC` | `900` | Max OPEN before fail-fast (15 min) |
| `D1_BREAKER_HALF_OPEN_SUCCESSES` | `1` | Probes needed to close |
| `D1_INTERNAL_ERROR_FLOOR_SEC` | `2.0` | Inner-retry backoff floor for code 7500 |

Cross-reference [ADR-056] and [BFR-020]. Add the same env-var rows to `docs/handbook/en/self-hoster/configuration.md` next to the existing `D1_*` documentation.

- [ ] **Step 2: Mirror to ZH**

Translate the prose in the paired `docs/handbook/zh/ops/d1-rollback.md` and `docs/handbook/zh/self-hoster/configuration.md`. Keep env-var names, defaults, and the table structure verbatim (code/identifiers are never translated).

- [ ] **Step 3: Update `config.py.example` if it lists D1 flags**

Run: `grep -n "D1_RECOVERY_OUTBOX_ENABLED\|D1_BATCHING_ENABLED" config.py.example`
- If matches: add commented `D1_CIRCUIT_BREAKER_ENABLED` (+ the other knobs) in the same style.
- If no matches: skip — these are runtime env vars, not `config.py` keys.

- [ ] **Step 4: Verify en/zh pairing has no drift**

Run: `diff <(grep -oE "D1_[A-Z_]+" docs/handbook/en/ops/d1-rollback.md | sort -u) <(grep -oE "D1_[A-Z_]+" docs/handbook/zh/ops/d1-rollback.md | sort -u)`
Expected: no differences (same env-var set in both languages).

- [ ] **Step 5: Commit**

```bash
git add docs/handbook/en docs/handbook/zh config.py.example
git commit -m "docs(ops): document D1 circuit breaker env vars (ADR-056)"
```

---

### Task 10: Risk verification gates (R1/R2) + full-suite gate

**Files:** none (verification only); record findings in the ADR Status Log if action is needed.

- [ ] **Step 1: R1 — does the spider tolerate resume after a long pause? (login/session)**

Read `javdb/spider/runtime/` + `javdb/spider/auth/` for how `JAVDB_SESSION_COOKIE` / index login state is validated per request vs. cached at startup. Determine whether a 1–15 min mid-run pause (workers blocked inside a D1 call) can outlive the login state.
- If login is re-validated lazily on the next fetch → **no action**; note "R1 verified: lazy re-validation" in the ADR Status Log.
- If login is cached and would go stale → file a follow-up (deferred per ADR roadmap; do NOT block this IMP) and note it.

- [ ] **Step 2: R2 — MovieClaim / WorkDistributor lease expiry during a pause**

Read `javdb/proxy/coordinator/` (or the MovieClaim/WorkDistributor client) for lease TTLs. Confirm a held lease either auto-renews or is re-acquired on resume, or that expiry is harmless (claim is best-effort / fail-open — see `runner.py` `_release_movie_claim` "never breaks the run").
- Record the finding (verified-safe / follow-up) in the ADR Status Log.

- [ ] **Step 3: Full unit suite (no regressions)**

Run: `python3 -m pytest tests/unit/ -q`
Expected: no NEW failures vs. the known env-baseline (per project memory, ~57 pre-existing env-related failures are the baseline; the breaker tests must all pass and nothing else may newly break).

- [ ] **Step 4: Mark ADR accepted + update Status Log**

In `docs/design/ADR-056-D1-Transport-Circuit-Breaker/ADR-056-d1-transport-circuit-breaker.md` (and `.zh.md`), set `Status: Accepted`, append a Status Log line dated today, and check off the Phase-1 roadmap row. Commit:

```bash
git add docs/design/ADR-056-D1-Transport-Circuit-Breaker
git commit -m "docs(adr-056): record R1/R2 verification + mark Accepted"
```

---

## Self-Review

- **Spec coverage:** D1 — per-DB registry (Task 2 `get_circuit_breaker(key)` + Task 4 `self._url` key); D2 (Task 2 `record_failure`/trip; Task 3 concurrency); D3 (Task 2 `_run_probe_loop`/prober election); D4 (Task 2 terminal + Task 5 port terminal); D5 (Task 1 error class + Task 5 `not issubclass`); D6 (Task 6); D7 — default `true` read at construction (Task 2 `get_circuit_breaker`) + flag-off proven (Task 5 asserts `enabled is False`) + all-workflows default (Task 8); D8 — `_probe_d1` bypass + body-success check (Task 4). Observability across per-DB breakers → Task 7 (`aggregate_metrics`). Docs → Task 9. Risks R1/R2 + regression → Task 10. No gaps. Review fixes (per-DB probe binding, probe body check, all-workflow wiring, construction-time env) are folded into Tasks 2/4/5/7/8.
- **Placeholder scan:** every code step shows full code; commands have expected output. The two `NOTE` blocks (Task 4 timing, Task 6 cap clamp) flag known interactions and give concrete adjustments, not deferred work.
- **Type/name consistency:** `get_circuit_breaker` / `reset_circuit_breaker` / `record_failure` / `record_success` / `acquire(probe_fn)` / `_run_probe_loop` / `metrics_snapshot` / `_probe_d1` / `D1CircuitOpenError` / `is_internal_error` / `_INTERNAL_ERROR_BACKOFF_FLOOR_SEC` — used consistently across Tasks 1-10. States `closed/open/half_open/terminal` match the tests in Tasks 2-5.
