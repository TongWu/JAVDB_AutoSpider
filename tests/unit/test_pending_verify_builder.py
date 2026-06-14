from __future__ import annotations

from datetime import datetime

from javdb.storage.sessions.pending_verify import (
    F_CLEANUP_PATH_MISMATCH_COUNT,
    F_COMMIT_ATTEMPTS,
    F_COMMIT_DURATION_MS,
    F_DERIVED_DRIFT_SAMPLES,
    F_DERIVED_DRIFT_ERROR,
    F_DERIVED_RECOMPUTE_DRIFT,
    F_ERROR,
    F_FINAL_STATUS,
    F_HREFS_PROCESSED,
    F_KIND,
    F_MOVIES_UPSERTED,
    F_PENDING_APPLIED_COUNT,
    F_PENDING_RESIDUAL_COUNT,
    F_PENDING_STAGED_COUNT,
    F_ROLLBACK_MODE,
    F_SESSION_ID,
    F_SHADOW_AUDIT_ENABLED,
    F_SOURCE,
    F_STATS_READ_ERROR,
    F_TORRENTS_DELETED,
    F_TORRENTS_UPSERTED,
    F_WORKER_STAGE_ROLLBACK_FAILED,
    F_WRITE_MODE,
    build_pending_verify_record,
)


def test_build_commit_session_record_merges_shared_core_and_shadow_result(
):
    record = build_pending_verify_record(
        "S1",
        source="commit_session",
        write_mode="pending",
        final_status="committed",
        drain={
            "pending_marked_applied": "4",
            "hrefs_processed": "7",
            "torrents_upserted": 8,
            "torrents_deleted": None,
            "movies_upserted": 2,
        },
        stats={"pending_residual_count": "1"},
        commit_attempts="2",
        commit_duration_ms=123,
        shadow_audit_enabled=True,
        shadow_audit_result={
            "derived_recompute_drift": 1,
            "derived_drift_samples": ["/v/A"],
            "derived_drift_error": "shadow_audit_failed: boom",
        },
    )

    assert record[F_KIND] == "pending_session_verify"
    assert "ts" in record
    assert record[F_SOURCE] == "commit_session"
    assert record[F_SESSION_ID] == "S1"
    assert record[F_WRITE_MODE] == "pending"
    assert record[F_FINAL_STATUS] == "committed"
    assert record[F_PENDING_STAGED_COUNT] == 5
    assert record[F_PENDING_APPLIED_COUNT] == 4
    assert record[F_PENDING_RESIDUAL_COUNT] == 1
    assert record[F_COMMIT_ATTEMPTS] == 2
    assert record[F_COMMIT_DURATION_MS] == 123
    assert record[F_HREFS_PROCESSED] == 7
    assert record[F_TORRENTS_UPSERTED] == 8
    assert record[F_TORRENTS_DELETED] == 0
    assert record[F_MOVIES_UPSERTED] == 2
    assert record[F_WORKER_STAGE_ROLLBACK_FAILED] == 0
    assert record[F_STATS_READ_ERROR] is False
    assert record[F_SHADOW_AUDIT_ENABLED] is True
    assert record[F_DERIVED_RECOMPUTE_DRIFT] == 1
    assert record[F_DERIVED_DRIFT_SAMPLES] == ["/v/A"]
    assert record[F_DERIVED_DRIFT_ERROR] == "shadow_audit_failed: boom"
    ts = datetime.fromisoformat(record["ts"])
    assert ts.tzinfo is not None


def test_build_commit_session_lib_record_uses_default_derived_fields(
):
    record = build_pending_verify_record(
        "S2",
        source="commit_session_lib",
        write_mode="pending",
        final_status="committed",
        drain={"pending_marked_applied": 2, "torrents_deleted": 5},
        stats={"pending_residual_count": 0},
        commit_attempts=1,
        commit_duration_ms=None,
        shadow_audit_enabled=False,
        shadow_audit_result=None,
    )

    assert record[F_SOURCE] == "commit_session_lib"
    assert record[F_PENDING_STAGED_COUNT] == 2
    assert record[F_PENDING_APPLIED_COUNT] == 2
    assert record[F_PENDING_RESIDUAL_COUNT] == 0
    assert record[F_COMMIT_ATTEMPTS] == 1
    assert record[F_COMMIT_DURATION_MS] is None
    assert record[F_TORRENTS_DELETED] == 5
    assert record[F_SHADOW_AUDIT_ENABLED] is False
    assert record[F_DERIVED_RECOMPUTE_DRIFT] == 0
    assert record[F_DERIVED_DRIFT_SAMPLES] == []


