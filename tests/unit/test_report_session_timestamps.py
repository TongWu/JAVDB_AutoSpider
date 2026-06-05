"""ReportSessions timestamp regression tests."""

from datetime import datetime as real_datetime
from datetime import timezone as real_timezone

import javdb.storage.db._db_reports as db_reports
from javdb.storage.db import db_create_report_session, get_db


def test_create_report_session_defaults_created_at_to_utc(_isolate_sqlite, monkeypatch):
    class Clock:
        @classmethod
        def now(cls, tz: object = None) -> real_datetime:
            assert tz is real_timezone.utc
            return real_datetime(2026, 1, 2, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr(db_reports, "datetime", Clock)
    sid = db_create_report_session(
        report_type="daily",
        report_date="20240101",
        csv_filename="utc-created.csv",
        db_path=_isolate_sqlite,
    )

    with get_db(_isolate_sqlite) as conn:
        row = conn.execute(
            "SELECT DateTimeCreated FROM ReportSessions WHERE Id=?",
            (sid,),
        ).fetchone()

    assert row["DateTimeCreated"] == "2026-01-02 03:04:05"
