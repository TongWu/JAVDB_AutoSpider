# tests/unit/test_ownership_ledger_repo.py
import sqlite3

import pytest

from javdb.ops.reconcile.models import OwnershipLedgerRecord
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo

_DDL = """
CREATE TABLE OwnershipLedger (
  video_code TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
  path TEXT, size INTEGER, present INTEGER NOT NULL DEFAULT 1, observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return OwnershipLedgerRepo(c)


def test_upsert_then_get(repo):
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", path="/g/x", size=10, observed_at="t1"))
    got = repo.get("ABC-1", "gdrive", "无码破解|中字")
    assert got.path == "/g/x"
    assert got.present == 1


def test_upsert_is_idempotent_on_pk(repo):
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", size=10, observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", size=20, observed_at="t2"))
    assert repo.get("ABC-1", "gdrive", "无码破解|中字").size == 20
    assert repo._conn.execute("SELECT COUNT(*) FROM OwnershipLedger").fetchone()[0] == 1


def test_distinct_category_is_a_distinct_row(repo):
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "有码|无字", observed_at="t1"))
    rows = repo.list_by_source("gdrive")
    assert {r.category for r in rows} == {"无码破解|中字", "有码|无字"}


def test_mark_absent_sweeps_only_unseen_rows_of_that_source(repo):
    repo.upsert(OwnershipLedgerRecord("A", "gdrive", "c1", observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("B", "gdrive", "c1", observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("C", "nas", "", observed_at="t1"))   # different source untouched
    swept = repo.mark_absent("gdrive", present_keys={("A", "c1")})
    assert swept == 1                                   # only B swept
    assert repo.get("A", "gdrive", "c1").present == 1
    assert repo.get("B", "gdrive", "c1").present == 0   # swept, not deleted
    assert repo.get("C", "nas", "").present == 1        # other source intact


def test_list_present_video_codes_filters_by_present_and_source(repo):
    repo.upsert(OwnershipLedgerRecord("OWNED", "gdrive", "c1", present=1, observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("GONE", "gdrive", "c1", present=0, observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("QBONLY", "qb", "subtitle", present=1, observed_at="t1"))
    codes = repo.list_present_video_codes(("gdrive", "nas"))
    assert codes == {"OWNED"}     # GONE swept; QBONLY is not a persistent source


def test_upsert_reactivates_swept_row(repo):
    # upsert → mark_absent (present=0) → upsert again (present back to 1)
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "c1", path="/g/x", size=10, observed_at="t1"))
    swept = repo.mark_absent("gdrive", present_keys=set())   # sweep everything in gdrive
    assert swept == 1
    assert repo.get("ABC-1", "gdrive", "c1").present == 0    # confirm swept

    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "c1", path="/g/x", size=10, present=1, observed_at="t2"))
    reactivated = repo.get("ABC-1", "gdrive", "c1")
    assert reactivated.present == 1                           # row is live again
    assert reactivated.observed_at == "t2"                   # observed_at refreshed by upsert
    assert repo._conn.execute("SELECT COUNT(*) FROM OwnershipLedger").fetchone()[0] == 1  # no duplicate row
