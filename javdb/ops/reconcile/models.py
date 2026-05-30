"""Typed contracts for ADR-033 Phase 1 acquisition-outcome reconciliation."""

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
