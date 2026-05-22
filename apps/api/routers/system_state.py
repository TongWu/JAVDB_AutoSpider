"""GET /api/system/state and PUT /api/system/state — generic KV store endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from apps.api.schemas.capabilities_payloads import (
    SystemStateGetResponse,
    SystemStatePutPayload,
)
from apps.api.infra.auth import _require_auth, require_role
from javdb.storage.repos.system_state_repo import SystemStateRepo
import javdb.storage.db._db_connection as db_connection

router = APIRouter(prefix="/api/system", tags=["system-state"])


@router.get("/state", response_model=SystemStateGetResponse)
def get_state(
    key: str = Query(..., min_length=1),
    _user=Depends(_require_auth),
) -> SystemStateGetResponse:
    with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
        value = SystemStateRepo(conn).get(key)
    return SystemStateGetResponse(key=key, value=value)


@router.put("/state", response_model=SystemStateGetResponse)
def put_state(
    payload: SystemStatePutPayload,
    _user=Depends(require_role("admin")),
) -> SystemStateGetResponse:
    with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put(payload.key, payload.value)
    return SystemStateGetResponse(key=payload.key, value=payload.value)
