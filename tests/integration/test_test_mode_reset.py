import os
import pytest
from fastapi.testclient import TestClient


def _reload_app():
    """Reload app after env mutation. Required because TEST_MODE is read at
    import time when wiring the conditional router."""
    import importlib
    import apps.api.services.runtime as runtime_mod
    importlib.reload(runtime_mod)
    return runtime_mod.app


def test_reset_endpoint_returns_404_when_test_mode_off(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    app = _reload_app()
    client = TestClient(app)
    r = client.post("/api/test/reset")
    assert r.status_code == 404


def test_reset_endpoint_works_when_test_mode_on(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODE", "1")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    app = _reload_app()
    client = TestClient(app)
    r = client.post("/api/test/reset")
    assert r.status_code == 200
    body = r.json()
    assert body == {"reset": True}
