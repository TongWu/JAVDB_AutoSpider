"""Thin httpx-based GitHub REST API v3 client.

Wraps the subset of GitHub Actions endpoints needed by the web UI:
- List workflows (with latest run enrichment)
- List runs (optionally filtered by workflow)
- Dispatch a workflow run
- Get the logs download URL for a run
- Get/update workflow file content (Contents API)

Token + repo resolution (ADR-008 D18):
- token: GIT_PASSWORD from config.py via cfg()
- repo: derived from GIT_REPO_URL (https://github.com/OWNER/NAME.git → OWNER/NAME),
  falling back to GH_ACTIONS_REPO env var if unparseable/absent.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Optional

import httpx

from javdb.infra.config import cfg

_GH_REPO_URL_RE = re.compile(
    r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


def _parse_repo_from_url(url: str) -> Optional[str]:
    """Extract 'owner/name' from a GitHub remote URL.

    Handles both HTTPS (https://github.com/owner/name.git) and
    SSH (git@github.com:owner/name.git) forms.
    Returns None when the URL is empty or unparseable.
    """
    if not url:
        return None
    m = _GH_REPO_URL_RE.search(url)
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('name')}"


def resolve_token_and_repo() -> tuple[str, str]:
    """Return (token, repo) from config per ADR-008 D18.

    - token: cfg('GIT_PASSWORD', '')
    - repo: parsed from cfg('GIT_REPO_URL', '') or env GH_ACTIONS_REPO
    """
    token = str(cfg("GIT_PASSWORD", "") or "")
    repo_url = str(cfg("GIT_REPO_URL", "") or "")
    repo = _parse_repo_from_url(repo_url)
    if not repo:
        repo = os.environ.get("GH_ACTIONS_REPO", "")
    return token, repo or ""


_GH_API_BASE = "https://api.github.com"
_GH_API_VERSION = "2022-11-28"


class GitHubActionsClient:
    """Thin wrapper around GitHub REST API v3 for Actions endpoints."""

    def __init__(
        self,
        token: str,
        repo: str,
        *,
        transport=None,
    ) -> None:
        """
        Args:
            token: GitHub personal access token (PAT).
            repo: Repository in "owner/name" format.
            transport: Optional httpx transport (e.g. MockTransport for tests).
        """
        self.repo = repo
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GH_API_VERSION,
        }
        client_kwargs: dict = {
            "base_url": _GH_API_BASE,
            "headers": headers,
            "timeout": 30.0,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.Client(**client_kwargs)

    def list_workflows(self) -> list[dict]:
        """GET /repos/{repo}/actions/workflows → list of workflow dicts."""
        resp = self._client.get(f"/repos/{self.repo}/actions/workflows")
        resp.raise_for_status()
        return resp.json().get("workflows", [])

    def list_runs(
        self,
        workflow_id: Optional[int] = None,
        per_page: int = 30,
    ) -> list[dict]:
        """GET runs for a specific workflow or all runs in the repo.

        Args:
            workflow_id: If given, fetch runs for that workflow only.
            per_page: Page size (default 30).
        """
        if workflow_id is not None:
            url = f"/repos/{self.repo}/actions/workflows/{workflow_id}/runs"
        else:
            url = f"/repos/{self.repo}/actions/runs"
        resp = self._client.get(url, params={"per_page": per_page})
        resp.raise_for_status()
        return resp.json().get("workflow_runs", [])

    def dispatch_workflow(
        self,
        workflow_id: int,
        ref: str = "main",
        inputs: Optional[dict] = None,
    ) -> None:
        """POST /repos/{repo}/actions/workflows/{id}/dispatches.

        Expects a 204 No Content response from GitHub.
        Raises httpx.HTTPStatusError on non-2xx.
        """
        body: dict = {"ref": ref}
        if inputs:
            body["inputs"] = inputs
        resp = self._client.post(
            f"/repos/{self.repo}/actions/workflows/{workflow_id}/dispatches",
            json=body,
        )
        resp.raise_for_status()

    def get_run_logs_url(self, run_id: int) -> str:
        """GET /repos/{repo}/actions/runs/{id}/logs with follow_redirects=False.

        GitHub responds 302 with a Location header pointing to a signed S3 URL.
        Returns the Location URL on 302, or raises on unexpected status codes.
        """
        resp = self._client.get(
            f"/repos/{self.repo}/actions/runs/{run_id}/logs",
            follow_redirects=False,
        )
        if resp.status_code == 302:
            location = resp.headers.get("location")
            if not location:
                raise httpx.HTTPError(
                    "GitHub logs redirect (302) missing Location header"
                )
            return location
        resp.raise_for_status()
        return ""

    def get_workflow_content(self, filename: str) -> dict:
        """Return { content: str (decoded), sha: str, path: str }."""
        resp = self._client.get(
            f"/repos/{self.repo}/contents/.github/workflows/{filename}"
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "content": base64.b64decode(data["content"]).decode("utf-8"),
            "sha": data["sha"],
            "path": data["path"],
        }

    def update_workflow_content(
        self,
        filename: str,
        content: str,
        sha: str,
        message: str,
        branch: str = "main",
    ) -> dict:
        """Commit updated workflow file. Returns { commit_sha }."""
        resp = self._client.put(
            f"/repos/{self.repo}/contents/.github/workflows/{filename}",
            json={
                "message": message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "sha": sha,
                "branch": branch,
            },
        )
        resp.raise_for_status()
        return {"commit_sha": resp.json()["commit"]["sha"]}

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


__all__ = [
    "GitHubActionsClient",
    "resolve_token_and_repo",
]
