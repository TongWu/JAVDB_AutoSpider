from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class GhActions(BaseModel):
    tier: Literal["none", "monitor", "edit", "admin"]
    repo: str | None
    token_configured: bool


class Features(BaseModel):
    pikpak: bool
    rclone: bool
    smtp: bool
    proxy_pool: bool
    javdb_login: bool
    proxy_preview: bool


class Build(BaseModel):
    frontend_version: str | None = None
    backend_version: str
    git_sha: str


class CapabilitiesResponse(BaseModel):
    version: str = Field(default="2.0.0", description="Capabilities schema version")
    ingestion_mode: Literal["local", "github", "dual"]
    gh_actions: GhActions
    storage_backend: Literal["sqlite", "d1", "dual"]
    features: Features
    deployment: Literal["colocated", "split", "unknown"]
    build: Build


class SystemStateGetResponse(BaseModel):
    key: str
    value: str | None


class SystemStatePutPayload(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str


class OnboardingStatusResponse(BaseModel):
    completed: bool
    required_missing: list[str]
    skippable_missing: list[str]


class OnboardingTestPayload(BaseModel):
    component: Literal["javdb", "qb", "proxy", "smtp"]


class OnboardingTestResponse(BaseModel):
    component: str
    ok: bool
    message: str
    details: dict | None = None


class DismissHintPayload(BaseModel):
    hint_id: str = Field(min_length=1, max_length=64)


# ── Sessions (Tasks 12-15) ────────────────────────────────────────────────────

class SessionItem(BaseModel):
    session_id: str
    state: str
    write_mode: str
    run_id: str | None
    run_attempt: int | None
    created_at: str


class SessionListResponse(BaseModel):
    items: list[SessionItem]
    next_cursor: str | None
    total_estimate: int | None = None


class SessionDetailResponse(BaseModel):
    session: SessionItem
    movies: list[dict]
    torrents: list[dict]


class SessionRollbackPayload(BaseModel):
    dry_run: bool = True
    include_pending: bool = True
    restore_from_audit: bool = True


class SessionRollbackResponse(BaseModel):
    session_id: str
    dry_run: bool
    actions: list[dict]
    summary: dict


class SessionCommitPayload(BaseModel):
    force: bool = False
    drop_pending: bool = False
    # Default True on the HTTP path so the API matches the CLI's behaviour
    # (CLI sets both to True). The library's CommitRequest defaults them to
    # False for safety; callers wanting a DB-only commit can opt out here.
    fanout_claims: bool = True
    emit_metrics: bool = True


class SessionCommitResponse(BaseModel):
    session_id: str
    new_state: str
    pending_dropped: int = 0
