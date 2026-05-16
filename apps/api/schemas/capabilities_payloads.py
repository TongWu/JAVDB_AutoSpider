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
