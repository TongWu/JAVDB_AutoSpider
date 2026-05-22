"""Pydantic schemas for all Operations endpoints (Phase 2, Tasks 3a and 3b).

All schemas in this file are defined upfront so Task 3b (Rclone + Cleanup)
can import them directly without needing to modify this file.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# qBittorrent
# ---------------------------------------------------------------------------


class QbTorrentItem(BaseModel):
    hash: str
    name: str
    size: int
    progress: float
    state: str
    category: str
    added_on: int
    completion_on: int


class QbTorrentsResponse(BaseModel):
    items: List[QbTorrentItem]
    total: int


class QbFilterSmallRequest(BaseModel):
    min_size_mb: float = 100.0
    days: int = 2
    dry_run: bool = True
    categories: Optional[List[str]] = None
    delete_local_files: bool = False


class QbFilterSmallResponse(BaseModel):
    filtered_count: int
    torrents_scanned: int
    dry_run: bool
    details: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# PikPak
# ---------------------------------------------------------------------------


class PikPakQueueItem(BaseModel):
    id: int
    torrent_hash: Optional[str]
    torrent_name: Optional[str]
    category: Optional[str]
    transfer_status: Optional[str]
    error_message: Optional[str]
    datetime_added_to_qb: Optional[str]


class PikPakQueueResponse(BaseModel):
    items: List[PikPakQueueItem]
    total: int


class PikPakTransferRequest(BaseModel):
    days: int = 7
    dry_run: bool = True


class PikPakTransferResponse(BaseModel):
    transferred: Optional[int]
    failed: Optional[int]
    skipped: Optional[int]
    dry_run: bool
    details: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Rclone (Task 3b — schemas only, no handler yet)
# ---------------------------------------------------------------------------


class RcloneLastResponse(BaseModel):
    inventory_count: int
    last_scan_time: Optional[str]
    dedup_pending: int
    dedup_completed: int
    total_freed_bytes: int = Field(
        description=(
            "Estimated bytes reclaimed by dedup, summed from the pre-deletion "
            "ExistingFolderSize of completed records. Approximate, not exact."
        ),
    )


class RcloneRunRequest(BaseModel):
    scan: bool = True
    report: bool = True
    execute: bool = False
    dry_run: bool = True


class RcloneRunResponse(BaseModel):
    phase_results: Dict[str, Any]
    dry_run: bool


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class EmailTestRequest(BaseModel):
    recipient: Optional[str] = None


class EmailHistoryItem(BaseModel):
    id: int
    session_id: Optional[str]
    recipient: Optional[str]
    subject: Optional[str]
    status: Optional[str]
    error_message: Optional[str]
    sent_at: Optional[str]
    resent_at: Optional[str]


class EmailHistoryResponse(BaseModel):
    items: List[EmailHistoryItem]
    next_cursor: Optional[str]


# ---------------------------------------------------------------------------
# Cleanup (Task 3b — schemas only, no handler yet)
# ---------------------------------------------------------------------------


class CleanupStaleRequest(BaseModel):
    older_than_hours: float = Field(default=48.0, gt=0)
    dry_run: bool = True
    scope: Literal["reports", "operations", "history", "all"] = "all"
    include_legacy: bool = False


class CleanupStaleResponse(BaseModel):
    sessions_found: int
    sessions_cleaned: int
    sessions_failed: int
    dry_run: bool
    details: List[Dict[str, Any]]


class CleanupClaimStagesRequest(BaseModel):
    shard_dates: Optional[List[str]] = None
    older_than_hours: float = Field(default=6.0, gt=0)


class CleanupClaimStagesResponse(BaseModel):
    shards_processed: int
    stages_reaped: int
    details: List[Dict[str, Any]]
