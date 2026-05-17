"""Unit tests for apps.api.services.explore_service._qb_login_session.

Regression: the explore one-click flow's qB login used to accept only
status_code == 200 and response body == "Ok.". qBittorrent >= 5.2.0
returns 204 No Content with empty body on successful /auth/login,
which made the explore flow report 'Failed to login qBittorrent'
against every v5.2+ server.

The fix delegates to javdb.integrations.qb.client.try_login_base_urls
which already accepts both legacy 200/Ok. and v5.2+ 204.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from apps.api.services import explore_service  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _mk_response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _cfg(**overrides):
    base = {
        "QB_URL": "https://qb.example:8080",
        "QB_USERNAME": "admin",
        "QB_PASSWORD": "secret",
        "QB_VERIFY_TLS": False,
        "QB_ALLOW_INSECURE_HTTP": True,
        "REQUEST_TIMEOUT": 5,
    }
    base.update(overrides)
    return base


def test_qb_login_v52_204_accepted():
    """qB v5.2+ returns 204 No Content on /auth/login success — must
    not raise HTTPException."""
    fake_session = MagicMock()
    fake_session.post.return_value = _mk_response(204, "")
    with patch("requests.Session", return_value=fake_session):
        session, base_url = explore_service._qb_login_session(_cfg())

    assert session is fake_session
    assert base_url.startswith("http")


def test_qb_login_legacy_200_ok_accepted():
    fake_session = MagicMock()
    fake_session.post.return_value = _mk_response(200, "Ok.")
    with patch("requests.Session", return_value=fake_session):
        session, base_url = explore_service._qb_login_session(_cfg())

    assert session is fake_session
    assert base_url.startswith("http")


def test_qb_login_fails_credentials_rejected():
    """qB <= 5.1 returns 200 with body 'Fails.' for wrong credentials.
    Must raise 502 with a 'credentials rejected' detail (not silently
    fall back to other URLs)."""
    fake_session = MagicMock()
    fake_session.post.return_value = _mk_response(200, "Fails.")
    with patch("requests.Session", return_value=fake_session):
        with pytest.raises(HTTPException) as exc:
            explore_service._qb_login_session(_cfg())
    assert exc.value.status_code == 502
    assert "credentials rejected" in str(exc.value.detail)


def test_qb_login_missing_credentials_raises_422():
    with pytest.raises(HTTPException) as exc:
        explore_service._qb_login_session(_cfg(QB_USERNAME="", QB_PASSWORD=""))
    assert exc.value.status_code == 422
