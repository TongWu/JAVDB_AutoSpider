from unittest.mock import patch

from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo


def test_create_report_session_delegates_with_db_path():
    repo = SessionLifecycleRepo(db_path="/tmp/reports.db")

    with patch("javdb.storage.db.db_create_report_session", return_value="sess-1") as mock_fn:
        result = repo.create_report_session(
            report_type="rclone_inventory",
            report_date="20260523",
            csv_filename="rclone_inventory.csv",
        )

    assert result == "sess-1"
    mock_fn.assert_called_once_with(
        report_type="rclone_inventory",
        report_date="20260523",
        csv_filename="rclone_inventory.csv",
        db_path="/tmp/reports.db",
    )


def test_mark_session_committed_routes_through_transition():
    # ADR-019: status writes go through the SessionLifecycle authority, not the
    # loose db_* primitive directly. The repo is a thin facade over transition().
    repo = SessionLifecycleRepo()

    with patch("javdb.storage.sessions.lifecycle.transition", return_value=1) as mock_fn:
        assert repo.mark_session_committed("sess-1") == 1

    mock_fn.assert_called_once_with("sess-1", "committed", db_path=None)


def test_mark_session_failed_routes_through_transition():
    repo = SessionLifecycleRepo(db_path="/tmp/reports.db")

    with patch("javdb.storage.sessions.lifecycle.transition", return_value=1) as mock_fn:
        assert repo.mark_session_failed("sess-1", reason="scan_failed") == 1

    mock_fn.assert_called_once_with(
        "sess-1",
        "failed",
        db_path="/tmp/reports.db",
        reason="scan_failed",
    )


def test_active_session_id_delegates():
    repo = SessionLifecycleRepo()

    with patch("javdb.storage.db.get_active_session_id", return_value="sess-1") as mock_fn:
        assert repo.get_active_session_id() == "sess-1"

    mock_fn.assert_called_once_with()


def test_init_storage_delegates():
    repo = SessionLifecycleRepo(db_path="/tmp/reports.db")

    with patch("javdb.storage.db.init_db") as mock_fn:
        repo.init_storage()

    mock_fn.assert_called_once_with(db_path="/tmp/reports.db")
