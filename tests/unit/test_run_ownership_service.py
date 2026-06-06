# tests/unit/test_run_ownership_service.py
import sqlite3

import pytest

from javdb.ops.reconcile import service
from javdb.ops.reconcile.models import OwnershipLedgerRecord, OwnershipOptions
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.spider.services.dedup import RcloneEntry

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
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
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


def test_load_gdrive_inventory_normalises_fullwidth_codes():
    """_load_gdrive_inventory normalises full-width codes via NFKC+strip+upper.

    A full-width CJK code (built from codepoints below) must be stored as the
    ASCII key SSNI-001 and the RcloneEntry.video_code must also be normalised,
    so dedup and in_library join never see duplicate keys.
    """
    from javdb.ops.reconcile.service import _load_gdrive_inventory
    from javdb.spider.services.dedup import RcloneEntry
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
