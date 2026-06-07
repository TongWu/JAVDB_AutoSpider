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
    OpsIncidentAnalyticsResponse,
    OpsIncidentListResponse,
    OpsIncidentSchema,
    OpsIncidentSimilarityResponse,
    ParseFieldHealthItem,
    ParseFieldHealthResponse,
    SimilarIncidentSchema,
)
from javdb.infra.config import cfg
from javdb.ops.diagnosis.analytics import summarize_incidents
from javdb.ops.diagnosis.similarity import rank_similar_incidents
from javdb.ops.sentinel.health import compute_field_health
from javdb.storage.db import OPERATIONS_DB_PATH, REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo
from javdb.storage.repos.system_state_repo import SystemStateRepo

router = APIRouter(prefix="/api/diag", tags=["diagnostics"])

logger = logging.getLogger(__name__)

_KEY_LAST_REFRESH = "last_javdb_refresh"

# Analytics and similarity use a wider candidate window than the public list
# endpoint (which caps at 100) so that aggregations reflect the full incident
# history rather than being silently truncated.
_ANALYTICS_WINDOW = 500
_SIMILARITY_CANDIDATE_LIMIT = 500


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
    incident_type: str | None = None,
    confidence: str | None = None,
    limit: int = 50,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).list(
            status=status,
            run_id=run_id,
            session_id=session_id,
            incident_type=incident_type,
            confidence=confidence,
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


def _field_health_to_schema(h) -> ParseFieldHealthItem:
    return ParseFieldHealthItem(
        page_type=h.page_type,
        field=h.field,
        severity=h.severity,
        fill_rate=h.fill_rate,
        sample_count=h.sample_count,
        observed_at=h.observed_at,
        baseline=h.baseline,
        threshold=h.threshold,
        status=h.status,
    )


def _field_health_items(repo, min_sample: int, window: int) -> list[ParseFieldHealthItem]:
    rows = repo.latest_committed_fills()
    # Exclude each displayed row from its own baseline (via `before=observed_at`) so
    # the status mirrors the gate detector's pure-historical baseline — the run being
    # judged is not part of the history it is compared against. Without this, a lone
    # committed run reads as `ok` instead of `no_baseline`, and a short history lets
    # the current outlier drag down its own threshold and mask `soft_drift`.
    latest_at = {(pt, f): observed_at for (pt, f, _rate, _n, observed_at) in rows}
    health = compute_field_health(
        rows, min_sample=min_sample,
        baseline_fn=lambda pt, f: repo.baseline(
            pt, f, window=window, before=latest_at.get((pt, f))),
    )
    return [_field_health_to_schema(h) for h in health]


def _compute_parse_field_health(*, repo=None) -> list[ParseFieldHealthItem]:
    min_sample = int(cfg("SENTINEL_MIN_SAMPLE", 30))
    window = int(cfg("SENTINEL_BASELINE_WINDOW", 14))
    if repo is not None:
        return _field_health_items(repo, min_sample, window)
    with get_db(REPORTS_DB_PATH) as conn:
        return _field_health_items(ParseRunFieldFillRepo(conn), min_sample, window)


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


_ERROR_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
    "required": ["detail"],
}

_400_LIMIT_RESPONSE = {
    400: {
        "description": "limit must be a positive integer",
        "content": {"application/json": {"schema": _ERROR_DETAIL_SCHEMA}},
    }
}

_404_FEATURES_RESPONSE = {
    404: {
        "description": "Incident features not found",
        "content": {"application/json": {"schema": _ERROR_DETAIL_SCHEMA}},
    }
}


@router.get("/ops-incidents", response_model=OpsIncidentListResponse, responses=_400_LIMIT_RESPONSE)
def list_ops_incidents(
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    incident_type: str | None = None,
    confidence: str | None = None,
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
        incident_type=incident_type,
        confidence=confidence,
        limit=min(limit, 100),
    )
    return OpsIncidentListResponse(
        items=[_ops_record_to_schema(item) for item in items]
    )


@router.get("/ops-incidents/analytics", response_model=OpsIncidentAnalyticsResponse)
def get_ops_incident_analytics(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentAnalyticsResponse:
    """Return aggregated analytics over persisted operations incidents."""
    records = _list_ops_incident_records(limit=_ANALYTICS_WINDOW)
    return OpsIncidentAnalyticsResponse(**summarize_incidents(records))


@router.get("/parse-field-health", response_model=ParseFieldHealthResponse)
def get_parse_field_health(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> ParseFieldHealthResponse:
    """Latest committed per-field parse health (ADR-035 site-contract sentinel)."""
    return ParseFieldHealthResponse(items=_compute_parse_field_health())


def _similar_ops_incident_records(incident_id: str, *, limit: int = 5):
    with get_db(REPORTS_DB_PATH) as conn:
        repo = OpsIncidentRepo(conn)
        target = repo.get_features(incident_id)
        if target is None:
            return None
        candidates = repo.list_features(limit=_SIMILARITY_CANDIDATE_LIMIT)
        return rank_similar_incidents(target, candidates, limit=limit)


@router.get(
    "/ops-incidents/{incident_id}/similar",
    response_model=OpsIncidentSimilarityResponse,
    responses={**_400_LIMIT_RESPONSE, **_404_FEATURES_RESPONSE},
)
def get_similar_ops_incidents(
    incident_id: str,
    limit: int = 5,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentSimilarityResponse:
    """Return incidents most similar to the given incident, ranked by feature overlap."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be a positive integer")
    items = _similar_ops_incident_records(incident_id, limit=min(limit, 20))
    if items is None:
        raise HTTPException(status_code=404, detail="Incident features not found")
    return OpsIncidentSimilarityResponse(
        incident_id=incident_id,
        items=[
            SimilarIncidentSchema(
                incident_id=item.incident_id,
                score=item.score,
                matched_reasons=item.matched_reasons,
            )
            for item in items
        ],
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
    "get_ops_incident_analytics",
    "get_parse_field_health",
    "get_similar_ops_incidents",
    "list_ops_incidents",
    "refresh_javdb_session_diag",
    "router",
]
