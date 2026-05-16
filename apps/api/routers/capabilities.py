from __future__ import annotations

import os
import subprocess
from importlib.metadata import PackageNotFoundError, version as pkg_version
from typing import cast

from fastapi import APIRouter, Depends

from apps.api.infra.auth import _require_auth
from apps.api.schemas.capabilities_payloads import (
    Build,
    CapabilitiesResponse,
    Features,
    GhActions,
)


def _get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def _backend_version() -> str:
    try:
        return pkg_version("javdb-autospider")
    except PackageNotFoundError:
        return os.getenv("BACKEND_VERSION", "0.0.0-dev")


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def build_capabilities() -> CapabilitiesResponse:
    ingestion_mode = cast(
        "Literal['local', 'github', 'dual']",
        os.getenv("INGESTION_MODE", "local"),
    )
    storage_backend = cast(
        "Literal['sqlite', 'd1', 'dual']",
        os.getenv("STORAGE_BACKEND", "sqlite"),
    )
    deployment = cast(
        "Literal['colocated', 'split', 'unknown']",
        os.getenv("DEPLOYMENT", "unknown"),
    )

    return CapabilitiesResponse(
        version="2.0.0",
        ingestion_mode=ingestion_mode,
        gh_actions=GhActions(
            tier=cast(
                "Literal['none', 'monitor', 'edit', 'admin']",
                os.getenv("GH_ACTIONS_TIER", "none"),
            ),
            repo=os.getenv("GH_ACTIONS_REPO") or None,
            token_configured=bool(os.getenv("GH_ACTIONS_TOKEN")),
        ),
        storage_backend=storage_backend,
        features=Features(
            pikpak=_bool_env("FEATURE_PIKPAK"),
            rclone=_bool_env("FEATURE_RCLONE"),
            smtp=bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")),
            proxy_pool=_bool_env("PROXY_MODE_POOL", default=True),
            javdb_login=bool(os.getenv("JAVDB_USERNAME")),
            proxy_preview=True,
        ),
        deployment=deployment,
        build=Build(
            frontend_version=os.getenv("FRONTEND_VERSION"),
            backend_version=_backend_version(),
            git_sha=_get_git_sha(),
        ),
    )


router = APIRouter(prefix="/api", tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities(_user=Depends(_require_auth)) -> CapabilitiesResponse:
    return build_capabilities()
