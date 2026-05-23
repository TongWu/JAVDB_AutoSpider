"""Unit tests for EmailNotificationHistory CRUD in OperationsRepo.

Verifies:
- append_email_history inserts rows correctly.
- list_email_history returns all rows (newest first) when unfiltered.
- list_email_history narrows results correctly with status filter.
- get_email_history_by_id returns the right row or None.
- mark_email_resent updates Status and ResentAt.
- Cursor pagination works correctly.
- The EmailNotificationHistory table exists in a freshly-initialised DB
  (confirming the _OPERATIONS_DDL addition landed).
"""

from __future__ import annotations

import base64
import json

import pytest

from javdb.storage.repos.operations_repo import OperationsRepo


# ── Helpers ────────────────────────────────────────────────────────────────


def _repo() -> OperationsRepo:
    """Return a repo that targets the test DB (db_path=None lets the fixture
    redirect OPERATIONS_DB_PATH to the temp test.db via conftest)."""
    return OperationsRepo()


# ── Tests ──────────────────────────────────────────────────────────────────


class TestEmailHistoryTableExists:
    """The table must be present in a freshly-initialised DB."""

    def test_table_exists(self, _isolate_sqlite):
        from javdb.storage.db import get_db, OPERATIONS_DB_PATH

        with get_db(OPERATIONS_DB_PATH) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='EmailNotificationHistory'"
            ).fetchone()
        assert row is not None, "EmailNotificationHistory table missing from freshly-initialised DB"


class TestAppendEmailHistory:
    """append_email_history inserts a row with the expected column values."""

    def test_append_sent(self, _isolate_sqlite):
        repo = _repo()
        repo.append_email_history(
            session_id="sess-001",
            recipient="user@example.com",
            subject="Daily Report",
            status="sent",
            attachments=["report.csv", "log.txt"],
        )
        rows, _ = repo.list_email_history()
        assert len(rows) == 1
        row = rows[0]
        assert row["SessionId"] == "sess-001"
        assert row["Recipient"] == "user@example.com"
        assert row["Subject"] == "Daily Report"
        assert row["Status"] == "sent"
        assert row["ErrorMessage"] is None
        assert json.loads(row["AttachmentNames"]) == ["report.csv", "log.txt"]
        assert row["SentAt"] is not None
        assert row["ResentAt"] is None
        assert row["CreatedBy"] == "pipeline"

    def test_append_failed(self, _isolate_sqlite):
        repo = _repo()
        repo.append_email_history(
            session_id=None,
            recipient="ops@example.com",
            subject="Error Report",
            status="failed",
            error="Connection refused",
        )
        rows, _ = repo.list_email_history()
        assert len(rows) == 1
        row = rows[0]
        assert row["Status"] == "failed"
        assert row["ErrorMessage"] == "Connection refused"
        assert row["SessionId"] is None
        assert row["AttachmentNames"] is None

    def test_append_no_attachments_stored_as_null(self, _isolate_sqlite):
        repo = _repo()
        repo.append_email_history(None, "a@b.com", "sub", "sent")
        rows, _ = repo.list_email_history()
        assert rows[0]["AttachmentNames"] is None

    def test_append_invalid_status_raises(self, _isolate_sqlite):
        repo = _repo()
        with pytest.raises(ValueError):
            repo.append_email_history(None, "a@b.com", "sub", "bogus")

    def test_append_invalid_created_by_raises(self, _isolate_sqlite):
        repo = _repo()
        with pytest.raises(ValueError):
            repo.append_email_history(
                None, "a@b.com", "sub", "sent", created_by="bogus"
            )


class TestListEmailHistory:
    """list_email_history returns rows newest-first and respects status filter."""

    def _insert_two(self, repo: OperationsRepo) -> None:
        """Insert one 'sent' and one 'failed' row."""
        repo.append_email_history(None, "a@b.com", "S1", "sent")
        repo.append_email_history(None, "a@b.com", "S2", "failed", error="boom")

    def test_returns_both_rows(self, _isolate_sqlite):
        repo = _repo()
        self._insert_two(repo)
        rows, next_cursor = repo.list_email_history()
        assert len(rows) == 2
        assert next_cursor is None

    def test_newest_first_ordering(self, _isolate_sqlite):
        repo = _repo()
        self._insert_two(repo)
        rows, _ = repo.list_email_history()
        # Second insert has higher Id → should appear first (descending order)
        assert rows[0]["Subject"] == "S2"
        assert rows[1]["Subject"] == "S1"

    def test_status_filter_sent(self, _isolate_sqlite):
        repo = _repo()
        self._insert_two(repo)
        rows, _ = repo.list_email_history(status="sent")
        assert len(rows) == 1
        assert rows[0]["Status"] == "sent"
        assert rows[0]["Subject"] == "S1"

    def test_status_filter_failed(self, _isolate_sqlite):
        repo = _repo()
        self._insert_two(repo)
        rows, _ = repo.list_email_history(status="failed")
        assert len(rows) == 1
        assert rows[0]["Status"] == "failed"

    def test_status_filter_no_match(self, _isolate_sqlite):
        repo = _repo()
        self._insert_two(repo)
        rows, _ = repo.list_email_history(status="resent")
        assert rows == []


class TestGetEmailHistoryById:
    """get_email_history_by_id returns the correct row or None."""

    def test_found(self, _isolate_sqlite):
        repo = _repo()
        repo.append_email_history(None, "x@y.com", "Sub", "sent")
        rows, _ = repo.list_email_history()
        record_id = rows[0]["Id"]
        fetched = repo.get_email_history_by_id(record_id)
        assert fetched is not None
        assert fetched["Id"] == record_id
        assert fetched["Subject"] == "Sub"

    def test_not_found(self, _isolate_sqlite):
        repo = _repo()
        result = repo.get_email_history_by_id(99999)
        assert result is None


class TestMarkEmailResent:
    """mark_email_resent updates Status and ResentAt."""

    def test_mark_resent(self, _isolate_sqlite):
        repo = _repo()
        repo.append_email_history(None, "a@b.com", "Re-Test", "sent")
        rows, _ = repo.list_email_history()
        record_id = rows[0]["Id"]

        assert rows[0]["Status"] == "sent"
        assert rows[0]["ResentAt"] is None

        repo.mark_email_resent(record_id)

        updated = repo.get_email_history_by_id(record_id)
        assert updated["Status"] == "resent"
        assert updated["ResentAt"] is not None


class TestCursorPagination:
    """Cursor pagination returns correct pages and a valid next_cursor."""

    def _insert_n(self, repo: OperationsRepo, n: int) -> None:
        for i in range(n):
            repo.append_email_history(None, "a@b.com", f"Subject {i}", "sent")

    def test_single_page(self, _isolate_sqlite):
        repo = _repo()
        self._insert_n(repo, 3)
        rows, next_cursor = repo.list_email_history(limit=10)
        assert len(rows) == 3
        assert next_cursor is None

    def test_pagination(self, _isolate_sqlite):
        repo = _repo()
        self._insert_n(repo, 5)

        page1, cursor1 = repo.list_email_history(limit=3)
        assert len(page1) == 3
        assert cursor1 is not None

        page2, cursor2 = repo.list_email_history(limit=3, cursor=cursor1)
        assert len(page2) == 2
        assert cursor2 is None

        # No Id overlap between pages
        ids1 = {r["Id"] for r in page1}
        ids2 = {r["Id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_invalid_cursor_raises(self, _isolate_sqlite):
        repo = _repo()
        with pytest.raises(ValueError, match="invalid cursor"):
            repo.list_email_history(cursor="not-valid-base64!!!")
