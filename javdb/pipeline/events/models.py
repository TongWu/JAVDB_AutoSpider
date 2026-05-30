"""Event-spine contracts (ADR-036). Taxonomy is full; Phase 1 wires a subset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

EVENT_TYPES: tuple[str, ...] = (
    "RunStarted", "SessionCommitted", "SessionFailed",   # session (wired in Phase 1)
    "MovieDiscovered", "MovieSelected",                   # movie  (Phase 2)
    "TorrentSelected", "TorrentQueued", "TorrentCompleted",  # torrent (Phase 2)
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PipelineEventRecord:
    event_type: str
    session_id: str
    entity_type: str            # session | movie | torrent
    entity_id: Optional[str] = None
    payload: Optional[str] = None   # JSON string
    run_id: Optional[str] = None
    run_attempt: Optional[int] = None
    seq: Optional[int] = None       # assigned by the DB on append
    created_at: Optional[str] = None
