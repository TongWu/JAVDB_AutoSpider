"""JavDB session diagnostics endpoints.

GET  /api/diag/javdb-session         — cookie status, expiry, last refresh time
POST /api/diag/javdb-session/refresh — refresh javdb session (headless or cookie_paste)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.diagnostics import (
    JavdbSessionRefreshRequest,
    JavdbSessionRefreshResponse,
    JavdbSessionStatus,
)
from javdb.infra.config import cfg
from javdb.storage.db import db_connection
from javdb.storage.repos.system_state_repo import SystemStateRepo

router = APIRouter(prefix="/api/diag", tags=["diagnostics"])

logger = logging.getLogger(__name__)

_KEY_LAST_REFRESH = "last_javdb_refresh"


def _get_last_refresh_time() -> str | None:
    """Read last_javdb_refresh from system_state KV, return None on any failure."""
    try:
        with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
            return SystemStateRepo(conn).get(_KEY_LAST_REFRESH)
    except Exception:
        return None


def _set_last_refresh_time(ts: str) -> None:
    """Write last_javdb_refresh to system_state KV."""
    with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put(_KEY_LAST_REFRESH, ts)


def _is_refresh_recent(last_refresh_time: str | None, max_age_hours: int = 24) -> bool:
    """Return True if last_refresh_time exists and is within max_age_hours."""
    if not last_refresh_time:
        return False
    try:
        dt = datetime.fromisoformat(last_refresh_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt) < timedelta(hours=max_age_hours)
    except Exception:
        return False


def _cookie_preview(cookie: str) -> str:
    """Return first 8 chars + '...' as a preview (no ellipsis if not truncated)."""
    return cookie[:8] + ("..." if len(cookie) > 8 else "")


@router.get("/javdb-session", response_model=JavdbSessionStatus)
def get_javdb_session_status(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> JavdbSessionStatus:
    """Return current JavDB session cookie status."""
    cookie = cfg("JAVDB_SESSION_COOKIE", "") or ""
    last_refresh = _get_last_refresh_time()

    return JavdbSessionStatus(
        cookie_present=bool(cookie),
        cookie_value_preview=_cookie_preview(cookie) if cookie else None,
        last_refresh_time=last_refresh,
        estimated_expiry=None,  # cannot derive real expiry from the cookie string
        # A recent refresh is only meaningful if a cookie is actually present;
        # avoid the contradictory (is_likely_valid=True, cookie_present=False) pair.
        is_likely_valid=bool(cookie) and _is_refresh_recent(last_refresh),
    )


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

        # Persist the cookie via config_service (same mechanism as the existing
        # login/refresh endpoint) and record the refresh timestamp.
        try:
            from apps.api.services import config_service  # noqa: PLC0415

            config_service.update_config_payload(
                {"JAVDB_SESSION_COOKIE": cookie_value}, current["sub"]
            )
            ts = datetime.now(timezone.utc).isoformat()
            _set_last_refresh_time(ts)
        except Exception as exc:
            return JavdbSessionRefreshResponse(
                success=False,
                method=method,
                error=f"Failed to persist cookie: {exc}",
            )

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
        return JavdbSessionRefreshResponse(
            success=False,
            method=method,
            error=f"Unknown method: {method!r}. Must be 'headless' or 'cookie_paste'.",
        )


__all__ = [
    "get_javdb_session_status",
    "refresh_javdb_session_diag",
    "router",
]
