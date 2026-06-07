# tests/unit/test_sentinel_canary_incident.py
from javdb.ops.sentinel.models import DriftFinding, SentinelVerdict
from javdb.ops.sentinel.persistence import build_drift_incident


def _verdict(critical: bool) -> SentinelVerdict:
    v = SentinelVerdict(critical=critical)
    v.findings.append(DriftFinding("index", "href", "critical", 0.1, 0.99, None))
    return v


def test_default_trigger_source_is_sentinel():
    rec = build_drift_incident(_verdict(True), session_id="S1",
                               run_id=None, run_attempt=None)
    assert rec.trigger_source == "sentinel"
    assert rec.incident_type == "site_drift"


def test_canary_trigger_source_changes_id_and_actions():
    sentinel = build_drift_incident(_verdict(True), session_id=None,
                                    run_id="R1", run_attempt=1,
                                    trigger_source="sentinel")
    canary = build_drift_incident(_verdict(True), session_id=None,
                                  run_id="R1", run_attempt=1,
                                  trigger_source="canary")
    assert canary.trigger_source == "canary"
    # trigger_source is hashed into the id -> distinct rows, no collision.
    assert canary.incident_id != sentinel.incident_id
    assert "canary" in canary.recommended_next_actions_json.lower()


# tests/unit/test_sentinel_canary_incident.py  (append)
import pytest

from javdb.ops.sentinel import probes as _probes
from javdb.ops.sentinel import service as _service
from javdb.ops.sentinel.models import DriftFinding, FieldFill, SentinelOptions
from javdb.ops.sentinel.probes import ProbeObservation


class _FakeFillRepo:
    """Only baseline() is exercised by the canary (read-only)."""

    def __init__(self, baseline_value=None):
        self._b = baseline_value

    def baseline(self, page_type, field, *, window):
        return self._b


class _FakeIncidentRepo:
    def __init__(self):
        self.records = []

    def upsert(self, record):
        self.records.append(record)


def _stub_probes(monkeypatch, obs: ProbeObservation):
    monkeypatch.setattr(_probes, "run_probes", lambda gateway: obs)


def test_run_canary_clean_emits_no_incident(monkeypatch):
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 1.0, 100)]))
    inc = _FakeIncidentRepo()
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=inc, options=SentinelOptions(min_sample=30))
    assert v.critical is False
    assert v.findings == []
    assert inc.records == []


def test_run_canary_critical_fill_emits_canary_incident(monkeypatch):
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 0.05, 100)]))  # below min_fill 0.99
    inc = _FakeIncidentRepo()
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=inc, options=SentinelOptions(min_sample=30),
                            run_id="R1", run_attempt=1)
    assert v.critical is True
    assert len(inc.records) == 1
    assert inc.records[0].trigger_source == "canary"
    assert inc.records[0].incident_type == "site_drift"


def test_run_canary_anchor_finding_emits_incident(monkeypatch):
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 1.0, 100)],
        anchor_findings=[DriftFinding("detail", "video_code", "critical", 0.0, 1.0, None)]))
    inc = _FakeIncidentRepo()
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(), incident_repo=inc)
    assert v.critical is True
    assert any(f.field == "video_code" for f in v.findings)
    assert len(inc.records) == 1


def test_run_canary_total_fetch_failure_raises(monkeypatch):
    # Every pinned page failed to load: nothing fetched/parsed. Per ADR-035 D4 this
    # is NOT site drift, but it must NOT be reported as a clean run.
    from javdb.ops.sentinel.service import CanaryError
    _stub_probes(monkeypatch, ProbeObservation(fetch_failures=["https://javdb.com/"]))
    with pytest.raises(CanaryError):
        _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=_FakeIncidentRepo(),
                            options=SentinelOptions(min_sample=30))


def test_run_canary_persist_failure_raises(monkeypatch):
    # Drift detected but the incident write fails -> must propagate, not be a
    # silently-"successful" run that loses the site_drift incident.
    from javdb.ops.sentinel.service import CanaryError

    class _BoomIncidentRepo:
        def upsert(self, record):
            raise RuntimeError("d1 write failed")

    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 0.05, 100)]))  # critical -> a finding to persist
    with pytest.raises(CanaryError):
        _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=_BoomIncidentRepo(),
                            options=SentinelOptions(min_sample=30))


def test_run_canary_partial_fetch_failure_is_not_an_error(monkeypatch):
    # The index parsed fine (fills present); only an anchor page failed. That is a
    # normal run (anchor fetch failure is logged, not drift) -> no CanaryError.
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 1.0, 100)],
        fetch_failures=["https://javdb.com/v/STALE"]))
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=_FakeIncidentRepo(),
                            options=SentinelOptions(min_sample=30))
    assert v.critical is False


def test_cli_canary_returns_3_on_canary_error(monkeypatch):
    import apps.cli.ops.sentinel as _cli
    from javdb.ops.sentinel.service import CanaryError

    def _boom(**kwargs):
        raise CanaryError("boom")

    monkeypatch.setattr(_cli, "run_canary", _boom)
    assert _cli.main(["--canary"]) == 3


def test_canary_use_proxy_honors_proxy_modules(monkeypatch):
    # The canary follows the 'spider' module's proxy policy instead of hard-coding
    # use_proxy=True, so PROXY_MODULES / PROXY_MODE are respected.
    import javdb.ops.sentinel.service as _svcmod

    def _cfg(modules, mode):
        return lambda k, d=None: {"PROXY_MODULES": modules, "PROXY_MODE": mode}.get(k, d)

    monkeypatch.setattr(_svcmod, "cfg", _cfg(["spider"], "none"))
    assert _svcmod._canary_use_proxy() is False          # proxy disabled globally
    monkeypatch.setattr(_svcmod, "cfg", _cfg(["spider"], "pool"))
    assert _svcmod._canary_use_proxy() is True            # spider proxied -> canary proxies
    monkeypatch.setattr(_svcmod, "cfg", _cfg(["qbittorrent"], "pool"))
    assert _svcmod._canary_use_proxy() is False           # spider excluded -> no proxy
