# tests/unit/test_capabilities_site_drift_flag.py
from apps.api.routers.capabilities import build_capabilities


def test_flag_defaults_true(monkeypatch):
    monkeypatch.delenv("FEATURE_SITE_DRIFT_SENTINEL", raising=False)
    caps = build_capabilities()
    assert caps.features.site_drift_sentinel is True


def test_flag_env_override_false(monkeypatch):
    monkeypatch.setenv("FEATURE_SITE_DRIFT_SENTINEL", "false")
    caps = build_capabilities()
    assert caps.features.site_drift_sentinel is False
