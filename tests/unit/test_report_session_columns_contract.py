"""ADR-055: the ReportSessions projection is sourced from the registry, byte-stable."""
from javdb.storage.repos import sessions_repo
from javdb.storage.repos.sessions_repo import _build_session_query

_EXPECTED = (
    "Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
    "ReportType, ReportDate, FailureReason"
)


def test_session_columns_join_matches_canonical():
    assert sessions_repo._SESSION_COLUMNS == _EXPECTED


def test_build_session_query_projection_unchanged():
    sql, _ = _build_session_query(state=None, cursor=None, limit=50)
    assert sql == f"SELECT {_EXPECTED} FROM ReportSessions ORDER BY Id DESC LIMIT ?"
