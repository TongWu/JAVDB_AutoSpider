"""Content-filter CRUD API routes (ADR-040 Phase 4 / WS4a).

Delegates to the already-CRUD-complete ContentFilterRepo. ContentFilterRule
lives in REPORTS_DB (javdb-reports) — NOT HISTORY_DB. The legal (dimension,
mode) allow-list is imported from apps.cli.ops.content_filter (the single
source of truth), hand-mirrored in server/routes/content-filter.ts, and pinned
by tests/unit/test_content_filter_modes_parity.py. release_date values are
validated as strict ISO dates at this boundary (matching the TS route); regex
patterns are NOT compile-checked here — see _validate_value for why (cross-backend
JS/Python regex-dialect incompatibility; the ingestion engine fail-opens).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.content_filter import (
    ContentFilterRuleCreate,
    ContentFilterRuleEnabledUpdate,
    ContentFilterRuleListResponse,
    ContentFilterRuleResponse,
)
# Canonical allow-list + value validator (single source of truth, ADR-040 / WS4-D3).
# `validate_rule_value` enforces the same gender/age/release_date/value-required +
# ReDoS rules the CLI does; the TS Worker mirrors it (it cannot import Python).
from apps.cli.ops.content_filter import (
    VALID_RULE_MODES,
    VALUE_REQUIRED,
    validate_rule_value,
)
from javdb.spider.services.content_filter import Rule
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.content_filter_repo import ContentFilterRepo

router = APIRouter(prefix="/api/content-filter", tags=["content-filter"])

_NOT_FOUND = {
    "error": {"code": "content_filter.not_found", "message": "Rule not found"}
}


def _invalid_mode(dimension: str, mode: str) -> dict:
    return {
        "error": {
            "code": "content_filter.invalid_mode",
            "message": f"{dimension} rules do not support mode {mode!r}",
        }
    }


_VALUE_REQUIRED_ERR = {
    "error": {
        "code": "content_filter.value_required",
        "message": "this dimension/mode requires a non-empty value",
    }
}


_INVALID_VALUE_CODE = "content_filter.invalid_value"


def _rule_to_response(rule: Rule) -> ContentFilterRuleResponse:
    return ContentFilterRuleResponse(
        id=rule.id,
        dimension=rule.dimension,
        mode=rule.mode,
        value=rule.value,
        enabled=rule.enabled,
    )


@router.get("", response_model=ContentFilterRuleListResponse)
def list_rules(_user=Depends(_require_auth)):
    with get_db(_db.REPORTS_DB_PATH) as conn:
        rules = ContentFilterRepo(conn).list_rules()
    items = [_rule_to_response(r) for r in rules]
    return ContentFilterRuleListResponse(items=items, total=len(items))


@router.post("", response_model=ContentFilterRuleResponse, status_code=201)
def add_rule(body: ContentFilterRuleCreate, _admin=Depends(require_role("admin"))):
    rule_key = (body.dimension, body.mode)
    value = (body.value or "").strip()
    if rule_key not in VALID_RULE_MODES:
        raise HTTPException(status_code=422, detail=_invalid_mode(body.dimension, body.mode))
    if rule_key in VALUE_REQUIRED and not value:
        raise HTTPException(status_code=422, detail=_VALUE_REQUIRED_ERR)
    try:
        value = validate_rule_value(body.dimension, body.mode, value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": _INVALID_VALUE_CODE, "message": str(exc)}},
        )
    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        rule_id = repo.add_rule(body.dimension, body.mode, value)
        created = next((r for r in repo.list_rules() if r.id == rule_id), None)
    if created is None:  # pragma: no cover - the row was just inserted
        raise HTTPException(status_code=500, detail=_NOT_FOUND)
    return _rule_to_response(created)


@router.put("/{rule_id}", response_model=ContentFilterRuleResponse)
def set_enabled(
    rule_id: int,
    body: ContentFilterRuleEnabledUpdate,
    _admin=Depends(require_role("admin")),
):
    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        if not any(r.id == rule_id for r in repo.list_rules()):
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        repo.set_enabled(rule_id, body.enabled)
        updated = next(r for r in repo.list_rules() if r.id == rule_id)
    return _rule_to_response(updated)


@router.delete("/{rule_id}")
def remove_rule(rule_id: int, _admin=Depends(require_role("admin"))):
    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        existed = any(r.id == rule_id for r in repo.list_rules())
        if existed:
            repo.remove_rule(rule_id)
    return {"deleted": existed}
