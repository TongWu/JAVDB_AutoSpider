"""Configuration routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from apps.api.infra.auth import _require_auth, require_role
from apps.api.services import config_service

router = APIRouter(prefix="/api")


@router.get("/config")
async def get_config(current=Depends(_require_auth)):
    return config_service.get_config_payload(current["sub"])


@router.get("/config/meta")
async def get_config_meta(_: Dict[str, Any] = Depends(_require_auth)):
    return config_service.get_config_meta_payload()


@router.put("/config")
async def update_config(
    config_updates: Dict[str, Any],
    current=Depends(require_role("admin")),
):
    return config_service.update_config_payload(config_updates, current["sub"])


__all__ = ["get_config", "get_config_meta", "router", "update_config"]
