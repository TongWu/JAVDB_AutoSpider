"""Torrent quality evidence + assist endpoints (ADR-024)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.quality import (
    QualityRecommendationListResponse,
    QualityRecommendationSchema,
    ReviewLabelRequest,
    ReviewLabelResponse,
    TorrentQualityEvaluationListResponse,
    TorrentQualityEvaluationSchema,
    TorrentQualityEvidenceSchema,
)
from javdb.quality.features import PROBE_SCHEMA_VERSION
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo
from javdb.storage.repos.torrent_quality_review_repo import (
    ReviewLabel,
    TorrentQualityReviewRepo,
)

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
_AUTH_RESPONSES = {
    401: {"description": "Unauthorized", **_DETAIL_STRING_RESPONSE_SCHEMA},
    403: {"description": "Forbidden", **_DETAIL_STRING_RESPONSE_SCHEMA},
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


@contextmanager
def _review_repo() -> Iterator[TorrentQualityReviewRepo]:
    with get_db(REPORTS_DB_PATH) as conn:
        yield TorrentQualityReviewRepo(conn)


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
    data["resolution_consistent"] = _optional_bool(row.get("resolution_consistent"))
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
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "Bad Request", **_DETAIL_STRING_RESPONSE_SCHEMA},
    },
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
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "Not Found", **_DETAIL_STRING_RESPONSE_SCHEMA},
    },
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


# ---------------------------------------------------------------------------
# ADR-024 IMP-08: assist-mode endpoints
# ---------------------------------------------------------------------------

def _build_recommendations(rows: list[dict[str, Any]]) -> list[QualityRecommendationSchema]:
    """Group evaluation rows by javdb_category and build recommendation items.

    Evaluation rows carry no target_role, so "current" is identified via the
    ranking invariant instead: rank_candidates only sets
    would_replace_current_choice on the production row when a probe outranks it,
    and otherwise leaves production at shadow_rank==1. So for each category:
    - current: the row flagged would_replace_current_choice (the production
      download that got outranked); when none is flagged, production IS rank 1,
      so current falls back to the rank-1 row.
    - recommended: the row with shadow_rank == 1.
    - reason_diff: codes in recommended.reasons that are NOT in current.reasons.
    """
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cat = row.get("javdb_category") or "unknown"
        by_category[cat].append(row)

    items: list[QualityRecommendationSchema] = []
    for cat, cat_rows in by_category.items():
        # Find the shadow_rank==1 row as "recommended"
        rank1 = next(
            (r for r in cat_rows if r.get("shadow_rank") == 1),
            None,
        )
        # "current" = the production download. It is the row flagged
        # would_replace_current_choice (the production pick a probe outranked);
        # when nothing is flagged, production is already rank 1, so we fall back
        # to the rank-1 row below.
        current_row = next(
            (r for r in cat_rows if _optional_bool(r.get("would_replace_current_choice"))),
            None,
        )
        if current_row is None:
            # No replacement flag — use rank1 as both current and recommended
            # (production is the best; no replacement needed)
            current_row = rank1

        reason_diff: list[str] = []
        if rank1 is not None and current_row is not None and rank1 is not current_row:
            recommended_codes = set(_row_reasons(rank1))
            current_codes = set(_row_reasons(current_row))
            reason_diff = sorted(recommended_codes - current_codes)

        items.append(
            QualityRecommendationSchema(
                javdb_category=cat,
                current=_evaluation_from_row(current_row) if current_row else None,
                recommended=_evaluation_from_row(rank1) if rank1 else None,
                reason_diff=reason_diff,
            )
        )
    return items


@router.get(
    "/recommendations",
    response_model=QualityRecommendationListResponse,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "Bad Request", **_DETAIL_STRING_RESPONSE_SCHEMA},
    },
)
def list_recommendations(
    movie_href: Optional[str] = None,
    _user=Depends(_require_auth),
) -> QualityRecommendationListResponse:
    """Per-category recommendation: current vs best probe candidate + reason diff."""
    if not movie_href:
        raise HTTPException(status_code=400, detail="movie_href query parameter is required")

    with _repo() as repo:
        rows = repo.list_evaluations_for_movie(movie_href)

    return QualityRecommendationListResponse(items=_build_recommendations(rows))


@router.get(
    "/needs-review",
    response_model=TorrentQualityEvaluationListResponse,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "Bad Request", **_DETAIL_STRING_RESPONSE_SCHEMA},
    },
)
def list_needs_review(
    limit: _LimitQuery = 50,
    _user=Depends(_require_auth),
) -> TorrentQualityEvaluationListResponse:
    """Queue of evaluations that need operator review.

    Includes rows where decision='needs_review' OR would_replace_current_choice
    is true (a probe candidate outranked the production download). Limit defaults
    to 50, capped at 200.
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be a positive integer")

    with _repo() as repo:
        rows = repo.list_needs_review(limit=min(limit, 200))

    return TorrentQualityEvaluationListResponse(
        items=[_evaluation_from_row(row) for row in rows]
    )


@router.post(
    "/review-labels",
    response_model=ReviewLabelResponse,
    responses={
        **_AUTH_RESPONSES,
        422: {"description": "Unprocessable Entity"},
    },
)
def write_review_label(
    body: ReviewLabelRequest,
    _user=Depends(require_role("admin")),
) -> ReviewLabelResponse:
    """Record an operator review label (accept / reject / skip) for an evaluation.

    Admin-only: the labels are shared, tunable state (the dataset ADR-024 Phase 3
    tunes thresholds against), not per-user data, so a readonly JWT must not be
    able to overwrite them. ``require_role`` still runs ``_require_auth``, so the
    returned payload is the same JWT claims dict the reviewer identity reads from.

    Stamps reviewed_at server-side and stores the reviewer from the JWT subject.
    Idempotent: a second call with the same (info_hash, movie_href, scoring_version)
    overwrites the previous label.
    """
    reviewed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    reviewer = str(_user.get("sub", "") or "")

    label = ReviewLabel(
        info_hash=body.info_hash,
        movie_href=body.movie_href,
        scoring_version=body.scoring_version,
        label=body.label.value,
        reviewer=reviewer or None,
        note=body.note,
    )

    with _review_repo() as repo:
        repo.upsert_label(label, reviewed_at=reviewed_at)

    return ReviewLabelResponse(status="recorded")
