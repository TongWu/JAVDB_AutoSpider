"""Onboarding endpoints: status, test, complete, dismiss-hint."""
from __future__ import annotations

import os
from fastapi import APIRouter, Depends

from apps.api.schemas.capabilities_payloads import OnboardingStatusResponse
from apps.api.infra.auth import _require_auth, require_role
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo
from packages.python.javdb_platform.db_connection import get_db, OPERATIONS_DB_PATH


REQUIRED_COMPONENTS = ("javdb_session", "qb")
SKIPPABLE_COMPONENTS = ("smtp", "pikpak", "rclone", "proxy")


def _is_configured(component: str) -> bool:
    if component == "javdb_session":
        return bool(os.getenv("JAVDB_SESSION_COOKIE") or os.getenv("JAVDB_USERNAME"))
    if component == "qb":
        return bool(os.getenv("QB_URL"))
    if component == "smtp":
        return bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER"))
    if component == "pikpak":
        return bool(os.getenv("PIKPAK_USERNAME"))
    if component == "rclone":
        return bool(os.getenv("RCLONE_REMOTE"))
    if component == "proxy":
        return bool(os.getenv("PROXY_HTTP") or os.getenv("PROXY_POOL"))
    return False


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _read_onboarded() -> bool:
    with get_db(OPERATIONS_DB_PATH) as conn:
        return SystemStateRepo(conn).get("onboarded") == "true"


@router.get("/status", response_model=OnboardingStatusResponse)
def get_status(_user=Depends(_require_auth)) -> OnboardingStatusResponse:
    required_missing = [c for c in REQUIRED_COMPONENTS if not _is_configured(c)]
    skippable_missing = [c for c in SKIPPABLE_COMPONENTS if not _is_configured(c)]
    return OnboardingStatusResponse(
        completed=_read_onboarded(),
        required_missing=required_missing,
        skippable_missing=skippable_missing,
    )
