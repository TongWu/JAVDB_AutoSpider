# tests/unit/test_dedup_reads_ledger.py
import contextlib
import inspect

from javdb.spider.services import dedup


@contextlib.contextmanager
def _ctx(repo):
    """Wrap a fake repo as the context manager _open_ledger_for_dedup now is."""
    yield repo


def test_public_api_signatures_unchanged():
    # load_rclone_inventory still takes a single csv_path positional.
    sig = inspect.signature(dedup.load_rclone_inventory)
    assert list(sig.parameters) == ["csv_path"]
    # the three consumer-facing entrypoints keep their names + arity
    assert callable(dedup.should_skip_from_rclone)
    assert callable(dedup.check_dedup_upgrade)
    assert callable(dedup.check_redownload_dedup_upgrade)
    # new persistent-source presence helper exists
    assert callable(dedup.should_skip_from_ownership)


def test_ledger_rows_synthesize_rclone_entries(monkeypatch):
    # Stub the Ledger read to return one gdrive row with a glyph composite.
    class _FakeLedger:
        def list_by_source(self, source):
            from javdb.ops.reconcile.models import OwnershipLedgerRecord
            assert source == "gdrive"
            return [OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", path="/g/x", size=10, present=1)]

        def list_present_video_codes(self, sources):
            return {"ABC-1"}

    monkeypatch.setattr(dedup, "_open_ledger_for_dedup", lambda: _ctx(_FakeLedger()))
    monkeypatch.setattr(dedup, "_ledger_has_gdrive_rows", lambda repo: True)

    inv = dedup.load_rclone_inventory("ignored.csv")
    entries = inv["ABC-1"]
    assert entries[0].sensor_category == "无码破解"
    assert entries[0].subtitle_category == "中字"
    assert entries[0].folder_path == "/g/x"


def test_should_skip_from_ownership_uses_persistent_sources(monkeypatch):
    class _FakeLedger:
        def list_present_video_codes(self, sources):
            assert set(sources) == {"gdrive", "nas"}
            return {"OWNED-1"}

    monkeypatch.setattr(dedup, "_open_ledger_for_dedup", lambda: _ctx(_FakeLedger()))
    assert dedup.should_skip_from_ownership("owned-1") is True   # normalised match
    assert dedup.should_skip_from_ownership("MISSING-9") is False


def test_load_rclone_inventory_falls_back_to_legacy_when_ledger_empty(monkeypatch, caplog):
    """D-P2-9 transitional fallback: empty Ledger -> legacy RcloneInventory used.

    Verifies:
    - `_legacy_load_rclone_inventory` result is returned unchanged.
    - A WARNING-level log fires containing the key phrase about falling back.
    """
    import logging

    _LEGACY_DATA = {
        "DEF-2": [
            dedup.RcloneEntry(
                video_code="DEF-2",
                sensor_category="有码",
                subtitle_category="中字",
                folder_path="/nas/def2",
                folder_size=5_000_000,
                file_count=3,
                scan_datetime="2026-01-01 00:00:00",
            )
        ]
    }

    class _EmptyLedger:
        def list_by_source(self, source):
            return []  # zero gdrive rows -> triggers fallback

    monkeypatch.setattr(dedup, "_open_ledger_for_dedup", lambda: _ctx(_EmptyLedger()))
    monkeypatch.setattr(dedup, "_ledger_has_gdrive_rows", lambda repo: False)
    monkeypatch.setattr(dedup, "_legacy_load_rclone_inventory", lambda csv_path: _LEGACY_DATA)

    with caplog.at_level(logging.WARNING, logger="javdb.spider.services.dedup"):
        result = dedup.load_rclone_inventory("irrelevant.csv")

    # Fallback inventory must equal the legacy data exactly.
    assert result == _LEGACY_DATA, "Expected legacy inventory to be returned on fallback"

    # A WARNING about the fallback must have been emitted.
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "OwnershipLedger has no gdrive rows" in msg and "falling back to legacy" in msg
        for msg in warning_messages
    ), f"Expected fallback warning not found in: {warning_messages}"
