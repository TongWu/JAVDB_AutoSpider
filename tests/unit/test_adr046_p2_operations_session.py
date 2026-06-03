"""ADR-046 Phase 2: OperationsRepo resolves session explicitly (arg > bound >
raise); replace_rclone_inventory no longer reads the process-global."""
import inspect
import pytest
from javdb.storage.repos.operations_repo import OperationsRepo

_SID = "20260603T000000.000000Z-aaaa-0000"


def test_require_session_order():
    assert OperationsRepo(session_id=_SID)._require_session() == _SID
    assert OperationsRepo(session_id=_SID)._require_session("OTHER") == "OTHER"
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo()._require_session()


def test_replace_rclone_inventory_no_longer_reads_global():
    src = inspect.getsource(OperationsRepo.replace_rclone_inventory)
    assert "get_active_session_id" not in src


def test_replace_rclone_inventory_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().replace_rclone_inventory([])


# ── Session-tagging writes raise when no session is resolvable ────────


def test_append_dedup_record_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().append_dedup_record({"VideoCode": "X"})


def test_append_pikpak_history_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().append_pikpak_history({"magnet": "..."})


def test_mark_records_deleted_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().mark_records_deleted([("/p/a", "2026-01-01")])


def test_mark_orphan_records_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().mark_orphan_records(["/p/x"], "stale", "2026-01-01")


def test_upsert_align_no_exact_match_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().upsert_align_no_exact_match("ABC-123")


# ── Session-tagging writes use the bound session when arg omitted ─────


def test_session_tagging_writes_use_bound_session():
    """A bound session_id flows into the session-tagging writes when the
    per-call arg is omitted (explicit > bound > raise)."""
    from unittest.mock import patch

    repo = OperationsRepo(session_id=_SID)
    with patch(
        "javdb.storage.db._db_operations.db_append_dedup_record", return_value=1,
    ) as mock_fn:
        repo.append_dedup_record({"VideoCode": "X"})
        mock_fn.assert_called_once_with(
            {"VideoCode": "X"}, session_id=_SID, db_path=None,
        )
