import importlib
import os
import sys
from pathlib import Path

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

_CONFIG_STORE = Path(project_root) / "reports" / "api_config_store.json"


def _reload_auth_module():
    module = importlib.import_module("apps.api.infra.auth")
    return importlib.reload(module)


def _hide_api_config_store(monkeypatch):
    real_exists = Path.exists

    def exists_without_api_store(path: Path) -> bool:
        if path == _CONFIG_STORE:
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists_without_api_store)


def test_missing_api_secret_generates_ephemeral_secret_in_non_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("API_SECRET_KEY", raising=False)

    auth_module = _reload_auth_module()

    assert len(auth_module.API_SECRET_KEY) >= 32


def test_generated_api_secret_is_stable_across_auth_reimports(monkeypatch):
    """A test-only auth reimport must not strand already-wired API routers.

    FastAPI dependencies capture the auth functions present when routers are
    registered. If a later test deletes/reimports ``apps.api.infra.auth`` and
    gets a new generated secret, tokens minted from the fresh module fail
    against those already-captured dependencies with 401.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("API_SECRET_KEY", raising=False)
    sys.modules.pop("apps.api.infra._auth_runtime_state", None)

    from javdb.infra.config import cfg as _real_cfg

    def _cfg_no_secret(name, default=""):
        if name == "API_SECRET_KEY":
            return default
        return _real_cfg(name, default)

    monkeypatch.setattr("javdb.infra.config.cfg", _cfg_no_secret)

    _hide_api_config_store(monkeypatch)

    first = _reload_auth_module().API_SECRET_KEY
    sys.modules.pop("apps.api.infra.auth", None)
    second = importlib.import_module("apps.api.infra.auth").API_SECRET_KEY

    assert second == first


def test_missing_api_secret_raises_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("API_SECRET_KEY", raising=False)

    from javdb.infra.config import cfg as _real_cfg

    def _cfg_no_secret(name, default=""):
        if name == "API_SECRET_KEY":
            return default
        return _real_cfg(name, default)

    monkeypatch.setattr("javdb.infra.config.cfg", _cfg_no_secret)

    # _read_store_value reads from the on-disk config store which may
    # also carry API_SECRET_KEY. Temporarily hide it so _resolve sees all
    # three sources as empty.
    _hide_api_config_store(monkeypatch)

    with pytest.raises(RuntimeError, match="API_SECRET_KEY is required"):
        _reload_auth_module()


class _StubURL:
    def __init__(self, path: str) -> None:
        self.path = path


class _StubRequest:
    """Minimal duck-typed Request for ``_verify_csrf``."""

    def __init__(
        self,
        *,
        method: str,
        path: str,
        header_token: str,
        cookie_token: str,
    ) -> None:
        self.method = method
        self.url = _StubURL(path)
        self.headers = {"X-CSRF-Token": header_token} if header_token else {}
        self.cookies = {"csrf_token": cookie_token} if cookie_token else {}


def _csrf_verifier(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    return _reload_auth_module()._verify_csrf


def test_csrf_accepts_matching_tokens(monkeypatch):
    fn = _csrf_verifier(monkeypatch)
    # No exception → accepted. Matching tokens must pass the constant-time
    # check (hmac.compare_digest replaces a plain ``!=`` comparison).
    fn(_StubRequest(
        method="POST", path="/api/tasks/daily",
        header_token="abc123", cookie_token="abc123",
    ))


def test_csrf_rejects_mismatched_tokens(monkeypatch):
    from fastapi import HTTPException
    fn = _csrf_verifier(monkeypatch)
    with pytest.raises(HTTPException) as excinfo:
        fn(_StubRequest(
            method="POST", path="/api/tasks/daily",
            header_token="abc123", cookie_token="xyz789",
        ))
    assert excinfo.value.status_code == 403


def test_csrf_rejects_missing_header(monkeypatch):
    from fastapi import HTTPException
    fn = _csrf_verifier(monkeypatch)
    with pytest.raises(HTTPException):
        fn(_StubRequest(
            method="POST", path="/api/tasks/daily",
            header_token="", cookie_token="abc123",
        ))


def test_csrf_skips_get_methods(monkeypatch):
    fn = _csrf_verifier(monkeypatch)
    # GET must bypass CSRF even with empty tokens.
    fn(_StubRequest(
        method="GET", path="/api/tasks/daily",
        header_token="", cookie_token="",
    ))


def test_csrf_skips_login_endpoint(monkeypatch):
    fn = _csrf_verifier(monkeypatch)
    # /api/auth/login is the only POST that intentionally bypasses CSRF —
    # the CSRF cookie is *set* by this endpoint.
    fn(_StubRequest(
        method="POST", path="/api/auth/login",
        header_token="", cookie_token="",
    ))
