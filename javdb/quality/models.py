"""Typed records for ADR-024 torrent quality evidence and evaluation.

`EvidenceRecord` mirrors `TorrentQualityEvidence` (torrent-level objective
facts). `EvaluationRecord` mirrors `TorrentQualityEvaluation` (movie-context
shadow scoring). Both are pure domain objects: serialization to DB rows lives in
`TorrentQualityRepo`, not here.

Feature-storage contract (ADR-024 review, 2026-05-31): the promoted typed fields
on `EvidenceRecord` are the queryable columns. `EvidenceRecord.features` holds
ONLY not-yet-promoted experimental/summary features and must never reuse a
promoted column name; the repo enforces this and a unit test pins it.

`policy_mode` / `decision` / `would_replace_current_choice` / `shadow_rank` are
reserved for ADR-024 Phase 2/3; Phase 1 always sets `policy_mode="shadow"`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EvidenceRecord:
    info_hash: str
    probe_schema_version: str
    target_role: str  # "production_download" | "quality_probe"
    probe_target_name: Optional[str] = None
    metadata_status: Optional[str] = None
    metadata_started_at: Optional[str] = None
    metadata_completed_at: Optional[str] = None
    total_size_bytes: Optional[int] = None
    main_video_size_bytes: Optional[int] = None
    main_video_ratio: Optional[float] = None
    video_file_count: Optional[int] = None
    subtitle_file_count: Optional[int] = None
    non_video_file_count: Optional[int] = None
    junk_size_bytes: Optional[int] = None
    junk_size_ratio: Optional[float] = None
    suspicious_file_count: Optional[int] = None
    # Non-promoted experimental/summary features only (never a promoted column).
    features: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    source_fingerprint: Optional[str] = None


@dataclass
class EvaluationRecord:
    info_hash: str
    movie_href: str
    scoring_version: str
    video_code: Optional[str] = None
    javdb_category: Optional[str] = None
    magnet_name: Optional[str] = None
    javdb_tags: list[str] = field(default_factory=list)
    javdb_size_text: Optional[str] = None
    inferred_category: Optional[str] = None
    category_consistent: Optional[bool] = None
    subtitle_evidence: Optional[str] = None
    resolution_consistent: Optional[bool] = None
    source_trust: Optional[str] = None
    score: Optional[float] = None
    shadow_rank: Optional[int] = None
    would_replace_current_choice: Optional[bool] = None
    policy_mode: str = "shadow"
    decision: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
