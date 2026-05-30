# tests/unit/test_pipeline_event_models.py
from javdb.pipeline.events.models import EVENT_TYPES, PipelineEventRecord, utc_now_iso


def test_event_taxonomy_complete():
    assert {"RunStarted", "SessionCommitted", "SessionFailed",
            "MovieDiscovered", "MovieSelected",
            "TorrentSelected", "TorrentQueued", "TorrentCompleted"} == set(EVENT_TYPES)


def test_record_minimal():
    r = PipelineEventRecord(event_type="RunStarted", session_id="S1", entity_type="session")
    assert r.entity_id is None
    assert r.seq is None


def test_utc_now_iso_trailing_z():
    assert utc_now_iso().endswith("Z")
