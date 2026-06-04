"""Schemas for /api/quality/* torrent-quality endpoints (ADR-024)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TorrentQualityEvidenceSchema(BaseModel):
    """Torrent-level objective evidence."""

    info_hash: str
    probe_schema_version: str
    target_role: str
    probe_target_name: Optional[str] = None
    metadata_status: Optional[str] = None
    total_size_bytes: Optional[int] = None
    main_video_size_bytes: Optional[int] = None
    main_video_ratio: Optional[float] = None
    video_file_count: Optional[int] = None
    subtitle_file_count: Optional[int] = None
    non_video_file_count: Optional[int] = None
    junk_size_bytes: Optional[int] = None
    junk_size_ratio: Optional[float] = None
    suspicious_file_count: Optional[int] = None
    reasons: list[str] = []


class TorrentQualityEvaluationSchema(BaseModel):
    """Movie-context shadow evaluation."""

    info_hash: str
    movie_href: str
    scoring_version: str
    video_code: Optional[str] = None
    javdb_category: Optional[str] = None
    magnet_name: Optional[str] = None
    inferred_category: Optional[str] = None
    category_consistent: Optional[bool] = None
    subtitle_evidence: Optional[str] = None
    score: Optional[float] = None
    shadow_rank: Optional[int] = None
    would_replace_current_choice: Optional[bool] = None
    policy_mode: Optional[str] = None
    decision: Optional[str] = None
    reasons: list[str] = []


class TorrentQualityEvaluationListResponse(BaseModel):
    items: list[TorrentQualityEvaluationSchema]


__all__ = [
    "TorrentQualityEvaluationListResponse",
    "TorrentQualityEvaluationSchema",
    "TorrentQualityEvidenceSchema",
]
