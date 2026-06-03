"""ADR-047 D1a: NULL WriteMode maps to pending, matching the TS backend."""

from javdb.storage.repos.sessions_repo import _row_to_session


def _row(**over):
    base = {
        "Id": "S1",
        "Status": None,
        "WriteMode": None,
        "RunId": None,
        "RunAttempt": None,
        "DateTimeCreated": "2026-06-02T00:00:00Z",
        "ReportType": None,
        "ReportDate": None,
        "FailureReason": None,
    }
    base.update(over)
    return base


def test_null_write_mode_defaults_to_pending():
    assert _row_to_session(_row(WriteMode=None)).write_mode == "pending"


def test_explicit_write_mode_preserved():
    assert _row_to_session(_row(WriteMode="pending")).write_mode == "pending"
