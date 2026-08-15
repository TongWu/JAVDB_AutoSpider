# tests/unit/test_run_ownership_service.py
import sqlite3

import pytest

from javdb.ops.reconcile import service
from javdb.ops.reconcile.models import OwnershipLedgerRecord, OwnershipOptions
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.spider.services.dedup_types import RcloneEntry

_LEDGER_DDL = """
CREATE TABLE OwnershipLedger (
  video_code TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
  path TEXT, size INTEGER, present INTEGER NOT NULL DEFAULT 1, observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);
"""
_OUTCOME_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT,
  state_changed_at TEXT
);
"""


@pytest.fixture
def ledger():
    c = sqlite3.connect(":memory:")
    c.executescript(_LEDGER_DDL)
    return OwnershipLedgerRepo(c)


@pytest.fixture
def outcomes():
    c = sqlite3.connect(":memory:")
    c.executescript(_OUTCOME_DDL)
    return AcquisitionOutcomeRepo(c)


def test_run_ownership_upserts_gdrive_snapshot(ledger, outcomes):
    inv = {"A-1": [RcloneEntry("A-1", "无码破解", "中字", "/g/x", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.upserted == 1
    assert ledger.get("A-1", "gdrive", "无码破解|中字").present == 1


def test_run_ownership_sweeps_absent_gdrive_rows(ledger, outcomes):
    ledger.upsert(OwnershipLedgerRecord("OLD", "gdrive", "有码|无字", present=1, observed_at="t0"))
    inv = {"NEW": [RcloneEntry("NEW", "无码", "中字", "/g/n", 5, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.swept_absent == 1
    assert ledger.get("OLD", "gdrive", "有码|无字").present == 0   # swept, not deleted
    assert ledger.get("NEW", "gdrive", "无码|中字").present == 1


def test_run_ownership_does_not_sweep_pikpak(ledger, outcomes):
    ledger.upsert(OwnershipLedgerRecord("OLDPK", "pikpak", "", present=1, observed_at="t0"))
    res = service.run_ownership(
        OwnershipOptions(sources=("pikpak",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory={}, qb_outcomes=[], pikpak_rows=[],  # empty pikpak snapshot
    )
    assert res.swept_absent == 0
    assert ledger.get("OLDPK", "pikpak", "").present == 1   # monotonic: not swept


def test_run_ownership_derives_in_library(ledger, outcomes):
    outcomes.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-1", state="completed"))
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.marked_in_library == 1
    assert outcomes.get("c").state == "in_library"
    assert outcomes.get("c").landed_at is not None


def test_run_ownership_leaves_failed_untouched(ledger, outcomes):
    outcomes.upsert(AcquisitionOutcomeRecord(qb_hash="f", video_code="A-1", state="failed"))
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert outcomes.get("f").state == "failed"   # D-P2-8: failed left untouched


def test_run_ownership_dry_run_writes_nothing(ledger, outcomes):
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    service.run_ownership(
        OwnershipOptions(sources=("gdrive",), dry_run=True),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert ledger.get("A-1", "gdrive", "无码|中字") is None


def test_run_ownership_observed_counter(ledger, outcomes):
    """result.observed equals the number of collected observations."""
    inv = {
        "A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")],
        "B-2": [RcloneEntry("B-2", "有码", "无字", "/g/y", 20, 2, "t")],
    }
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.observed == 2


def test_run_ownership_delta_skips_unchanged_rows(ledger, outcomes):
    """Steady-state: re-running with the same inventory should upsert 0 rows."""
    inv = {
        "A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")],
        "B-2": [RcloneEntry("B-2", "有码", "无字", "/g/y", 20, 2, "t")],
    }
    # First run — should upsert both
    res1 = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res1.upserted == 2

    # Second run with same data — delta logic skips full upsert
    res2 = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res2.upserted == 0
    assert res2.observed == 2


def test_run_ownership_delta_refreshes_observed_at(ledger, outcomes):
    """Unchanged rows still get observed_at refreshed (ADR-033 D10 freshness)."""
    from unittest.mock import patch

    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    # First run — establishes the row
    with patch("javdb.ops.reconcile.service.utc_now_iso", return_value="2026-01-01T00:00:00Z"):
        service.run_ownership(
            OwnershipOptions(sources=("gdrive",)),
            repo=ledger, outcome_repo=outcomes,
            rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
        )
    assert ledger.get("A-1", "gdrive", "无码|中字").observed_at == "2026-01-01T00:00:00Z"

    # Second run with same data but later timestamp — observed_at must refresh
    with patch("javdb.ops.reconcile.service.utc_now_iso", return_value="2026-01-01T01:00:00Z"):
        res = service.run_ownership(
            OwnershipOptions(sources=("gdrive",)),
            repo=ledger, outcome_repo=outcomes,
            rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
        )
    assert res.upserted == 0  # no content change
    assert ledger.get("A-1", "gdrive", "无码|中字").observed_at == "2026-01-01T01:00:00Z"


def test_run_ownership_delta_detects_path_change(ledger, outcomes):
    """A changed path triggers re-upsert even if video_code+category are the same."""
    inv1 = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/old", 10, 1, "t")]}
    service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv1, qb_outcomes=[], pikpak_rows=[],
    )
    inv2 = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/new", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv2, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.upserted == 1
    assert ledger.get("A-1", "gdrive", "无码|中字").path == "/g/new"


def test_run_ownership_delta_detects_size_change(ledger, outcomes):
    """A changed size triggers re-upsert."""
    inv1 = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv1, qb_outcomes=[], pikpak_rows=[],
    )
    inv2 = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 99, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv2, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.upserted == 1
    assert ledger.get("A-1", "gdrive", "无码|中字").size == 99


def test_run_ownership_delta_re_upserts_swept_row(ledger, outcomes):
    """A row previously swept to present=0 must be re-upserted when it reappears."""
    ledger.upsert(OwnershipLedgerRecord("A-1", "gdrive", "无码|中字", path="/g/x", size=10, present=0, observed_at="t0"))
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.upserted == 1
    assert ledger.get("A-1", "gdrive", "无码|中字").present == 1


def test_load_gdrive_inventory_normalises_fullwidth_codes():
    """_load_gdrive_inventory normalises full-width codes via NFKC+strip+upper.

    A full-width CJK code (built from codepoints below) must be stored as the
    ASCII key SSNI-001 and the RcloneEntry.video_code must also be normalised,
    so dedup and in_library join never see duplicate keys.
    """
    from javdb.ops.reconcile.service import _load_gdrive_inventory
    from javdb.spider.services.dedup_types import RcloneEntry
    from unittest.mock import patch

    # Full-width "SSNI" via codepoints so the source stays ASCII (no RUF001/002).
    fullwidth_code = "".join(chr(c) for c in (0xFF33, 0xFF33, 0xFF2E, 0xFF29)) + "-001"
    expected_ascii = "SSNI-001"               # NFKC normalised form

    fake_raw = {
        fullwidth_code: [
            {
                "VideoCode": fullwidth_code,
                "SensorCategory": "无码",
                "SubtitleCategory": "中字",
                "FolderPath": "/g/z",
                "FolderSize": "5000",
                "FileCount": "3",
                "DateTimeScanned": "2026-01-01",
            }
        ]
    }

    with patch(
        "javdb.storage.repos.operations_repo.OperationsRepo.load_rclone_inventory",
        return_value=fake_raw,
    ):
        inv = _load_gdrive_inventory()

    assert expected_ascii in inv, "dict key must be normalised to ASCII form"
    assert fullwidth_code not in inv, "full-width key must not survive normalisation"
    entry: RcloneEntry = inv[expected_ascii][0]
    assert entry.video_code == expected_ascii, "RcloneEntry.video_code must also be normalised"
