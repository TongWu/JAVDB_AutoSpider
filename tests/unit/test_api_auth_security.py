import importlib
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


def _reload_auth_module():
    module = importlib.import_module("apps.api.infra.auth")
    return importlib.reload(module)


def test_missing_api_secret_generates_ephemeral_secret_in_non_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("API_SECRET_KEY", raising=False)

    auth_module = _reload_auth_module()

    assert len(auth_module.API_SECRET_KEY) >= 32


def test_missing_api_secret_raises_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("API_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="API_SECRET_KEY is required"):
        _reload_auth_module()
