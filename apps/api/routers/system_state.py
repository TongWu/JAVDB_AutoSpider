"""GET /api/system/state and PUT /api/system/state — generic KV store endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from apps.api.schemas.capabilities_payloads import (
    SystemStateGetResponse,
    SystemStatePutPayload,
)
from apps.api.infra.auth import _require_auth, require_role
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo
from packages.python.javdb_platform.db_connection import get_db, OPERATIONS_DB_PATH

router = APIRouter(prefix="/api/system", tags=["system-state"])


@router.get("/state", response_model=SystemStateGetResponse)
def get_state(
    key: str = Query(..., min_length=1),
    _user=Depends(_require_auth),
) -> SystemStateGetResponse:
    with get_db(OPERATIONS_DB_PATH) as conn:
        value = SystemStateRepo(conn).get(key)
    return SystemStateGetResponse(key=key, value=value)


@router.put("/state", response_model=SystemStateGetResponse)
def put_state(
    payload: SystemStatePutPayload,
    _user=Depends(require_role("admin")),
) -> SystemStateGetResponse:
    with get_db(OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put(payload.key, payload.value)
    return SystemStateGetResponse(key=payload.key, value=payload.value)
