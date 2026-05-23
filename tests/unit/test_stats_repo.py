"""Unit tests for StatsRepo (ADR-005 PR-1)."""

from unittest.mock import MagicMock, patch

from javdb.storage.repos.stats_repo import StatsRepo


class TestStatsRepoConstruction:

    def test_default_db_path_is_none(self):
        repo = StatsRepo()
        assert repo._db_path is None

    def test_custom_db_path(self):
        repo = StatsRepo(db_path="/tmp/test.db")
        assert repo._db_path == "/tmp/test.db"


class TestStatsRepoSave:

    @patch("javdb.storage.db.db_save_spider_stats", return_value=42)
    def test_save_spider_stats_delegates(self, mock_fn):
        repo = StatsRepo(db_path="/tmp/r.db")
        stats = {"total_discovered": 10}
        result = repo.save_spider_stats("sess-1", stats)
        assert result == 42
        mock_fn.assert_called_once_with("sess-1", stats, db_path="/tmp/r.db")

    @patch("javdb.storage.db.db_save_uploader_stats", return_value=7)
    def test_save_uploader_stats_delegates(self, mock_fn):
        repo = StatsRepo()
        result = repo.save_uploader_stats("sess-2", {"attempted": 5})
        assert result == 7
        mock_fn.assert_called_once_with("sess-2", {"attempted": 5}, db_path=None)

    @patch("javdb.storage.db.db_save_pikpak_stats", return_value=3)
    def test_save_pikpak_stats_delegates(self, mock_fn):
        repo = StatsRepo()
        result = repo.save_pikpak_stats("sess-3", {"threshold_days": 7})
        assert result == 3
        mock_fn.assert_called_once_with(
            "sess-3", {"threshold_days": 7}, db_path=None,
        )


class TestStatsRepoRead:

    @patch(
        "javdb.storage.db.db_get_spider_stats",
        return_value={"SessionId": "s", "TotalProcessed": 15},
    )
    def test_get_spider_stats_delegates(self, mock_fn):
        repo = StatsRepo(db_path="/tmp/r.db")
        result = repo.get_spider_stats("s")
        assert result["TotalProcessed"] == 15
        mock_fn.assert_called_once_with("s", db_path="/tmp/r.db")

    @patch("javdb.storage.db.db_get_uploader_stats", return_value=None)
    def test_get_uploader_stats_returns_none(self, mock_fn):
        repo = StatsRepo()
        assert repo.get_uploader_stats("missing") is None

    @patch(
        "javdb.storage.db.db_get_pikpak_stats",
        return_value={"SessionId": "p"},
    )
    def test_get_pikpak_stats_delegates(self, mock_fn):
        repo = StatsRepo()
        assert repo.get_pikpak_stats("p") == {"SessionId": "p"}


class TestStatsRepoLocalRead:

    @patch(
        "javdb.storage.db.db_get_spider_stats_local",
        return_value={"TotalProcessed": 5},
    )
    def test_get_spider_stats_local_delegates(self, mock_fn):
        repo = StatsRepo(db_path="/tmp/r.db")
        result = repo.get_spider_stats_local("s")
        assert result == {"TotalProcessed": 5}
        mock_fn.assert_called_once_with("s", db_path="/tmp/r.db")

    @patch(
        "javdb.storage.db.db_get_uploader_stats_local",
        return_value=None,
    )
    def test_get_uploader_stats_local_delegates(self, mock_fn):
        repo = StatsRepo()
        assert repo.get_uploader_stats_local("x") is None
        mock_fn.assert_called_once_with("x", db_path=None)

    @patch(
        "javdb.storage.db.db_get_pikpak_stats_local",
        return_value={"SessionId": "loc"},
    )
    def test_get_pikpak_stats_local_delegates(self, mock_fn):
        repo = StatsRepo()
        assert repo.get_pikpak_stats_local("loc") == {"SessionId": "loc"}
