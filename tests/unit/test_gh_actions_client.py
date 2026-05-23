"""Unit tests for GitHubActionsClient using httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from javdb.integrations.gh_actions.client import GitHubActionsClient, _parse_repo_from_url


# ---------------------------------------------------------------------------
# _parse_repo_from_url helpers
# ---------------------------------------------------------------------------


class TestParseRepoFromUrl:
    def test_https_git_suffix(self):
        assert _parse_repo_from_url("https://github.com/owner/name.git") == "owner/name"

    def test_https_no_git_suffix(self):
        assert _parse_repo_from_url("https://github.com/owner/name") == "owner/name"

    def test_ssh_form(self):
        assert _parse_repo_from_url("git@github.com:owner/name.git") == "owner/name"

    def test_empty_string(self):
        assert _parse_repo_from_url("") is None

    def test_non_github_url(self):
        assert _parse_repo_from_url("https://gitlab.com/owner/name.git") is None

    def test_malformed_url(self):
        assert _parse_repo_from_url("not-a-url") is None


# ---------------------------------------------------------------------------
# Client tests with MockTransport
# ---------------------------------------------------------------------------

SAMPLE_WORKFLOWS = [
    {"id": 1, "name": "CI", "state": "active"},
    {"id": 2, "name": "Daily", "state": "active"},
]

SAMPLE_RUNS = [
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


def _make_handler(workflow_runs=None, dispatch_status=204):
    """Return a MockTransport handler with canned responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # List all workflows
        if path.endswith("/actions/workflows") and not "/workflows/" in path:
            return httpx.Response(200, json={"workflows": SAMPLE_WORKFLOWS})
        # List runs for a specific workflow
        if "/workflows/" in path and path.endswith("/runs"):
            runs = workflow_runs if workflow_runs is not None else SAMPLE_RUNS
            return httpx.Response(200, json={"workflow_runs": runs})
        # List all runs (no workflow filter)
        if path.endswith("/actions/runs") and "/runs/" not in path:
            return httpx.Response(200, json={"workflow_runs": SAMPLE_RUNS})
        # Logs redirect
        if "/runs/" in path and path.endswith("/logs"):
            return httpx.Response(
                302,
                headers={"location": "https://s3.example.com/logs/run-101.zip"},
            )
        # Dispatch
        if "/dispatches" in path:
            return httpx.Response(dispatch_status)
        return httpx.Response(404, json={"message": "Not found"})

    return handler


@pytest.fixture
def client():
    transport = httpx.MockTransport(_make_handler())
    c = GitHubActionsClient(token="test-token", repo="owner/repo", transport=transport)
    yield c
    c.close()


class TestListWorkflows:
    def test_returns_list(self, client):
        wfs = client.list_workflows()
        assert isinstance(wfs, list)
        assert len(wfs) == 2

    def test_workflow_fields(self, client):
        wf = client.list_workflows()[0]
        assert wf["id"] == 1
        assert wf["name"] == "CI"
        assert wf["state"] == "active"

    def test_empty_workflows(self):
        def empty_handler(req):
            if req.url.path.endswith("/actions/workflows"):
                return httpx.Response(200, json={"workflows": []})
            return httpx.Response(404)

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(empty_handler)
        )
        assert c.list_workflows() == []
        c.close()

    def test_http_error_raises(self):
        def err_handler(req):
            return httpx.Response(500, json={"message": "Internal Server Error"})

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(err_handler)
        )
        with pytest.raises(httpx.HTTPStatusError):
            c.list_workflows()
        c.close()


class TestListRuns:
    def test_all_runs_no_filter(self, client):
        runs = client.list_runs()
        assert len(runs) == 1
        assert runs[0]["id"] == 101

    def test_runs_with_workflow_filter(self, client):
        runs = client.list_runs(workflow_id=1)
        assert len(runs) == 1
        assert runs[0]["id"] == 101

    def test_runs_with_workflow_filter_uses_correct_path(self):
        seen_paths = []

        def recording_handler(req):
            seen_paths.append(req.url.path)
            return httpx.Response(200, json={"workflow_runs": []})

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(recording_handler)
        )
        c.list_runs(workflow_id=42)
        c.close()
        assert any("workflows/42/runs" in p for p in seen_paths)

    def test_runs_without_workflow_filter_uses_all_runs_path(self):
        seen_paths = []

        def recording_handler(req):
            seen_paths.append(req.url.path)
            return httpx.Response(200, json={"workflow_runs": []})

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(recording_handler)
        )
        c.list_runs()
        c.close()
        assert any(p.endswith("/actions/runs") for p in seen_paths)

    def test_per_page_sent_as_query_param(self):
        seen_params = []

        def recording_handler(req):
            seen_params.append(dict(req.url.params))
            return httpx.Response(200, json={"workflow_runs": []})

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(recording_handler)
        )
        c.list_runs(per_page=5)
        c.close()
        assert seen_params[0].get("per_page") == "5"


class TestDispatchWorkflow:
    def test_dispatch_204_no_exception(self, client):
        # Should not raise
        client.dispatch_workflow(workflow_id=1, ref="main")

    def test_dispatch_with_inputs(self):
        sent_bodies = []

        def recording_handler(req):
            if "/dispatches" in req.url.path:
                import json
                sent_bodies.append(json.loads(req.content))
                return httpx.Response(204)
            return httpx.Response(404)

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(recording_handler)
        )
        c.dispatch_workflow(workflow_id=7, ref="develop", inputs={"env": "staging"})
        c.close()
        assert sent_bodies[0]["ref"] == "develop"
        assert sent_bodies[0]["inputs"] == {"env": "staging"}

    def test_dispatch_http_error_raises(self):
        def err_handler(req):
            return httpx.Response(422, json={"message": "Unprocessable Entity"})

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(err_handler)
        )
        with pytest.raises(httpx.HTTPStatusError):
            c.dispatch_workflow(workflow_id=1, ref="main")
        c.close()


class TestGetRunLogsUrl:
    def test_302_returns_location(self, client):
        url = client.get_run_logs_url(101)
        assert url == "https://s3.example.com/logs/run-101.zip"

    def test_non_302_raises(self):
        def err_handler(req):
            if "/runs/" in req.url.path and req.url.path.endswith("/logs"):
                return httpx.Response(404)
            return httpx.Response(404)

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(err_handler)
        )
        with pytest.raises(httpx.HTTPStatusError):
            c.get_run_logs_url(999)
        c.close()

    def test_302_missing_location_raises(self):
        def handler(req):
            if "/runs/" in req.url.path and req.url.path.endswith("/logs"):
                return httpx.Response(302, headers={})
            return httpx.Response(404)

        c = GitHubActionsClient(
            token="t", repo="o/r", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(httpx.HTTPError):
            c.get_run_logs_url(101)
        c.close()


class TestAuthHeader:
    def test_bearer_token_sent(self):
        received_headers = []

        def recording_handler(req):
            received_headers.append(dict(req.headers))
            return httpx.Response(200, json={"workflows": []})

        c = GitHubActionsClient(
            token="secret-pat",
            repo="o/r",
            transport=httpx.MockTransport(recording_handler),
        )
        c.list_workflows()
        c.close()
        assert received_headers[0].get("authorization") == "Bearer secret-pat"


class TestContextManager:
    def test_context_manager(self):
        transport = httpx.MockTransport(_make_handler())
        with GitHubActionsClient(token="t", repo="o/r", transport=transport) as c:
            wfs = c.list_workflows()
        assert len(wfs) == 2
