"""Unit tests for HistoryRepo (ADR-005 PR-1)."""

from unittest.mock import MagicMock, patch

from javdb.storage.repos.history_repo import HistoryRepo


class TestHistoryRepoConstruction:

    def test_default_db_path_is_none(self):
        repo = HistoryRepo()
        assert repo._db_path is None

    def test_custom_db_path(self):
        repo = HistoryRepo(db_path="/tmp/h.db")
        assert repo._db_path == "/tmp/h.db"


class TestHistoryRepoReads:

    @patch(
        "javdb.storage.db.db_history_read.db_load_history",
        return_value={"/movies/abc": {"VideoCode": "ABC-123"}},
    )
    def test_load_history_delegates(self, mock_fn):
        repo = HistoryRepo(db_path="/tmp/h.db")
        result = repo.load_history(phase=1)
        assert "/movies/abc" in result
        mock_fn.assert_called_once_with(db_path="/tmp/h.db", phase=1)

    @patch(
        "javdb.storage.db.db_history_read.db_load_history",
        return_value={},
    )
    def test_load_history_default_phase_none(self, mock_fn):
        repo = HistoryRepo()
        repo.load_history()
        mock_fn.assert_called_once_with(db_path=None, phase=None)

    @patch(
        "javdb.storage.db.db_history_read.db_load_history_snapshot",
        return_value={"snap": True},
    )
    def test_load_history_snapshot_delegates(self, mock_fn):
        repo = HistoryRepo(db_path="/tmp/h.db")
        result = repo.load_history_snapshot("sess-1")
        assert result == {"snap": True}
        mock_fn.assert_called_once_with(
            session_id="sess-1", db_path="/tmp/h.db",
        )

    @patch(
        "javdb.storage.db.db_history_read.db_check_torrent_in_history",
        return_value=True,
    )
    def test_check_torrent_in_history_delegates(self, mock_fn):
        repo = HistoryRepo()
        assert repo.check_torrent_in_history("/movies/x", "subtitle") is True
        mock_fn.assert_called_once_with(
            href="/movies/x", torrent_type="subtitle", db_path=None,
        )

    @patch(
        "javdb.storage.db.db_history_read.db_get_all_history_records",
        return_value=[{"Id": 1}],
    )
    def test_get_all_history_records_delegates(self, mock_fn):
        repo = HistoryRepo()
        assert repo.get_all_history_records() == [{"Id": 1}]
        mock_fn.assert_called_once_with(db_path=None)


class TestHistoryRepoWrites:

    @patch(
        "javdb.storage.db.db_history_write.db_stage_history_write",
        return_value="SEQ-000",
    )
    def test_stage_history_write_delegates(self, mock_fn):
        repo = HistoryRepo(db_path="/tmp/h.db")
        payload = {"Href": "/movies/abc"}
        result = repo.stage_history_write("sess-0", "movie", payload)
        assert result == "SEQ-000"
        mock_fn.assert_called_once_with(
            session_id="sess-0", kind="movie", payload=payload,
            db_path="/tmp/h.db",
        )

    @patch(
        "javdb.storage.db.db_history_write.db_stage_history_write",
        return_value="SEQ-001",
    )
    def test_stage_movie_delegates(self, mock_fn):
        repo = HistoryRepo(db_path="/tmp/h.db")
        payload = {"Href": "/movies/abc"}
        result = repo.stage_movie("sess-1", payload)
        assert result == "SEQ-001"
        mock_fn.assert_called_once_with(
            session_id="sess-1", kind="movie", payload=payload,
            db_path="/tmp/h.db",
        )

    @patch(
        "javdb.storage.db.db_history_write.db_stage_history_write",
        return_value="SEQ-002",
    )
    def test_stage_torrent_delegates(self, mock_fn):
        repo = HistoryRepo()
        payload = {"MagnetUri": "magnet:?xt=..."}
        result = repo.stage_torrent("sess-2", payload)
        assert result == "SEQ-002"
        mock_fn.assert_called_once_with(
            session_id="sess-2", kind="torrent", payload=payload,
            db_path=None,
        )

    @patch(
        "javdb.storage.db.db_history_write.db_commit_session_history",
        return_value={"movies": 3, "torrents": 5},
    )
    def test_commit_session_delegates(self, mock_fn):
        repo = HistoryRepo()
        result = repo.commit_session("sess-3", dry_run=True)
        assert result == {"movies": 3, "torrents": 5}
        mock_fn.assert_called_once_with("sess-3", dry_run=True)

    @patch(
        "javdb.storage.db.db_history_read.db_batch_update_last_visited",
        return_value=2,
    )
    def test_batch_update_last_visited_delegates(self, mock_fn):
        repo = HistoryRepo(db_path="/tmp/h.db")
        result = repo.batch_update_last_visited(["/a", "/b"])
        assert result == 2
        mock_fn.assert_called_once_with(["/a", "/b"], db_path="/tmp/h.db")

    @patch(
        "javdb.storage.db.db.db_batch_update_movie_actors",
        return_value=1,
    )
    def test_batch_update_movie_actors_delegates(self, mock_fn):
        repo = HistoryRepo()
        updates = [("/m/1", "Actor", "M", "/actors/1", "[]")]
        result = repo.batch_update_movie_actors(updates)
        assert result == 1
        mock_fn.assert_called_once_with(updates, db_path=None)
