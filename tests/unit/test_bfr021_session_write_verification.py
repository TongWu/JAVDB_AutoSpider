"""BFR-021: db_create_report_session must fail fast on a silent write loss.

Run 27810377978 created a session that the backend ACKed but never persisted
on D1. The pipeline then ran for 6 minutes against a non-existent session: every
downstream FK child orphaned with "FOREIGN KEY constraint failed" and
commit_session finally died with "None -> committed is not allowed".

The fix verifies the row is readable back (on the same connection, so it is
read-your-write consistent) before returning success.
"""

import contextlib

import pytest

import javdb.storage.db._db_reports as reports


def test_create_report_session_happy_path_returns_sid():
    """A normal create (real SQLite backend) still succeeds and lands the row."""
    sid = reports.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-06-19",
        csv_filename="ok.csv",
    )
    assert sid
    assert reports.db_get_session_status(sid) is not None


def test_create_report_session_fails_fast_when_write_does_not_land(monkeypatch):
    """If the INSERT is ACKed but the row is absent on read-back, raise."""
    # Populate the module's lazy globals before swapping _get_db: once _get_db is
    # monkeypatched non-None, db_create_report_session's _ensure_imports() guard
    # skips and would leave _resolve_write_mode/_generate_session_id as None.
    # _ensure_imports() does this with no DB write, keeping the test
    # order-independent and free of real DB-state coupling.
    reports._ensure_imports()

    class _LosingCursor:
        rowcount = 0

        def fetchone(self):
            return None  # simulate the row never persisting

    class _LosingConn:
        def execute(self, *args, **kwargs):
            return _LosingCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    @contextlib.contextmanager
    def _losing_get_db(_path=None):
        yield _LosingConn()

    monkeypatch.setattr(reports, "_get_db", _losing_get_db)

    with pytest.raises(RuntimeError, match="did not durably land"):
        reports.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-06-19",
            csv_filename="lost.csv",
        )


def test_verification_reads_d1_leg_not_sqlite_fallback_in_dual(monkeypatch):
    """In dual mode the check must consult the D1 leg, not the SQLite mirror.

    DualConnection writes the SQLite leg first and falls back to it on a D1
    read error, so a connection whose SQLite leg HAS the row but whose D1 leg
    does NOT must still fail fast — otherwise the mirror masks a lost D1 write.
    """
    # Populate lazy globals without a real DB write (see note in the test above);
    # _FakeDualConn below fully supplies the SQLite-has-row / D1-missing condition.
    reports._ensure_imports()

    class _PresentCursor:
        def fetchone(self):
            return (1,)  # SQLite mirror still holds the row

    class _AbsentCursor:
        def fetchone(self):
            return None  # D1 leg never got the row

    class _FakeD1:
        def execute(self, *args, **kwargs):
            return _AbsentCursor()

    class _FakeDualConn:
        _d1 = _FakeD1()  # verify_conn must resolve to this, not self

        def execute(self, *args, **kwargs):
            return _PresentCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    @contextlib.contextmanager
    def _dual_get_db(_path=None):
        yield _FakeDualConn()

    monkeypatch.setattr(reports, "_get_db", _dual_get_db)

    with pytest.raises(RuntimeError, match="did not durably land"):
        reports.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-06-19",
            csv_filename="dual-drift.csv",
        )
