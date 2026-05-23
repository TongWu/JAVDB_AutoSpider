"""Unit tests for /api/gh-actions/* endpoints.

Tests cover:
- Monitor-tier gate (GH_ACTIONS_TIER env var)
- Each of the 4 endpoints returns expected shape when tier is monitor+
- POST /runs requires admin role (readonly → 403)
- Error propagation (gh client raises → 502)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from javdb.integrations.gh_actions.client import GitHubActionsClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def readonly_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def anon_client():
    from apps.api.services.runtime import app

    return TestClient(app)


def _make_mock_transport():
    """MockTransport returning canned GH API responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/actions/workflows") and "/workflows/" not in path:
            return httpx.Response(
                200,
                json={
                    "workflows": [
                        {"id": 1, "name": "CI", "state": "active"},
                    ]
                },
            )
        if "/workflows/" in path and path.endswith("/runs"):
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 101,
                            "name": "CI",
                            "display_title": "Fix bug",
                            "status": "completed",
                            "conclusion": "success",
                            "event": "push",
                            "created_at": "2024-01-01T10:00:00Z",
                            "updated_at": "2024-01-01T10:30:00Z",
                            "head_sha": "abc123",
                            "run_number": 5,
                        }
                    ]
                },
            )
        if path.endswith("/actions/runs") and "/runs/" not in path:
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 101,
                            "workflow_id": 1,
                            "name": "CI",
                            "display_title": "Fix bug",
                            "status": "completed",
                            "conclusion": "success",
                            "event": "push",
                            "created_at": "2024-01-01T10:00:00Z",
                            "updated_at": "2024-01-01T10:30:00Z",
                            "head_sha": "abc123",
                            "run_number": 5,
                        }
                    ]
                },
            )
        if "/runs/" in path and path.endswith("/logs"):
            return httpx.Response(
                302,
                headers={"location": "https://s3.example.com/logs/run.zip"},
            )
        if "/dispatches" in path:
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


def _patched_client():
    """Return a GitHubActionsClient backed by MockTransport."""
    return GitHubActionsClient(
        token="fake-token",
        repo="owner/repo",
        transport=_make_mock_transport(),
    )


# ---------------------------------------------------------------------------
# Monitor-tier gate tests
# ---------------------------------------------------------------------------


class TestTierGate:
    def test_tier_none_blocks_all_endpoints(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "none")
        for method, path in [
            ("GET", "/api/gh-actions/workflows"),
            ("GET", "/api/gh-actions/runs"),
            ("GET", "/api/gh-actions/runs/101/logs"),
        ]:
            resp = admin_client.request(method, path)
            assert resp.status_code == 403, f"{method} {path} should be 403 when tier=none"
            detail = resp.json()["detail"]
            assert detail["error"]["code"] == "gh_actions.not_configured"

    def test_tier_none_blocks_post_runs(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "none")
        resp = admin_client.post(
            "/api/gh-actions/runs",
            json={"workflow_id": 1, "ref": "main"},
        )
        assert resp.status_code == 403

    def test_tier_monitor_allows_get_endpoints(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/workflows")
        assert resp.status_code == 200

    def test_tier_admin_allows_all(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "admin")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/runs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/gh-actions/workflows
# ---------------------------------------------------------------------------


class TestListWorkflows:
    def test_returns_200_with_workflows_key(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert "workflows" in data

    def test_workflow_shape(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/workflows")
        wf = resp.json()["workflows"][0]
        assert wf["id"] == 1
        assert wf["name"] == "CI"
        assert "last_run" in wf

    def test_last_run_enriched(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/workflows")
        last_run = resp.json()["workflows"][0]["last_run"]
        assert last_run is not None
        assert last_run["id"] == 101
        assert last_run["status"] == "completed"

    def test_readonly_can_list_workflows(self, readonly_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = readonly_client.get("/api/gh-actions/workflows")
        assert resp.status_code == 200

    def test_anon_returns_401(self, anon_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        resp = anon_client.get("/api/gh-actions/workflows")
        assert resp.status_code == 401

    def test_gh_api_error_returns_502(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")

        def bad_handler(req):
            return httpx.Response(500)

        bad_client = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(bad_handler)
        )
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=bad_client,
        ):
            resp = admin_client.get("/api/gh-actions/workflows")
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == "gh_actions.api_error"


# ---------------------------------------------------------------------------
# GET /api/gh-actions/runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_returns_200_with_runs_key(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/runs")
        assert resp.status_code == 200
        assert "runs" in resp.json()

    def test_run_item_shape(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/runs")
        run = resp.json()["runs"][0]
        assert "id" in run
        assert "status" in run
        assert "conclusion" in run
        assert "event" in run
        assert "created_at" in run

    def test_workflow_filter_query_param(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/runs?workflow=1")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["id"] == 101

    def test_anon_returns_401(self, anon_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        resp = anon_client.get("/api/gh-actions/runs")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/gh-actions/runs
# ---------------------------------------------------------------------------


class TestDispatchRun:
    def test_admin_dispatch_returns_dispatched_true(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.post(
                "/api/gh-actions/runs",
                json={"workflow_id": 1, "ref": "main"},
            )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] is True

    def test_dispatch_with_inputs(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.post(
                "/api/gh-actions/runs",
                json={"workflow_id": 1, "ref": "develop", "inputs": {"env": "test"}},
            )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] is True

    def test_readonly_returns_403(self, readonly_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        resp = readonly_client.post(
            "/api/gh-actions/runs",
            json={"workflow_id": 1, "ref": "main"},
        )
        assert resp.status_code == 403

    def test_anon_returns_401_or_403(self, anon_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        resp = anon_client.post(
            "/api/gh-actions/runs",
            json={"workflow_id": 1, "ref": "main"},
        )
        assert resp.status_code in (401, 403)

    def test_gh_api_error_returns_502(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")

        def err_handler(req):
            return httpx.Response(422)

        bad_client = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(err_handler)
        )
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=bad_client,
        ):
            resp = admin_client.post(
                "/api/gh-actions/runs",
                json={"workflow_id": 1, "ref": "main"},
            )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /api/gh-actions/runs/{run_id}/logs
# ---------------------------------------------------------------------------


class TestGetRunLogs:
    def test_returns_logs_url(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = admin_client.get("/api/gh-actions/runs/101/logs")
        assert resp.status_code == 200
        assert resp.json()["logs_url"] == "https://s3.example.com/logs/run.zip"

    def test_anon_returns_401(self, anon_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        resp = anon_client.get("/api/gh-actions/runs/101/logs")
        assert resp.status_code == 401

    def test_readonly_can_get_logs(self, readonly_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=_patched_client(),
        ):
            resp = readonly_client.get("/api/gh-actions/runs/101/logs")
        assert resp.status_code == 200

    def test_gh_api_error_returns_502(self, admin_client, monkeypatch):
        monkeypatch.setenv("GH_ACTIONS_TIER", "monitor")

        def err_handler(req):
            return httpx.Response(404)

        bad_client = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(err_handler)
        )
        with patch(
            "apps.api.routers.gh_actions._get_gh_client",
            return_value=bad_client,
        ):
            resp = admin_client.get("/api/gh-actions/runs/999/logs")
        assert resp.status_code == 502
