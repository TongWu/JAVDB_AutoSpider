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
        self._terminal_at = 0.0
        self._prober_active = False
        self._half_open_ok = 0
        self._metrics = {
            "trips": 0,
            "open_seconds_total": 0.0,
            "probes": 0,
            "resumes": 0,
            "terminal_opens": 0,
            "rearms": 0,
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
                # A one-shot run (spider / CLI / Actions) crashes on this raise and
                # exits, so TERMINAL is effectively permanent there — the intended
                # fail-fast. But a long-lived process (e.g. apps/api on D1) survives
                # the raise and would otherwise reject this D1 database forever from
                # its cached breaker. Re-arm after a max_open_sec cooldown so a
                # recovered D1 is retried; the cooldown never elapses within a
                # crashing run, so fail-fast still holds there (ADR-056 D4).
                if self._monotonic() - self._terminal_at > self._max_open_sec:
                    self._rearm_locked()
                    return
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
        self._terminal_at = self._monotonic()
        self._prober_active = False
        self._cond.notify_all()
        logger.error(
            "D1 unavailable for %ds — failing run for rollback (ADR-056)",
            self._max_open_sec,
        )

    def _rearm_locked(self) -> None:
        """Leave TERMINAL back to CLOSED so a long-lived process retries a
        recovered D1 (ADR-056 D4). One-shot runs never reach here — they crash on
        the terminal raise. Resetting to CLOSED gives the next POST a fresh attempt;
        if D1 is still down it re-trips and re-probes from scratch."""
        self._state = CLOSED
        self._consecutive_failures = 0
        self._half_open_ok = 0
        self._prober_active = False
        self._metrics["rearms"] += 1
        logger.warning(
            "D1 circuit re-arming after %ds terminal cooldown — retrying D1 "
            "(long-lived process recovery, ADR-056)",
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
        "rearms": 0,
    }
    for breaker in breakers:
        snapshot = breaker.metrics_snapshot()
        for metric_key in agg:
            agg[metric_key] += snapshot.get(metric_key, 0)
    return agg
