"""Cursor-based event consumers (ADR-036). Replay = reset cursor, re-run."""

from __future__ import annotations

import json
import logging

from javdb.pipeline.events.models import PipelineEventRecord
from javdb.storage.repos.pipeline_event_repo import (
    AcquisitionOutcomeShadowRepo,
    RunEventSummaryRepo,
)

logger = logging.getLogger(__name__)


class Consumer:
    name = "base"

    def handle(self, event: PipelineEventRecord) -> None:
        raise NotImplementedError

    def run_once(self, *, event_repo, batch: int = 500) -> int:
        last = event_repo.get_cursor(self.name)
        events = event_repo.read_since(last, limit=batch)
        for event in events:
            self.handle(event)
        if events:
            event_repo.advance_cursor(self.name, events[-1].seq)
        return len(events)


class RunEventSummaryConsumer(Consumer):
    name = "run_event_summary"

    def __init__(self, summary_repo: RunEventSummaryRepo) -> None:
        self._summary = summary_repo

    def handle(self, event: PipelineEventRecord) -> None:
        self._summary.bump(event.session_id, event.event_type)


class AcquisitionOutcomeShadowConsumer(Consumer):
    """Projects TorrentQueued/TorrentCompleted events into AcquisitionOutcomeShadow.

    Cross-validation use only — never read by production decisions.
    """

    name = "acquisition_outcome_shadow"

    def __init__(self, shadow_repo: AcquisitionOutcomeShadowRepo) -> None:
        self._shadow = shadow_repo

    def handle(self, event: PipelineEventRecord) -> None:
        if event.event_type == "TorrentQueued":
            # Skip events with no entity_id — shadow cannot key on NULL
            if not event.entity_id:
                return
            try:
                payload = json.loads(event.payload or "{}")
            except Exception:
                logger.debug(
                    "AcquisitionOutcomeShadowConsumer: bad payload seq=%s", event.seq
                )
                return
            self._shadow.upsert_queued(
                qb_hash=event.entity_id,
                href=payload.get("href") or "",
                video_code=payload.get("video_code"),
                category=payload.get("category"),
                queued_at=event.created_at,
                session_id=event.session_id,
            )
        elif event.event_type == "TorrentCompleted":
            if not event.entity_id:
                return
            try:
                payload = json.loads(event.payload or "{}")
            except Exception:
                logger.debug(
                    "AcquisitionOutcomeShadowConsumer: bad payload seq=%s", event.seq
                )
                return
            completed_at = payload.get("completed_at") or event.created_at
            self._shadow.mark_completed(
                qb_hash=event.entity_id,
                completed_at=completed_at,
            )
        # All other event types: no-op
