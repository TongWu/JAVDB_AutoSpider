import sqlite3

import pytest

from javdb.ops.reconcile import service
from javdb.ops.reconcile.models import (
    AcquisitionOutcomeRecord,
    OwnershipOptions,
)
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo
from javdb.spider.services.dedup_types import RcloneEntry

_OUTCOME_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""

_LEDGER_DDL = """
CREATE TABLE OwnershipLedger (
  video_code TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
  path TEXT, size INTEGER, present INTEGER NOT NULL DEFAULT 1, observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);
"""


@pytest.fixture
def outcome_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_OUTCOME_DDL)
    return AcquisitionOutcomeRepo(c)


@pytest.fixture
def ledger_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_LEDGER_DDL)
    return OwnershipLedgerRepo(c)


def test_list_pending_landing_excludes_failed_and_already_landed(outcome_repo):
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="q", video_code="A-1", state="queued"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="d", video_code="A-2", state="downloading"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-3", state="completed"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="f", video_code="A-4", state="failed"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="l", video_code="A-5", state="in_library"))
    codes = {r.video_code for r in outcome_repo.list_pending_landing()}
    assert codes == {"A-1", "A-2", "A-3"}   # failed + in_library excluded


def test_mark_in_library_sets_state_and_landed_at(outcome_repo):
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-3", state="completed"))
    outcome_repo.mark_in_library("c", landed_at="t-land")
    got = outcome_repo.get("c")
    assert got.state == "in_library"
    assert got.landed_at == "t-land"


# --- Service half (ADR-033 Phase 2 D-P2-8) ----------------------------------

def test_run_ownership_service_derives_in_library_via_gdrive(ledger_repo, outcome_repo):
    """run_ownership promotes completed outcomes to in_library when their
    video_code is present in a persistent source (gdrive/nas)."""
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-1", state="completed"))
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",), derive_in_library=True),
        repo=ledger_repo, outcome_repo=outcome_repo,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.marked_in_library == 1
    got = outcome_repo.get("c")
    assert got.state == "in_library"
    assert got.landed_at is not None
