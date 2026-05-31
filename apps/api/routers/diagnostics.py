"""JavDB session diagnostics endpoints.

GET  /api/diag/javdb-session         — cookie status, expiry, last refresh time
POST /api/diag/javdb-session/refresh — refresh javdb session (headless or cookie_paste)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.diagnostics import (
    EvidenceRefSchema,
    JavdbSessionRefreshRequest,
    JavdbSessionRefreshResponse,
    JavdbSessionStatus,
    OpsIncidentListResponse,
    OpsIncidentSchema,
)
from javdb.infra.config import cfg
from javdb.storage.db import OPERATIONS_DB_PATH, REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
from javdb.storage.repos.system_state_repo import SystemStateRepo

router = APIRouter(prefix="/api/diag", tags=["diagnostics"])

logger = logging.getLogger(__name__)

_KEY_LAST_REFRESH = "last_javdb_refresh"


def _get_last_refresh_time() -> str | None:
    """Read last_javdb_refresh from system_state KV, return None on any failure."""
    try:
        with get_db(OPERATIONS_DB_PATH) as conn:
            return SystemStateRepo(conn).get(_KEY_LAST_REFRESH)
    except Exception:
        return None


def _set_last_refresh_time(ts: str) -> None:
    """Write last_javdb_refresh to system_state KV."""
    with get_db(OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put(_KEY_LAST_REFRESH, ts)


def _is_refresh_recent(last_refresh_time: str | None, max_age_hours: int = 24) -> bool:
    """Return True if last_refresh_time exists and is within max_age_hours."""
    if not last_refresh_time:
        return False
    try:
        dt = datetime.fromisoformat(last_refresh_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = now - dt
        # A future timestamp (clock drift / dirty data) is not "recent".
        return timedelta(0) <= age < timedelta(hours=max_age_hours)
    except Exception:
        return False


def _cookie_preview(cookie: str) -> str:
    """Return first 8 chars + '...' as a preview (no ellipsis if not truncated)."""
    return cookie[:8] + ("..." if len(cookie) > 8 else "")


def _list_ops_incident_records(
    *,
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).list(
            status=status,
            run_id=run_id,
            session_id=session_id,
            limit=limit,
        )


def _get_ops_incident_record(incident_id: str):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).get(incident_id)


def _json_list_field(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _evidence_refs_field(raw: str | None) -> list[EvidenceRefSchema]:
    refs: list[EvidenceRefSchema] = []
    for item in _json_list_field(raw):
        if not isinstance(item, dict):
            continue
        try:
            refs.append(EvidenceRefSchema(**item))
        except (TypeError, ValidationError):
            continue
    return refs


def _ops_record_to_schema(record) -> OpsIncidentSchema:
    return OpsIncidentSchema(
        incident_id=record.incident_id,
        trigger_source=record.trigger_source,
        run_id=record.run_id,
        run_attempt=record.run_attempt,
        session_id=record.session_id,
        incident_type=record.incident_type,
        status=record.status,
        persistence_status=record.persistence_status,
        model_version=record.model_version,
        detector_version=record.detector_version,
        confidence=record.confidence,
        confirmed_findings=_json_list_field(record.confirmed_findings_json),
        likely_causes=_json_list_field(record.likely_causes_json),
        unknowns=_json_list_field(record.unknowns_json),
        recommended_next_actions=_json_list_field(record.recommended_next_actions_json),
        unsafe_actions=_json_list_field(record.unsafe_actions_json),
        evidence_refs=_evidence_refs_field(record.evidence_refs_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
        resolved_at=record.resolved_at,
    )


@router.get("/javdb-session", response_model=JavdbSessionStatus)
def get_javdb_session_status(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> JavdbSessionStatus:
    """Return current JavDB session cookie status."""
    cookie = cfg("JAVDB_SESSION_COOKIE", "") or ""
    last_refresh = _get_last_refresh_time()

    is_admin = _user.get("role") == "admin"
    return JavdbSessionStatus(
        cookie_present=bool(cookie),
        cookie_value_preview=_cookie_preview(cookie) if cookie and is_admin else None,
        last_refresh_time=last_refresh,
        estimated_expiry=None,  # cannot derive real expiry from the cookie string
        # A recent refresh is only meaningful if a cookie is actually present;
        # avoid the contradictory (is_likely_valid=True, cookie_present=False) pair.
        is_likely_valid=bool(cookie) and _is_refresh_recent(last_refresh),
    )


@router.get("/ops-incidents", response_model=OpsIncidentListResponse)
def list_ops_incidents(
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentListResponse:
    """Return persisted read-only operations diagnosis incidents."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be a positive integer")

    items = _list_ops_incident_records(
        status=status,
        run_id=run_id,
        session_id=session_id,
        limit=min(limit, 100),
    )
    return OpsIncidentListResponse(
        items=[_ops_record_to_schema(item) for item in items]
    )


