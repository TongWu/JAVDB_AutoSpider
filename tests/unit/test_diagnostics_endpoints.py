"""Unit tests for /api/diag/* endpoints (Phase 2, Task 4).

All external integrations (JavDB login, config.py writes, DB writes) are mocked —
no real network calls and no real config.py modifications are made.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(_isolate_sqlite):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update(
        {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}
    )
    return client


@pytest.fixture
def readonly_client(_isolate_sqlite):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update(
        {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}
    )
    return client


@pytest.fixture
def anon_client(_isolate_sqlite):
    from apps.api.services.runtime import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/diag/javdb-session
# ---------------------------------------------------------------------------


class TestGetJavdbSession:
    def test_returns_200_with_cookie_present_field(self, admin_client):
        """GET /api/diag/javdb-session → 200 with cookie_present field."""
        with patch("apps.api.routers.diagnostics.cfg", return_value=""):
            with patch("apps.api.routers.diagnostics._get_last_refresh_time", return_value=None):
                resp = admin_client.get("/api/diag/javdb-session")

        assert resp.status_code == 200
        data = resp.json()
        assert "cookie_present" in data

    def test_cookie_absent_fields(self, admin_client):
        """When no cookie is set, cookie_present=False and preview=None."""
        with patch("apps.api.routers.diagnostics.cfg", return_value=""):
            with patch("apps.api.routers.diagnostics._get_last_refresh_time", return_value=None):
                resp = admin_client.get("/api/diag/javdb-session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cookie_present"] is False
        assert data["cookie_value_preview"] is None
        assert data["last_refresh_time"] is None
        assert data["is_likely_valid"] is False

    def test_cookie_present_shows_preview(self, admin_client):
        """When a cookie is set, preview shows first 8 chars + '...'."""
        fake_cookie = "abcdefghijklmnop"
        with patch("apps.api.routers.diagnostics.cfg", return_value=fake_cookie):
            with patch("apps.api.routers.diagnostics._get_last_refresh_time", return_value=None):
                resp = admin_client.get("/api/diag/javdb-session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cookie_present"] is True
        assert data["cookie_value_preview"] == "abcdefgh..."

    def test_last_refresh_time_recent_sets_is_likely_valid(self, admin_client):
        """Recent last_refresh_time → is_likely_valid=True."""
        from datetime import datetime, timezone

        recent_ts = datetime.now(timezone.utc).isoformat()
        with patch("apps.api.routers.diagnostics.cfg", return_value="somecookie"):
            with patch("apps.api.routers.diagnostics._get_last_refresh_time", return_value=recent_ts):
                resp = admin_client.get("/api/diag/javdb-session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_likely_valid"] is True

    def test_unauthenticated_get_returns_401(self, anon_client):
        """Unauthenticated GET → 401."""
        resp = anon_client.get("/api/diag/javdb-session")
        assert resp.status_code == 401

    def test_readonly_can_get_session_status(self, readonly_client):
        """Readonly user can call GET /api/diag/javdb-session."""
        with patch("apps.api.routers.diagnostics.cfg", return_value=""):
            with patch("apps.api.routers.diagnostics._get_last_refresh_time", return_value=None):
                resp = readonly_client.get("/api/diag/javdb-session")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/diag/javdb-session/refresh — cookie_paste
# ---------------------------------------------------------------------------


class TestRefreshCookiePaste:
    def test_cookie_paste_success(self, admin_client):
        """POST with method=cookie_paste + valid cookie_value → success, preview returned."""
        fake_cookie = "valid_session_cookie_value"

        with patch("apps.api.services.config_service.update_config_payload") as mock_update:
            with patch("apps.api.routers.diagnostics._set_last_refresh_time") as mock_set_ts:
                resp = admin_client.post(
                    "/api/diag/javdb-session/refresh",
                    json={"method": "cookie_paste", "cookie_value": fake_cookie},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["method"] == "cookie_paste"
        assert data["new_cookie_preview"] == "valid_se..."
        assert data["error"] is None

        # Verify persist was called with the cookie value
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0]["JAVDB_SESSION_COOKIE"] == fake_cookie

        # Verify last_javdb_refresh timestamp was written
        mock_set_ts.assert_called_once()

    def test_cookie_paste_missing_cookie_value_returns_422(self, admin_client):
        """POST method=cookie_paste without cookie_value → 422."""
        resp = admin_client.post(
            "/api/diag/javdb-session/refresh",
            json={"method": "cookie_paste"},
        )
        assert resp.status_code == 422

    def test_cookie_paste_empty_cookie_value_returns_422(self, admin_client):
        """POST method=cookie_paste with empty cookie_value → 422."""
        resp = admin_client.post(
            "/api/diag/javdb-session/refresh",
            json={"method": "cookie_paste", "cookie_value": ""},
        )
        assert resp.status_code == 422

    def test_cookie_paste_whitespace_only_returns_422(self, admin_client):
        """POST method=cookie_paste with whitespace-only cookie_value → 422."""
        resp = admin_client.post(
            "/api/diag/javdb-session/refresh",
            json={"method": "cookie_paste", "cookie_value": "   "},
        )
        assert resp.status_code == 422

    def test_cookie_paste_persist_failure_returns_success_false(self, admin_client):
        """If persisting the cookie raises, return success=False with error message."""
        with patch(
            "apps.api.services.config_service.update_config_payload",
            side_effect=RuntimeError("disk full"),
        ):
            resp = admin_client.post(
                "/api/diag/javdb-session/refresh",
                json={"method": "cookie_paste", "cookie_value": "somecookievalue"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "disk full" in data["error"]


# ---------------------------------------------------------------------------
# POST /api/diag/javdb-session/refresh — headless
# ---------------------------------------------------------------------------


class TestRefreshHeadless:
    def test_headless_success(self, admin_client):
        """POST method=headless → delegates to system_service, returns success."""
        mock_result = {
            "status": "ok",
            "message": "Logged in via auto",
            "proxy_used": "auto",
            "attempts": [],
            "output": "",
        }
        with patch(
            "apps.api.services.system_service.refresh_javdb_session_with_options",
            return_value=mock_result,
        ) as mock_svc:
            with patch("apps.api.routers.diagnostics.cfg", return_value="newcookie1234"):
                with patch("apps.api.routers.diagnostics._set_last_refresh_time"):
                    resp = admin_client.post(
                        "/api/diag/javdb-session/refresh",
                        json={"method": "headless"},
                    )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["method"] == "headless"
        assert data["new_cookie_preview"] == "newcooki..."
        assert data["error"] is None
        mock_svc.assert_called_once()

    def test_headless_failure_returns_success_false(self, admin_client):
        """POST method=headless when login fails → success=False with error."""
        mock_result = {
            "status": "failed",
            "message": "IP appears blocked or rate-limited",
            "proxy_used": None,
            "attempts": [],
            "output": "",
        }
        with patch(
            "apps.api.services.system_service.refresh_javdb_session_with_options",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/diag/javdb-session/refresh",
                json={"method": "headless"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] == "IP appears blocked or rate-limited"


# ---------------------------------------------------------------------------
# Auth checks on POST
# ---------------------------------------------------------------------------


class TestRefreshAuthChecks:
    def test_unauthenticated_post_returns_401_or_403(self, anon_client):
        """Unauthenticated POST → 401 (or 403 when CSRF fires first)."""
        resp = anon_client.post(
            "/api/diag/javdb-session/refresh",
            json={"method": "headless"},
        )
        assert resp.status_code in (401, 403)

    def test_readonly_post_returns_403(self, readonly_client):
        """Readonly user POST → 403 (admin role required)."""
        resp = readonly_client.post(
            "/api/diag/javdb-session/refresh",
            json={"method": "headless"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Unknown method
# ---------------------------------------------------------------------------


class TestUnknownMethod:
    def test_unknown_method_returns_422(self, admin_client):
        """POST with an unrecognized method → 422 with a descriptive detail."""
        with patch("apps.api.routers.diagnostics._set_last_refresh_time"):
            resp = admin_client.post(
                "/api/diag/javdb-session/refresh",
                json={"method": "ssh_tunnel"},
            )

        assert resp.status_code == 422
        assert "ssh_tunnel" in resp.json()["detail"]
