"""GitHub Actions endpoints.

GET    /api/gh-actions/workflows              — list workflows, each enriched with latest run
GET    /api/gh-actions/runs                   — list runs, optional ?workflow=<id> filter
POST   /api/gh-actions/runs                   — dispatch a workflow run (admin only)
GET    /api/gh-actions/runs/{run_id}/logs     — return the run's logs download URL
GET    /api/gh-actions/workflows/{name}       — return workflow YAML content (edit tier)
PUT    /api/gh-actions/workflows/{name}       — validate & commit workflow YAML (edit tier, admin)
GET    /api/gh-actions/secrets                — list repo secrets metadata (admin tier)
POST   /api/gh-actions/secrets                — create or update a secret (admin tier, admin role)
DELETE /api/gh-actions/secrets/{name}         — delete a secret (admin tier, admin role)

Monitor-tier endpoints (first four) require:
  1. A valid auth token (_require_auth / require_role)
  2. capabilities.gh_actions.tier != "none" (monitor-tier gate)

Edit-tier endpoints (next two) require:
  1. A valid auth token (_require_auth / require_role("admin") for PUT)
  2. capabilities.gh_actions.tier in ("edit", "admin") (edit-tier gate)

Admin-tier endpoints (last three) require:
  1. A valid auth token (_require_auth / require_role("admin") for POST/DELETE)
  2. capabilities.gh_actions.tier == "admin" (admin-tier gate)
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

_SAFE_WORKFLOW_NAME_RE = re.compile(r"^[\w\-\.]+\.ya?ml$")
_SAFE_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

from apps.api.infra.auth import _require_auth, require_role
from apps.api.routers.capabilities import build_capabilities
from apps.api.schemas.gh_actions import (
    CreateSecretRequest,
    CreateSecretResponse,
    DeleteSecretResponse,
    DispatchRequest,
    DispatchResponse,
    RunItem,
    RunLogsResponse,
    RunsResponse,
    SecretItem,
    SecretsResponse,
    WorkflowContentResponse,
    WorkflowItem,
    WorkflowsResponse,
    WorkflowUpdateRequest,
    WorkflowUpdateResponse,
)
from javdb.integrations.gh_actions.client import (
    GitHubActionsClient,
    resolve_token_and_repo,
)

router = APIRouter(prefix="/api/gh-actions", tags=["gh-actions"])


# ---------------------------------------------------------------------------
# Monitor-tier gate
# ---------------------------------------------------------------------------


def _require_gh_monitor() -> None:
    """Raise 403 if gh_actions.tier == 'none'."""
    caps = build_capabilities()
    if caps.gh_actions.tier == "none":
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "gh_actions.not_configured",
                    "message": "GitHub Actions integration not configured",
                }
            },
        )


# ---------------------------------------------------------------------------
# Edit-tier gate
# ---------------------------------------------------------------------------


def _require_gh_edit() -> None:
    """Raise 403 if gh_actions.tier not in ('edit', 'admin')."""
    caps = build_capabilities()
    if caps.gh_actions.tier not in ("edit", "admin"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "gh_actions.edit_not_allowed",
                    "message": "GH Actions edit tier required",
                }
            },
        )


# ---------------------------------------------------------------------------
# Admin-tier gate
# ---------------------------------------------------------------------------


def _require_gh_admin() -> None:
    """Raise 403 if gh_actions.tier != 'admin'."""
    caps = build_capabilities()
    if caps.gh_actions.tier != "admin":
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "gh_actions.admin_required",
                    "message": "GH Actions admin tier required",
                }
            },
        )


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def _get_gh_client() -> GitHubActionsClient:
    """Build a GitHubActionsClient from config (ADR-008 D18).

    Raises 503 when token or repo cannot be resolved.
    """
    token, repo = resolve_token_and_repo()
    if not token or not repo:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "gh_actions.config_missing",
                    "message": (
                        "GitHub token (GIT_PASSWORD) or repo (GIT_REPO_URL / "
                        "GH_ACTIONS_REPO) is not configured"
                    ),
                }
            },
        )
    return GitHubActionsClient(token=token, repo=repo)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_item(run: dict) -> RunItem:
    return RunItem(
        id=run["id"],
        name=run.get("name"),
        display_title=run.get("display_title"),
        status=run.get("status"),
        conclusion=run.get("conclusion"),
        event=run.get("event"),
        created_at=run.get("created_at"),
        updated_at=run.get("updated_at"),
        head_sha=run.get("head_sha"),
        run_number=run.get("run_number"),
    )


def _wrap_gh_error(exc: Exception) -> HTTPException:
    """Convert httpx or other errors into a clean 502 response."""
    msg = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        msg = f"GitHub API returned {exc.response.status_code}: {exc.response.text[:200]}"
    return HTTPException(
        status_code=502,
        detail={
            "error": {
                "code": "gh_actions.api_error",
                "message": msg,
            }
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/workflows",
    response_model=WorkflowsResponse,
    dependencies=[Depends(_require_gh_monitor)],
)
def list_workflows(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> WorkflowsResponse:
    """List workflows, each enriched with its latest run."""
    client = _get_gh_client()
    try:
        raw_workflows = client.list_workflows()
        # Fetch recent repo-wide runs once and bucket by workflow_id to avoid
        # an N+1 call per workflow. Runs come back newest-first, so the first
        # run seen for a given workflow id is its latest.
        latest_by_wf: dict[int, dict] = {}
        for run in client.list_runs(per_page=100):
            wf_id = run.get("workflow_id")
            if wf_id is not None and wf_id not in latest_by_wf:
                latest_by_wf[wf_id] = run
        result: list[WorkflowItem] = []
        for wf in raw_workflows:
            run = latest_by_wf.get(wf["id"])
            result.append(
                WorkflowItem(
                    id=wf["id"],
                    name=wf.get("name", ""),
                    state=wf.get("state"),
                    last_run=_run_to_item(run) if run else None,
                )
            )
        return WorkflowsResponse(workflows=result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


@router.get(
    "/runs",
    response_model=RunsResponse,
    dependencies=[Depends(_require_gh_monitor)],
)
def list_runs(
    workflow: Optional[int] = Query(default=None, description="Workflow ID to filter by"),
    _user: Dict[str, Any] = Depends(_require_auth),
) -> RunsResponse:
    """List workflow runs, optionally filtered by workflow ID."""
    client = _get_gh_client()
    try:
        raw_runs = client.list_runs(workflow_id=workflow)
        return RunsResponse(runs=[_run_to_item(r) for r in raw_runs])
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


@router.post(
    "/runs",
    response_model=DispatchResponse,
    dependencies=[Depends(_require_gh_monitor)],
)
def dispatch_run(
    body: DispatchRequest,
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> DispatchResponse:
    """Dispatch a workflow run (admin only)."""
    client = _get_gh_client()
    try:
        client.dispatch_workflow(
            workflow_id=body.workflow_id,
            ref=body.ref,
            inputs=body.inputs,
        )
        return DispatchResponse(dispatched=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


@router.get(
    "/runs/{run_id}/logs",
    response_model=RunLogsResponse,
    dependencies=[Depends(_require_gh_monitor)],
)
def get_run_logs(
    run_id: int,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> RunLogsResponse:
    """Return the logs download URL for a run."""
    client = _get_gh_client()
    try:
        logs_url = client.get_run_logs_url(run_id)
        return RunLogsResponse(logs_url=logs_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Workflow YAML editor endpoints
# ---------------------------------------------------------------------------


def _validate_workflow_name(name: str) -> None:
    """Raise 400 if name contains path traversal or is not a YAML filename."""
    if not _SAFE_WORKFLOW_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "gh_actions.invalid_filename",
                    "message": "Workflow name must be a simple .yml/.yaml filename",
                }
            },
        )


@router.get(
    "/workflows/{name}",
    response_model=WorkflowContentResponse,
    dependencies=[Depends(_require_gh_edit)],
)
def get_workflow_content(
    name: str,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> WorkflowContentResponse:
    """Return the decoded content of a workflow YAML file."""
    _validate_workflow_name(name)
    client = _get_gh_client()
    try:
        data = client.get_workflow_content(name)
        return WorkflowContentResponse(
            content=data["content"],
            sha=data["sha"],
            path=data["path"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


@router.put(
    "/workflows/{name}",
    response_model=WorkflowUpdateResponse,
    dependencies=[Depends(_require_gh_edit)],
)
def update_workflow_content(
    name: str,
    body: WorkflowUpdateRequest,
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> WorkflowUpdateResponse:
    """Validate and commit an updated workflow YAML file (admin only)."""
    _validate_workflow_name(name)
    try:
        yaml.safe_load(body.content)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "gh_actions.invalid_yaml",
                    "message": f"Invalid YAML: {exc}",
                }
            },
        )

    client = _get_gh_client()
    try:
        result = client.update_workflow_content(
            name,
            body.content,
            body.sha,
            body.commit_message,
            body.branch,
        )
        return WorkflowUpdateResponse(
            updated=True,
            commit_sha=result["commit_sha"],
            validation_warnings=[],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Secrets endpoints (admin tier)
# ---------------------------------------------------------------------------


def _validate_secret_name(name: str) -> None:
    """Raise 400 if name is not a valid GitHub Actions secret name.

    GitHub secret names must be uppercase alphanumeric + underscores,
    starting with a letter.
    """
    if not _SAFE_SECRET_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "gh_actions.invalid_secret_name",
                    "message": (
                        "Secret name must be uppercase letters, digits, and underscores, "
                        "starting with a letter"
                    ),
                }
            },
        )


@router.get(
    "/secrets",
    response_model=SecretsResponse,
    dependencies=[Depends(_require_gh_admin)],
)
def list_secrets(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> SecretsResponse:
    """List repository Actions secrets metadata (admin tier)."""
    client = _get_gh_client()
    try:
        raw = client.list_secrets()
        return SecretsResponse(
            secrets=[
                SecretItem(
                    name=s["name"],
                    created_at=s["created_at"],
                    updated_at=s["updated_at"],
                )
                for s in raw
            ]
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


@router.post(
    "/secrets",
    response_model=CreateSecretResponse,
    dependencies=[Depends(_require_gh_admin)],
)
def create_secret(
    body: CreateSecretRequest,
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> CreateSecretResponse:
    """Create or update a repository Actions secret (admin tier, admin role)."""
    _validate_secret_name(body.name)
    client = _get_gh_client()
    try:
        client.create_or_update_secret(body.name, body.value)
        return CreateSecretResponse(created=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


@router.delete(
    "/secrets/{name}",
    response_model=DeleteSecretResponse,
    dependencies=[Depends(_require_gh_admin)],
)
def delete_secret(
    name: str,
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> DeleteSecretResponse:
    """Delete a repository Actions secret (admin tier, admin role)."""
    _validate_secret_name(name)
    client = _get_gh_client()
    try:
        client.delete_secret(name)
        return DeleteSecretResponse(deleted=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_gh_error(exc) from exc
    finally:
        client.close()


__all__ = [
    "create_secret",
    "delete_secret",
    "dispatch_run",
    "get_run_logs",
    "get_workflow_content",
    "list_runs",
    "list_secrets",
    "list_workflows",
    "router",
    "update_workflow_content",
]