@router.get("/ops-incidents/{incident_id}", response_model=OpsIncidentSchema)
def get_ops_incident(
    incident_id: str,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentSchema:
    """Return one persisted read-only operations diagnosis incident."""
    record = _get_ops_incident_record(incident_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _ops_record_to_schema(record)


@router.post("/javdb-session/refresh", response_model=JavdbSessionRefreshResponse)
async def refresh_javdb_session_diag(
    body: JavdbSessionRefreshRequest,
    current: Dict[str, Any] = Depends(require_role("admin")),
) -> JavdbSessionRefreshResponse:
    """Refresh the JavDB session via headless login or cookie paste."""
    method = body.method

    if method == "cookie_paste":
        # Validate cookie_value is present and non-empty
        cookie_value = (body.cookie_value or "").strip()
        if not cookie_value:
            raise HTTPException(
                status_code=422,
                detail="cookie_value is required when method='cookie_paste'",
            )

        # Persisting the cookie is the critical step — a failure here is a real
        # error. Recording the refresh timestamp is best-effort: if it fails the
        # cookie is still saved, so it must not flip the response to success=False.
        try:
            from apps.api.services import config_service  # noqa: PLC0415

            config_service.update_config_payload(
                {"JAVDB_SESSION_COOKIE": cookie_value}, current["sub"]
            )
        except Exception as exc:
            return JavdbSessionRefreshResponse(
                success=False,
                method=method,
                error=f"Failed to persist cookie: {exc}",
            )

        try:
            _set_last_refresh_time(datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.warning("Failed to persist last_javdb_refresh: %s", exc)

        return JavdbSessionRefreshResponse(
            success=True,
            method=method,
            new_cookie_preview=_cookie_preview(cookie_value),
        )

    elif method == "headless":
        # Reuse the existing system_service machinery (attempt_login_refresh)
        # which handles proxy selection, error categorization, and config
        # persistence.  We pass a minimal payload with proxy_mode='auto'.
        try:
            from apps.api.schemas.payloads import JavdbLoginRefreshPayload  # noqa: PLC0415
            from apps.api.services import system_service  # noqa: PLC0415

            payload = JavdbLoginRefreshPayload(proxy_mode="auto")
            result = await system_service.refresh_javdb_session_with_options(
                payload, current["sub"]
            )
        except Exception as exc:
            return JavdbSessionRefreshResponse(
                success=False,
                method=method,
                error=str(exc),
            )

        if result.get("status") == "ok":
            # Refresh succeeded — record timestamp and return new cookie preview.
            ts = datetime.now(timezone.utc).isoformat()
            try:
                _set_last_refresh_time(ts)
            except Exception as exc:
                # non-fatal; refresh itself succeeded — log and continue
                logger.warning("Failed to persist last_javdb_refresh: %s", exc)

            new_cookie = cfg("JAVDB_SESSION_COOKIE", "") or ""
            return JavdbSessionRefreshResponse(
                success=True,
                method=method,
                new_cookie_preview=_cookie_preview(new_cookie) if new_cookie else None,
            )
        else:
            return JavdbSessionRefreshResponse(
                success=False,
                method=method,
                error=result.get("message") or "Headless login failed",
            )

    else:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown method: {method!r}. Must be 'headless' or 'cookie_paste'.",
        )


__all__ = [
    "get_javdb_session_status",
    "get_ops_incident",
    "list_ops_incidents",
    "refresh_javdb_session_diag",
    "router",
]
