"""Typed contracts for ADR-033 media closed-loop reconciliation (Phases 1-3:
acquisition outcome, ownership ledger, consumption signal)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional, Sequence

AcquisitionState = Literal[
    "queued",
    "downloading",
    "completed",
    "in_library",
    "stalled",
    "failed",
]

ACQUISITION_STATES: tuple[str, ...] = (
    "queued",
    "downloading",
    "completed",
    "in_library",
    "stalled",
    "failed",
)

# Terminal in Phase 1 (in_library is Phase-2-gated; see ADR-033 D6).
TERMINAL_STATES: frozenset[str] = frozenset({"completed", "in_library", "failed"})


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp with a trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class AcquisitionOutcomeRecord:
    qb_hash: str
    href: str = ""
    video_code: Optional[str] = None
    category: Optional[str] = None
    state: AcquisitionState = "queued"
    queued_at: Optional[str] = None
    completed_at: Optional[str] = None
    landed_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class Observation:
    """Normalized, read-only signal from one source about one torrent."""

    source: str
    qb_hash: str
    state: AcquisitionState
    observed_at: str


@dataclass
class ReconcileOptions:
    sources: Sequence[str] = ("qb",)
    categories: Sequence[str] = ("JavDB", "Ad Hoc")
    stalled_after_days: int = 7
    dry_run: bool = False
    infer_absent: bool = True


@dataclass
class ReconcileResult:
    observed: int = 0
    outcomes_updated: int = 0
    marked_downloading: int = 0
    marked_completed: int = 0
    marked_stalled: int = 0
    marked_failed: int = 0
    errors: list[str] = field(default_factory=list)


# --- ADR-033 Phase 2: Ownership truth ---------------------------------------

OWNERSHIP_SOURCES: tuple[str, ...] = ("qb", "nas", "gdrive", "pikpak")
# Sources that mean "the file is durably owned" (used by in_library + dedup skip).
PERSISTENT_OWNERSHIP_SOURCES: frozenset[str] = frozenset({"gdrive", "nas"})


@dataclass
class OwnershipLedgerRecord:
    """One multi-source ownership row: do I own video_code from this source?"""

    video_code: str
    source: str
    category: str = ""            # source-native, NOT NULL (D-P2-1)
    path: Optional[str] = None
    size: Optional[int] = None
    present: int = 1
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class OwnershipObservation:
    """Normalized, read-only ownership signal from one source (one snapshot row)."""

    source: str
    video_code: str
    category: str = ""
    path: Optional[str] = None
    size: Optional[int] = None


@dataclass
class OwnershipOptions:
    sources: Sequence[str] = OWNERSHIP_SOURCES
    derive_in_library: bool = True
    dry_run: bool = False


@dataclass
class OwnershipResult:
    observed: int = 0
    upserted: int = 0
    swept_absent: int = 0
    marked_in_library: int = 0
    errors: list[str] = field(default_factory=list)


# ── ADR-033 Phase 3: consumption signal ────────────────────────────────────

RESOLVED_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")


@dataclass(frozen=True)
class MediaItem:
    """Raw, read-only item returned by a MediaServerAdapter (never persisted as-is).

    The adapter fills the descriptive + signal fields; the service resolves a
    video_code from file_path/folder_name/title and decides confidence."""

    instance: str
    source_type: str            # 'emby' | 'plex'
    library_id: str
    library_name: Optional[str] = None
    item_id: str = ""
    file_path: Optional[str] = None
    folder_name: Optional[str] = None
    title: Optional[str] = None
    watched: Optional[bool] = None
    progress_pct: Optional[int] = None
    play_count: Optional[int] = None
    rating: Optional[float] = None
    watched_at: Optional[str] = None


@dataclass
class ConsumptionSignalRecord:
    video_code: str
    source_type: str
    instance: str
    library_id: str
    library_name: Optional[str] = None
    watched: Optional[bool] = None
    progress_pct: Optional[int] = None
    play_count: Optional[int] = None
    rating: Optional[float] = None
    watched_at: Optional[str] = None
    resolved_confidence: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass
class UnresolvedMediaItemRecord:
    instance: str
    library_id: str
    item_id: str
    source_type: Optional[str] = None
    library_name: Optional[str] = None
    raw_title: Optional[str] = None
    file_path: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass
class ConsumptionOptions:
    # Sequence[MediaServerConfig]; typed loosely to avoid a models→media_config
    # import cycle (media_config imports nothing from models).
    servers: Sequence[object] = ()
    dry_run: bool = False
    since: Optional[str] = None


@dataclass
class ConsumptionResult:
    instances_observed: int = 0
    items_observed: int = 0
    signals_updated: int = 0
    resolved_high: int = 0
    resolved_medium: int = 0
    resolved_low: int = 0
    marked_unresolved: int = 0
    errors: list[str] = field(default_factory=list)
