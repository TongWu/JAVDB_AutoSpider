"""Cursor-based event consumers (ADR-036). Replay = reset cursor, re-run."""

from __future__ import annotations

from javdb.pipeline.events.models import PipelineEventRecord
from javdb.storage.repos.pipeline_event_repo import RunEventSummaryRepo


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
