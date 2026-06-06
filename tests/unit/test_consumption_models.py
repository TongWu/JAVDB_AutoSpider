import dataclasses

import pytest

from javdb.ops.reconcile.models import (
    ConsumptionResult,
    ConsumptionSignalRecord,
    MediaItem,
    RESOLVED_CONFIDENCES,
    UnresolvedMediaItemRecord,
)


def test_resolved_confidences_are_high_medium_low():
    assert RESOLVED_CONFIDENCES == ("high", "medium", "low")


def test_media_item_is_frozen():
    item = MediaItem(
        instance="plex-home", source_type="plex", library_id="3",
        library_name="JAV", item_id="998", file_path="/m/ABC-123.mp4",
        folder_name="ABC-123", title="ABC-123 Title",
        watched=True, progress_pct=100, play_count=2, rating=8.0,
        watched_at="2026-06-06T00:00:00Z",
    )
    assert item.instance == "plex-home"
    assert item.watched is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.instance = "emby-nas"  # type: ignore[misc]


def test_consumption_signal_record_defaults():
    rec = ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3",
    )
    assert rec.library_name is None
    assert rec.watched is None
    assert rec.resolved_confidence is None


def test_unresolved_record_minimal():
    rec = UnresolvedMediaItemRecord(
        instance="emby-nas", library_id="7", item_id="42",
    )
    assert rec.source_type is None
    assert rec.raw_title is None


def test_consumption_result_starts_empty():
    res = ConsumptionResult()
    assert res.items_observed == 0
    assert res.marked_unresolved == 0
    assert res.errors == []
