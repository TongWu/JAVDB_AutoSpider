"""Pydantic schemas for GitHub Actions endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class DispatchRequest(BaseModel):
    workflow_id: int
    ref: str = "main"
    inputs: Optional[dict] = None


# ---------------------------------------------------------------------------
# Response schemas — keep minimal; only fields the frontend needs
# ---------------------------------------------------------------------------


class RunItem(BaseModel):
    id: int
    name: Optional[str] = None
    display_title: Optional[str] = None
    status: Optional[str] = None
    conclusion: Optional[str] = None
    event: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    head_sha: Optional[str] = None
    run_number: Optional[int] = None


class WorkflowItem(BaseModel):
    id: int
    name: str
    state: Optional[str] = None
    last_run: Optional[RunItem] = None


class WorkflowsResponse(BaseModel):
    workflows: list[WorkflowItem]


class RunsResponse(BaseModel):
    runs: list[RunItem]


class RunLogsResponse(BaseModel):
    logs_url: str


class DispatchResponse(BaseModel):
    dispatched: bool


# ---------------------------------------------------------------------------
# Workflow YAML editor schemas
# ---------------------------------------------------------------------------


class WorkflowUpdateRequest(BaseModel):
    content: str
    sha: str
    commit_message: str
    branch: str = "main"


class WorkflowContentResponse(BaseModel):
    content: str
    sha: str
    path: str


class WorkflowUpdateResponse(BaseModel):
    updated: bool
    commit_sha: str
    validation_warnings: list[str]


# ---------------------------------------------------------------------------
# Secrets schemas
# ---------------------------------------------------------------------------


class SecretItem(BaseModel):
    name: str
    created_at: str
    updated_at: str


class SecretsResponse(BaseModel):
    secrets: list[SecretItem]


class CreateSecretRequest(BaseModel):
    name: str
    value: str


class CreateSecretResponse(BaseModel):
    created: bool


class DeleteSecretResponse(BaseModel):
    deleted: bool


__all__ = [
    "CreateSecretRequest",
    "CreateSecretResponse",
    "DeleteSecretResponse",
    "DispatchRequest",
    "DispatchResponse",
    "RunItem",
    "RunLogsResponse",
    "RunsResponse",
    "SecretItem",
    "SecretsResponse",
    "WorkflowContentResponse",
    "WorkflowItem",
    "WorkflowUpdateRequest",
    "WorkflowUpdateResponse",
    "WorkflowsResponse",
]
