from javdb.storage.d1_client import D1CircuitOpenError, D1Error, D1TransientError


def test_circuit_open_error_is_terminal_not_transient():
    """D5: must NOT be a D1TransientError, so flush()'s recovery handler skips it."""
    err = D1CircuitOpenError("D1 unavailable for 900s")
    assert isinstance(err, D1Error)
    assert not isinstance(err, D1TransientError)


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


# ── New branch-coverage tests (ADR-056 Task 2) ────────────────────────────────

def test_terminal_acquire_raises_without_probing():
    """TERMINAL acquire() branch: second caller after breaker went terminal must raise
    D1CircuitOpenError immediately and must NOT invoke probe_fn."""
    clock = _Clock()
    # Drive to terminal: probe always fails, max_open_sec=10 so deadline hits fast.
    b = _breaker(clock, max_open_sec=10, probe_interval_sec=5.0)
    b.record_failure(); b.record_failure(); b.record_failure()
    assert b.state == "open"
    with pytest.raises(D1CircuitOpenError):
        b.acquire(probe_fn=lambda: False)
    assert b.state == "terminal"
    # Now a fresh acquire() must hit the TERMINAL early-exit path — probe_fn must not run.
    with pytest.raises(D1CircuitOpenError):
        b.acquire(probe_fn=lambda: pytest.fail("must not probe when TERMINAL"))


def _scripted_probe(results):
    seq = iter(results)
    def probe():
        return next(seq)
    return probe


def test_half_open_backslide_to_open_then_recovers():
    """HALF_OPEN→OPEN backslide branch: a bad probe after a good one returns the
    breaker to OPEN, and subsequent good probes can still close it.

    Sequence [True, False, True, True] with half_open_successes=2:
      iter 1: True  — OPEN→HALF_OPEN, half_open_ok=1; 1<2, stay HALF_OPEN
      iter 2: False — HALF_OPEN→OPEN (backslide), half_open_ok=0
      iter 3: True  — OPEN→HALF_OPEN, half_open_ok=1; 1<2, stay HALF_OPEN
      iter 4: True  — already HALF_OPEN, half_open_ok=2; 2>=2 → CLOSED
    """
    clock = _Clock()
    b = _breaker(clock, half_open_successes=2, max_open_sec=900, probe_interval_sec=5.0)
    b.record_failure(); b.record_failure(); b.record_failure()
    assert b.state == "open"
    probe = _scripted_probe([True, False, True, True])
    b.acquire(probe_fn=probe)
    assert b.state == "closed"


def test_metrics_snapshot_trip_resume_and_terminal():
    """Metrics accounting: pin trips/resumes/probes/terminal_opens counters.

    Part A — one full trip→probe→recover cycle:
      trips==1, resumes==1, probes>=1, terminal_opens==0.
    Part B — terminal cycle:
      terminal_opens==1, resumes==0.
    """
    # Part A: trip → probe success → close
    clock_a = _Clock()
    b_a = _breaker(clock_a)
    b_a.record_failure(); b_a.record_failure(); b_a.record_failure()
    b_a.acquire(probe_fn=lambda: True)
    assert b_a.state == "closed"
    snap_a = b_a.metrics_snapshot()
    assert snap_a["trips"] == 1
    assert snap_a["resumes"] == 1
    assert snap_a["probes"] >= 1
    assert snap_a["terminal_opens"] == 0

    # Part B: trip → probe always fails → terminal
    clock_b = _Clock()
    b_b = _breaker(clock_b, max_open_sec=10, probe_interval_sec=5.0)
    b_b.record_failure(); b_b.record_failure(); b_b.record_failure()
    with pytest.raises(D1CircuitOpenError):
        b_b.acquire(probe_fn=lambda: False)
    assert b_b.state == "terminal"
    snap_b = b_b.metrics_snapshot()
    assert snap_b["terminal_opens"] == 1
    assert snap_b["resumes"] == 0


def test_terminal_rearms_after_cooldown_for_long_lived_process():
    """A long-lived process (apps/api) must not self-lock on a cached TERMINAL
    breaker after one >max_open_sec outage. Before the cooldown elapses the
    breaker still fails fast (one-shot semantics); after it, the next acquire
    re-arms to CLOSED so a recovered D1 is retried (ADR-056 D4).
    """
    clock = _Clock()
    b = _breaker(clock, max_open_sec=10, probe_interval_sec=5.0)
    b.record_failure(); b.record_failure(); b.record_failure()
    with pytest.raises(D1CircuitOpenError):
        b.acquire(probe_fn=lambda: False)        # prober exhausts max_open → TERMINAL
    assert b.state == "terminal"
    # Cooldown not yet elapsed → still fail-fast, no probe.
    with pytest.raises(D1CircuitOpenError):
        b.acquire(probe_fn=lambda: pytest.fail("must not probe before cooldown"))
    assert b.state == "terminal"
    # After the cooldown, the next acquire re-arms to CLOSED (no probe — the caller
    # gets a fresh real POST attempt) so a recovered D1 resumes serving.
    clock.sleep(11)                              # monotonic - terminal_at = 11 > 10
    b.acquire(probe_fn=lambda: pytest.fail("re-arm returns CLOSED without probing"))
    assert b.state == "closed"
    assert b.metrics_snapshot()["rearms"] == 1


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
