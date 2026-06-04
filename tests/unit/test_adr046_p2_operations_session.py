"""ADR-046 Phase 2: OperationsRepo resolves session explicitly (arg > bound >
None) for its nullable-session writes; replace_rclone_inventory no longer reads
the process-global.

These writes target tables whose ``SessionId`` column is NULLABLE
(DedupRecords, PikpakHistory, InventoryAlignNoExactMatch). A session-less
write is a deliberate, valid state for standalone jobs (WeeklyDedup CLI,
ad-hoc PikPak), so the resolver must NOT raise when no session is bound —
it returns ``None`` and the row is persisted untagged (ADR-046 P2 review fix).
"""
import inspect
import pytest
from javdb.storage.repos.operations_repo import OperationsRepo

_SID = "20260603T000000.000000Z-aaaa-0000"


def test_resolve_session_order():
    """explicit arg > bound session > None (never raises)."""
    assert OperationsRepo(session_id=_SID)._resolve_session() == _SID
    assert OperationsRepo(session_id=_SID)._resolve_session("OTHER") == "OTHER"
    # No bound session and no explicit arg → None (session-less, not a raise).
    assert OperationsRepo()._resolve_session() is None
    # Explicit arg still wins even when nothing is bound.
    assert OperationsRepo()._resolve_session("X") == "X"


def test_operations_repo_has_no_require_session():
    """The over-reaching raising guard was removed from OperationsRepo; only
    HistoryRepo (whose tables are NOT NULL) keeps a ``_require_session``."""
    assert not hasattr(OperationsRepo, "_require_session")


def test_replace_rclone_inventory_no_longer_reads_global():
    src = inspect.getsource(OperationsRepo.replace_rclone_inventory)
    assert "get_active_session_id" not in src


# ── Session-tagging writes resolve to None (no raise) when session-less ───
#
# Each write below targets a NULLABLE SessionId column; with no bound session
# and no explicit arg the resolver yields None and the underlying db_* function
# is invoked with ``session_id=None``. These assert the resolution contract via
# a mock so they stay fast and DB-independent; the real-DB persistence (row
# lands with SessionId NULL) is covered in
# ``test_adr046_p2_session_less_writes.py``.


def test_append_dedup_record_session_less_resolves_none():
    from unittest.mock import patch

    with patch(
        "javdb.storage.db._db_operations.db_append_dedup_record", return_value=1,
    ) as mock_fn:
        OperationsRepo().append_dedup_record({"VideoCode": "X"})
        assert mock_fn.call_args.kwargs["session_id"] is None


def test_append_pikpak_history_session_less_resolves_none():
    from unittest.mock import patch

    with patch(
        "javdb.storage.db._db_operations.db_append_pikpak_history", return_value=1,
    ) as mock_fn:
        OperationsRepo().append_pikpak_history({"magnet": "..."})
        assert mock_fn.call_args.kwargs["session_id"] is None


def test_mark_records_deleted_session_less_resolves_none():
    from unittest.mock import patch

    with patch(
        "javdb.storage.db._db_operations.db_mark_records_deleted", return_value=0,
    ) as mock_fn:
        OperationsRepo().mark_records_deleted([("/p/a", "2026-01-01")])
        assert mock_fn.call_args.kwargs["session_id"] is None


def test_mark_orphan_records_session_less_resolves_none():
    from unittest.mock import patch

    with patch(
        "javdb.storage.db._db_operations.db_mark_orphan_records", return_value=0,
    ) as mock_fn:
        OperationsRepo().mark_orphan_records(["/p/x"], "stale", "2026-01-01")
        assert mock_fn.call_args.kwargs["session_id"] is None


def test_upsert_align_no_exact_match_session_less_resolves_none():
    from unittest.mock import patch

    with patch(
        "javdb.storage.db._db_operations.db_upsert_align_no_exact_match",
        return_value=None,
    ) as mock_fn:
        OperationsRepo().upsert_align_no_exact_match("ABC-123")
        assert mock_fn.call_args.kwargs["session_id"] is None


# ── Session-tagging writes use the bound / explicit session when present ───


def test_session_tagging_writes_use_bound_session():
    """A bound session_id flows into the session-tagging writes when the
    per-call arg is omitted (explicit > bound > None)."""
    from unittest.mock import patch

    repo = OperationsRepo(session_id=_SID)
    with patch(
        "javdb.storage.db._db_operations.db_append_dedup_record", return_value=1,
    ) as mock_fn:
        repo.append_dedup_record({"VideoCode": "X"})
        mock_fn.assert_called_once_with(
            {"VideoCode": "X"}, session_id=_SID, db_path=None,
        )


def test_explicit_session_arg_wins_over_bound():
    """An explicit per-call session_id overrides the constructor-bound one."""
    from unittest.mock import patch

    repo = OperationsRepo(session_id=_SID)
    with patch(
        "javdb.storage.db._db_operations.db_append_pikpak_history", return_value=1,
    ) as mock_fn:
        repo.append_pikpak_history({"magnet": "..."}, session_id="OVERRIDE")
        assert mock_fn.call_args.kwargs["session_id"] == "OVERRIDE"
