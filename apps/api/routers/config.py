"""Configuration routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.payloads import ConfigResponse, StatusOkResponse
from apps.api.services import config_service

router = APIRouter(prefix="/api")


@router.get("/config", response_model=ConfigResponse)
async def get_config(
    include_secrets: bool = Query(False, description="Admin-only: return unmasked secrets"),
    current=Depends(_require_auth),
):
    if include_secrets and current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="include_secrets requires admin role")
    return config_service.get_config_payload(current["sub"], include_secrets=include_secrets)


@router.get("/config/meta")
async def get_config_meta(_: Dict[str, Any] = Depends(_require_auth)):
    return config_service.get_config_meta_payload()


@router.put("/config", response_model=StatusOkResponse)
async def update_config(
    config_updates: Dict[str, Any],
    current=Depends(require_role("admin")),
):
    return config_service.update_config_payload(config_updates, current["sub"])


__all__ = ["get_config", "get_config_meta", "router", "update_config"]
