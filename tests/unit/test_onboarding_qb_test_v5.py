"""Unit tests for apps.api.routers.onboarding._test_qb.

Regression: qBittorrent v5.2.0 changed the /api/v2/auth/login success
response from "200 OK with body 'Ok.'" to "204 No Content with empty
body" (qbittorrent/qBittorrent PR #21349). The onboarding qB test used
to accept only status_code == 200 and would report "qB auth failed"
against any v5.2+ server. The fix delegates to the shared
try_login_base_urls helper in javdb.integrations.qb.client, which
already accepts both legacy 200/Ok. and v5.2+ 204.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch


project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from apps.api.routers import onboarding as onboarding_router  # noqa: E402


def _stub_cfg(monkeypatch, **overrides):
    cfg = {
        "QB_URL": "https://qb.example:8080",
        "QB_USERNAME": "admin",
        "QB_PASSWORD": "secret",
        "QB_VERIFY_TLS": False,
    }
    cfg.update(overrides)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config", lambda: cfg
    )
    return cfg


def _mk_response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def test_qb_login_v52_204_treated_as_success(monkeypatch):
    """qB >= 5.2.0 returns 204 No Content on successful /auth/login.
    The onboarding test must accept this, not report 'qB auth failed'."""
    _stub_cfg(monkeypatch)

    fake_session = MagicMock()
    # POST /api/v2/auth/login → 204 (v5.2+ success), GET /app/version → 200
    fake_session.post.return_value = _mk_response(204, "")
    fake_session.get.return_value = _mk_response(200, "v5.2.0")
    with patch("requests.Session", return_value=fake_session):
        ok, msg, details = onboarding_router._test_qb()

    assert ok is True, f"expected success on 204, got: {msg}"
    assert "v5.2.0" in msg
    assert details and details.get("url") == "https://qb.example:8080"


def test_qb_login_legacy_200_ok_still_works(monkeypatch):
    """qB <= 5.1.x returns 200 OK with body 'Ok.' — must still be accepted."""
    _stub_cfg(monkeypatch)

    fake_session = MagicMock()
    fake_session.post.return_value = _mk_response(200, "Ok.")
    fake_session.get.return_value = _mk_response(200, "v5.0.4")
    with patch("requests.Session", return_value=fake_session):
        ok, msg, _ = onboarding_router._test_qb()

    assert ok is True
    assert "v5.0.4" in msg


def test_qb_login_fails_credentials_rejected(monkeypatch):
    """qB <= 5.1.x returns 200 OK with body 'Fails.' on bad credentials.
    Must surface as auth failure, not connect failure."""
    _stub_cfg(monkeypatch)

    fake_session = MagicMock()
    fake_session.post.return_value = _mk_response(200, "Fails.")
    with patch("requests.Session", return_value=fake_session):
        ok, msg, _ = onboarding_router._test_qb()

    assert ok is False
    assert "credentials rejected" in msg or "auth failed" in msg.lower()


def test_qb_no_credentials_uses_ping(monkeypatch):
    """When username/password are absent we skip login and ping /app/version."""
    _stub_cfg(monkeypatch, QB_USERNAME="", QB_PASSWORD="")

    fake_session = MagicMock()
    # try_ping_base_urls uses 200/403 as 'reachable'
    fake_session.get.return_value = _mk_response(200, "v5.2.0")
    with patch("requests.Session", return_value=fake_session):
        ok, msg, _ = onboarding_router._test_qb()

    assert ok is True
    # session.post is never called when there are no creds
    fake_session.post.assert_not_called()


def test_qb_url_not_set(monkeypatch):
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config", lambda: {}
    )
    ok, msg, details = onboarding_router._test_qb()
    assert ok is False
    assert "QB_URL not set" in msg
    assert details is None