def test_build_record_treats_empty_shadow_audit_result_as_default():
    record = build_pending_verify_record(
        "S2-empty",
        source="commit_session",
        write_mode="pending",
        final_status="committed",
        shadow_audit_enabled=True,
        shadow_audit_result={},
    )

    assert record[F_SHADOW_AUDIT_ENABLED] is True
    assert record[F_DERIVED_RECOMPUTE_DRIFT] == 0
    assert record[F_DERIVED_DRIFT_SAMPLES] == []


def test_build_record_flags_unavailable_pending_stats():
    record = build_pending_verify_record(
        "S2-stats-error",
        source="commit_session",
        write_mode="pending",
        final_status="committed",
        stats={},
        stats_read_error=True,
    )

    assert record[F_PENDING_RESIDUAL_COUNT] == 0
    assert record[F_STATS_READ_ERROR] is True


def test_build_rollback_record_merges_counts_extras_and_error(
):
    record = build_pending_verify_record(
        "S3",
        source="rollback",
        write_mode="pending",
        final_status="failed",
        drain={
            "mode": "rollback_pending",
            "pending_marked_applied": 1,
            "PendingMovieHistoryWrites": 2,
            "PendingTorrentHistoryWrites": 3,
            "hrefs_processed": 4,
            "torrents_upserted": 5,
            "torrents_deleted": 6,
            "movies_upserted": 7,
        },
        stats={"pending_residual_count": 8},
        commit_attempts=0,
        commit_duration_ms=55,
        worker_stage_rollback_failed=9,
        rollback_extras={
            F_ROLLBACK_MODE: "rollback_pending",
            F_CLEANUP_PATH_MISMATCH_COUNT: 1,
            "error": "rollback boom",
        },
    )

    assert record[F_SOURCE] == "rollback"
    assert record[F_FINAL_STATUS] == "failed"
    assert record[F_ROLLBACK_MODE] == "rollback_pending"
    assert record[F_PENDING_STAGED_COUNT] == 14
    assert record[F_PENDING_APPLIED_COUNT] == 1
    assert record[F_PENDING_RESIDUAL_COUNT] == 8
    assert record[F_COMMIT_ATTEMPTS] == 0
    assert record[F_COMMIT_DURATION_MS] == 55
    assert record[F_HREFS_PROCESSED] == 4
    assert record[F_TORRENTS_UPSERTED] == 5
    assert record[F_TORRENTS_DELETED] == 6
    assert record[F_MOVIES_UPSERTED] == 7
    assert record[F_WORKER_STAGE_ROLLBACK_FAILED] == 9
    assert record[F_CLEANUP_PATH_MISMATCH_COUNT] == 1
    assert record[F_SHADOW_AUDIT_ENABLED] is False
    assert record[F_DERIVED_RECOMPUTE_DRIFT] == 0
    assert record[F_DERIVED_DRIFT_SAMPLES] == []
    assert record[F_ERROR] == "rollback boom"


def test_build_record_does_not_attach_run_identity(monkeypatch):
    from javdb.storage.sessions import lifecycle_helpers

    def fail_attach(_record: dict, _session_id: str) -> None:
        raise AssertionError("builder must not attach run identity")

    monkeypatch.setattr(lifecycle_helpers, "attach_run_identity", fail_attach)

    record = build_pending_verify_record(
        "S4",
        source="commit_session",
        write_mode="pending",
        final_status="committed",
        stats={},
    )

    assert "run_id" not in record
    assert "run_attempt" not in record
