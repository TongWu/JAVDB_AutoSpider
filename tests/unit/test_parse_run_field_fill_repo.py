# tests/unit/test_parse_run_field_fill_repo.py
import sqlite3

import pytest

from javdb.ops.sentinel.models import FieldFill
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ParseRunFieldFillRepo(c)


def test_upsert_and_get(repo):
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.97, 100)])
    got = {f.field: f for f in repo.get_fills("S1")}
    assert got["href"].fill_rate == 0.97


def test_upsert_idempotent(repo):
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.9, 100)])
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.5, 100)])
    got = {f.field: f for f in repo.get_fills("S1")}
    assert got["href"].fill_rate == 0.5


def test_baseline_uses_committed_only_median(repo):
    repo.upsert_fills("S1", [FieldFill("index", "rate", 0.90, 100)]); repo.mark_committed("S1")
    repo.upsert_fills("S2", [FieldFill("index", "rate", 0.80, 100)]); repo.mark_committed("S2")
    repo.upsert_fills("S3", [FieldFill("index", "rate", 0.10, 100)])  # NOT committed
    assert repo.baseline("index", "rate", window=14) == 0.85  # median(0.90, 0.80)


def test_baseline_none_when_no_committed_rows(repo):
    repo.upsert_fills("S1", [FieldFill("index", "rate", 0.9, 100)])  # uncommitted
    assert repo.baseline("index", "rate", window=14) is None
