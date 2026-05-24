"""Unit tests for OperationsRepo (ADR-005 PR-1)."""

from unittest.mock import MagicMock, patch

from javdb.storage.repos.operations_repo import OperationsRepo


class TestOperationsRepoConstruction:

    def test_default_db_path_is_none(self):
        repo = OperationsRepo()
        assert repo._db_path is None

    def test_custom_db_path(self):
        repo = OperationsRepo(db_path="/tmp/ops.db")
        assert repo._db_path == "/tmp/ops.db"


class TestOperationsRepoRcloneInventory:

    @patch(
        "javdb.storage.db.db_load_rclone_inventory",
        return_value={"ABC-123": [{"VideoCode": "ABC-123"}]},
    )
    def test_load_rclone_inventory_delegates(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        result = repo.load_rclone_inventory()
        assert result == {"ABC-123": [{"VideoCode": "ABC-123"}]}
        mock_fn.assert_called_once_with(db_path="/tmp/o.db")

    @patch(
        "javdb.storage.db.db_replace_rclone_inventory",
        return_value=5,
    )
    def test_replace_rclone_inventory_delegates(self, mock_fn):
        repo = OperationsRepo()
        entries = [{"VideoCode": "X"}]
        assert repo.replace_rclone_inventory(entries) == 5
        mock_fn.assert_called_once_with(entries=entries, db_path=None)

    @patch(
        "javdb.storage.db.db_swap_rclone_inventory",
        return_value=10,
    )
    def test_swap_rclone_inventory_delegates(self, mock_fn):
        repo = OperationsRepo()
        assert repo.swap_rclone_inventory("sess-1") == 10
        mock_fn.assert_called_once_with(session_id="sess-1", db_path=None)

    @patch("javdb.storage.db.db_clear_rclone_inventory")
    def test_clear_rclone_inventory_delegates(self, mock_fn):
        repo = OperationsRepo()
        repo.clear_rclone_inventory()
        mock_fn.assert_called_once_with(db_path=None)

    @patch(
        "javdb.storage.db.db_append_rclone_inventory",
        return_value=3,
    )
    def test_append_rclone_inventory_delegates(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        entries = [{"VideoCode": "Y"}]
        result = repo.append_rclone_inventory(entries, session_id="s1")
        assert result == 3
        mock_fn.assert_called_once_with(
            entries=entries, session_id="s1", db_path="/tmp/o.db",
        )


class TestOperationsRepoDedup:

    @patch(
        "javdb.storage.db.db_load_dedup_records",
        return_value=[{"Id": 1}],
    )
    def test_load_dedup_records_delegates(self, mock_fn):
        repo = OperationsRepo()
        assert repo.load_dedup_records() == [{"Id": 1}]

    @patch("javdb.storage.db.db_save_dedup_records")
    def test_save_dedup_records_delegates(self, mock_fn):
        repo = OperationsRepo()
        rows = [{"VideoCode": "A"}]
        repo.save_dedup_records(rows)
        mock_fn.assert_called_once_with(rows=rows, db_path=None)

    @patch(
        "javdb.storage.db.db_append_dedup_record",
        return_value=9,
    )
    def test_append_dedup_record_delegates(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        record = {"VideoCode": "B"}
        result = repo.append_dedup_record(record, session_id="s1")
        assert result == 9
        mock_fn.assert_called_once_with(
            record, session_id="s1", db_path="/tmp/o.db",
        )

    @patch("javdb.storage.db.db_append_dedup_record")
    def test_append_dedup_record_keeps_payload_alias(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        payload = {"VideoCode": "B"}
        repo.append_dedup_record(session_id="s1", payload=payload)
        mock_fn.assert_called_once_with(
            payload, session_id="s1", db_path="/tmp/o.db",
        )


class TestOperationsRepoDedupLifecycle:

    @patch(
        "javdb.storage.db.db_mark_records_deleted",
        return_value=2,
    )
    def test_mark_records_deleted_with_session(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        pairs = [("/path/a", "2026-01-01"), ("/path/b", "2026-01-02")]
        result = repo.mark_records_deleted(pairs, session_id="s1")
        assert result == 2
        mock_fn.assert_called_once_with(
            pairs, db_path="/tmp/o.db", session_id="s1",
        )

    @patch(
        "javdb.storage.db.db_mark_records_deleted",
        return_value=0,
    )
    def test_mark_records_deleted_without_session(self, mock_fn):
        repo = OperationsRepo()
        repo.mark_records_deleted([], session_id=None)
        mock_fn.assert_called_once_with([], db_path=None, session_id=None)

    @patch(
        "javdb.storage.db.db_cleanup_deleted_records",
        return_value=5,
    )
    def test_cleanup_deleted_records_delegates(self, mock_fn):
        repo = OperationsRepo()
        assert repo.cleanup_deleted_records(older_than_days=14) == 5
        mock_fn.assert_called_once_with(older_than_days=14, db_path=None)

    @patch(
        "javdb.storage.db.db_mark_orphan_records",
        return_value=1,
    )
    def test_mark_orphan_records_delegates(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        result = repo.mark_orphan_records(
            ["/p/x"], "stale", "2026-01-01", session_id="s2",
        )
        assert result == 1
        mock_fn.assert_called_once_with(
            ["/p/x"],
            reason_suffix="stale",
            when="2026-01-01",
            db_path="/tmp/o.db",
            session_id="s2",
        )


class TestOperationsRepoRcloneStaging:

    @patch(
        "javdb.storage.db.db_open_rclone_staging",
        return_value="RcloneInventoryStaging_sess1",
    )
    def test_open_rclone_staging_delegates(self, mock_fn):
        repo = OperationsRepo()
        result = repo.open_rclone_staging("sess1")
        assert result == "RcloneInventoryStaging_sess1"
        mock_fn.assert_called_once_with(session_id="sess1", db_path=None)

    @patch(
        "javdb.storage.db.db_append_rclone_staging",
        return_value=3,
    )
    def test_append_rclone_staging_delegates(self, mock_fn):
        repo = OperationsRepo()
        entries = [{"VideoCode": "X"}]
        assert repo.append_rclone_staging(entries, "sess1") == 3
        mock_fn.assert_called_once_with(
            entries, session_id="sess1", db_path=None,
        )

    @patch(
        "javdb.storage.db.db_merge_rclone_inventory_from_stage",
        return_value=50,
    )
    def test_merge_rclone_inventory_from_stage_delegates(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        result = repo.merge_rclone_inventory_from_stage("s1", ["2025", "2026"])
        assert result == 50
        mock_fn.assert_called_once_with(
            session_id="s1", years=["2025", "2026"], db_path="/tmp/o.db",
        )

    @patch("javdb.storage.db.db_drop_rclone_staging")
    def test_drop_rclone_staging_delegates(self, mock_fn):
        repo = OperationsRepo()
        repo.drop_rclone_staging("sess1")
        mock_fn.assert_called_once_with(session_id="sess1", db_path=None)

    @patch(
        "javdb.storage.db.db_delete_rclone_inventory_paths",
        return_value=2,
    )
    def test_delete_rclone_inventory_paths_delegates(self, mock_fn):
        repo = OperationsRepo()
        assert repo.delete_rclone_inventory_paths(["/a", "/b"]) == 2
        mock_fn.assert_called_once_with(["/a", "/b"], db_path=None)


class TestOperationsRepoAlignNoExactMatch:

    @patch("javdb.storage.db.db_upsert_align_no_exact_match")
    def test_upsert_delegates_with_session(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        repo.upsert_align_no_exact_match("ABC-123", session_id="s1")
        mock_fn.assert_called_once_with(
            "ABC-123",
            reason="exact_video_code_not_found",
            db_path="/tmp/o.db",
            session_id="s1",
        )

    @patch("javdb.storage.db.db_upsert_align_no_exact_match")
    def test_upsert_custom_reason(self, mock_fn):
        repo = OperationsRepo()
        repo.upsert_align_no_exact_match("X-001", reason="custom_reason")
        mock_fn.assert_called_once_with(
            "X-001",
            reason="custom_reason",
            db_path=None,
            session_id=None,
        )

    @patch(
        "javdb.storage.db.db_load_align_no_exact_match_codes",
        return_value={"ABC-123", "DEF-456"},
    )
    def test_load_align_no_exact_match_codes_delegates(self, mock_fn):
        repo = OperationsRepo()
        result = repo.load_align_no_exact_match_codes()
        assert result == {"ABC-123", "DEF-456"}
        mock_fn.assert_called_once_with(db_path=None)

    @patch("javdb.storage.db.db_delete_align_no_exact_match")
    def test_delete_align_no_exact_match_delegates(self, mock_fn):
        repo = OperationsRepo()
        repo.delete_align_no_exact_match("ABC-123")
        mock_fn.assert_called_once_with("ABC-123", db_path=None)


class TestOperationsRepoPikpak:

    @patch(
        "javdb.storage.db.db_append_pikpak_history",
        return_value=10,
    )
    def test_append_pikpak_history_delegates(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        record = {"magnet": "..."}
        result = repo.append_pikpak_history(record, session_id="s1")
        assert result == 10
        mock_fn.assert_called_once_with(
            record, session_id="s1", db_path="/tmp/o.db",
        )

    @patch("javdb.storage.db.db_append_pikpak_history")
    def test_append_pikpak_history_keeps_payload_alias(self, mock_fn):
        repo = OperationsRepo(db_path="/tmp/o.db")
        payload = {"magnet": "..."}
        repo.append_pikpak_history(session_id="s1", payload=payload)
        mock_fn.assert_called_once_with(
            payload, session_id="s1", db_path="/tmp/o.db",
        )
