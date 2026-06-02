"""Torrent quality evidence read endpoints (ADR-024 Phase 1, read-only)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth
from apps.api.schemas.quality import (
    TorrentQualityEvaluationListResponse,
    TorrentQualityEvaluationSchema,
    TorrentQualityEvidenceSchema,
)
from javdb.quality.features import PROBE_SCHEMA_VERSION
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

router = APIRouter(prefix="/api/quality", tags=["quality"])
_PRODUCTION_ROLE = "production_download"
_DETAIL_STRING_RESPONSE_SCHEMA = {
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["detail"],
                "properties": {"detail": {"type": "string"}},
            }
        }
    }
}
_LimitQuery = Annotated[
    int,
    Query(
        description=(
            "Number of recent evaluations to return. Values greater than 200 "
            "are truncated to 200 by the server."
        ),
        json_schema_extra={"minimum": 1},
    ),
]


@contextmanager
def _repo() -> Iterator[TorrentQualityRepo]:
    with get_db(REPORTS_DB_PATH) as conn:
        yield TorrentQualityRepo(conn)


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def _list_from_jsonish(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, str)]
    return []


def _normalize_info_hash(value: str) -> str:
    return str(value or "").strip().lower()


def _row_reasons(row: dict[str, Any]) -> list[str]:
    reasons = _list_from_jsonish(row.get("reasons"))
    if reasons:
        return reasons
    return _list_from_jsonish(row.get("reasons_json"))


def _evaluation_from_row(row: dict[str, Any]) -> TorrentQualityEvaluationSchema:
    data = dict(row)
    data["category_consistent"] = _optional_bool(row.get("category_consistent"))
    data["would_replace_current_choice"] = _optional_bool(
        row.get("would_replace_current_choice")
    )
    data["reasons"] = _row_reasons(row)
    return TorrentQualityEvaluationSchema(**data)


def _evidence_from_row(row: dict[str, Any]) -> TorrentQualityEvidenceSchema:
    data = dict(row)
    data["reasons"] = _row_reasons(row)
    return TorrentQualityEvidenceSchema(**data)


@router.get(
    "/evaluations",
    response_model=TorrentQualityEvaluationListResponse,
    responses={400: {"description": "Bad Request", **_DETAIL_STRING_RESPONSE_SCHEMA}},
)
def list_evaluations(
    limit: _LimitQuery = 50,
    movie_href: Optional[str] = None,
    _user=Depends(_require_auth),
) -> TorrentQualityEvaluationListResponse:
    if limit <= 0:
        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer",
        )

    with _repo() as repo:
        if movie_href is not None:
            rows = repo.list_evaluations_for_movie(movie_href)
        else:
            rows = repo.list_recent_evaluations(limit=min(limit, 200))

    return TorrentQualityEvaluationListResponse(
        items=[_evaluation_from_row(row) for row in rows]
    )


@router.get(
    "/evidence/{info_hash}",
    response_model=TorrentQualityEvidenceSchema,
    responses={404: {"description": "Not Found", **_DETAIL_STRING_RESPONSE_SCHEMA}},
)
def get_evidence(
    info_hash: str,
    _user=Depends(_require_auth),
) -> TorrentQualityEvidenceSchema:
    normalized_hash = _normalize_info_hash(info_hash)
    with _repo() as repo:
        row = repo.get_evidence(normalized_hash, PROBE_SCHEMA_VERSION, _PRODUCTION_ROLE)

    if row is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return _evidence_from_row(row)
