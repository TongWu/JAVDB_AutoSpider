# tests/unit/test_parse_field_fill_latest.py
import sqlite3

import pytest

from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


def _row(conn, sid, field, rate, observed_at, committed):
    conn.execute(
        "INSERT INTO ParseRunFieldFill "
        "(session_id, page_type, field, fill_rate, sample_count, committed, observed_at) "
        "VALUES (?, 'index', ?, ?, 100, ?, ?)",
        [sid, field, rate, committed, observed_at],
    )


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c, ParseRunFieldFillRepo(c)


def test_latest_committed_wins(repo):
    conn, r = repo
    _row(conn, "S1", "href", 0.90, "2026-06-01T00:00:00Z", 1)
    _row(conn, "S2", "href", 0.95, "2026-06-03T00:00:00Z", 1)  # newer
    _row(conn, "S3", "href", 0.10, "2026-06-04T00:00:00Z", 0)  # newest but uncommitted
    rows = {row[1]: row for row in r.latest_committed_fills()}
    assert rows["href"][2] == 0.95          # fill_rate from S2 (newest committed)
    assert rows["href"][0] == "index"       # page_type
    assert rows["href"][4] == "2026-06-03T00:00:00Z"


def test_excludes_fields_with_no_committed_row(repo):
    conn, r = repo
    _row(conn, "S1", "rate", 0.80, "2026-06-01T00:00:00Z", 0)  # uncommitted only
    assert r.latest_committed_fills() == []


def test_latest_committed_dedupes_on_observed_at_tie(repo):
    conn, r = repo
    # Two committed runs for the same field with the SAME observed_at: the method
    # must still return exactly one row (deterministic by session_id), not a dup.
    _row(conn, "S1", "href", 0.90, "2026-06-03T00:00:00Z", 1)
    _row(conn, "S2", "href", 0.10, "2026-06-03T00:00:00Z", 1)  # tie on observed_at
    out = [row for row in r.latest_committed_fills() if row[1] == "href"]
    assert len(out) == 1                 # no duplicate field row
    assert out[0][2] == 0.10             # higher session_id (S2) wins the tie


def test_baseline_before_excludes_current_and_newer(repo):
    conn, r = repo
    _row(conn, "S1", "rate", 0.90, "2026-06-01T00:00:00Z", 1)
    _row(conn, "S2", "rate", 0.80, "2026-06-02T00:00:00Z", 1)
    _row(conn, "S3", "rate", 0.10, "2026-06-03T00:00:00Z", 1)  # current/newest
    # before=current's observed_at -> only the two prior committed rows count.
    assert r.baseline("index", "rate", window=14, before="2026-06-03T00:00:00Z") == 0.85
    # before=oldest -> no strictly-earlier committed row -> None (pure-historical).
    assert r.baseline("index", "rate", window=14, before="2026-06-01T00:00:00Z") is None
    # no before -> all committed rows (unchanged Phase-1 behaviour).
    assert r.baseline("index", "rate", window=14) == 0.80  # median(0.90, 0.80, 0.10)
