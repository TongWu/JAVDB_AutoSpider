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
    probe_calls = {"n": 0}

    def post_request(url, headers, json, timeout):
        sql = json.get("sql")
        if sql == "SELECT 1":
            probe_calls["n"] += 1
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
    # The probe must have been called at least once — verifies the breaker gate
    # was actually consulted (not just plain retry exhaustion).
    assert probe_calls["n"] >= 1, (
        f"Expected _probe_d1 to be called via breaker.acquire(), "
        f"but probe_calls={probe_calls['n']}. "
        f"The breaker is not wired into _post_with_retry."
    )


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


def test_internal_error_7500_gets_longer_backoff_floor():
    from javdb.storage.d1_port import D1PortConfig as PortConfig
    port = D1AccessPort(
        url=_TEST_URL,
        headers={"Authorization": "Bearer x"},
        config=PortConfig(timeout=1, batch_limit=50, max_retries=5,
                          retry_base_sec=0, retry_max_sleep_sec=30),
        post_request=lambda *a, **k: _Resp(200),
        sleep=lambda _dt: None,
        jitter=lambda: 0.0,
    )
    exc = D1TransientError("D1 API returned HTTP 500: ... internal error ...")
    exc.is_internal_error = True
    # With base/jitter zeroed, a plain transient would be ~0s on attempt 0;
    # the 7500 floor must lift it to a non-trivial wait.
    delay = port._compute_backoff(0, exc)
    assert delay >= 2.0


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


def test_breaker_defaults_enabled_when_env_unset(monkeypatch):
    """D7 guard: the production default (env unset) builds an ENABLED breaker.

    The suite-wide conftest fixture defaults ``D1_CIRCUIT_BREAKER_ENABLED=false``
    for isolation, which would otherwise mask a regression that flips the code
    default. Removing the env entirely must still yield an enabled breaker.
    """
    monkeypatch.delenv("D1_CIRCUIT_BREAKER_ENABLED", raising=False)
    d1_circuit_breaker.reset_circuit_breaker()
    breaker = d1_circuit_breaker.get_circuit_breaker("https://example/default-probe")
    assert breaker.enabled is True
