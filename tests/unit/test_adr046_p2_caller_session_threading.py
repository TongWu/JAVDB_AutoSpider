"""ADR-046 Phase 2: callers of the now-session-bound OperationsRepo writes
resolve the active session and bind it on the repo constructor (not the
process-global inside the repo).

Each test monkeypatches ``OperationsRepo`` with a capturing fake and sets the
active session via the process-global (the canonical "a run is in progress"
signal these callers still read; Phase 5 migrates that read).
"""
from unittest.mock import MagicMock

import pytest

from javdb.storage.db import set_active_session_id

_SID = "20260603T000000.000000Z-call-0001"


@pytest.fixture
def active_session():
    set_active_session_id(_SID)
    yield _SID
    set_active_session_id(None)


def _capturing_repo():
    """Return (repo_cls, repo_instance) where the class records ctor kwargs."""
    repo = MagicMock()
    repo_cls = MagicMock(return_value=repo)
    return repo_cls, repo


def test_dedup_append_binds_active_session(monkeypatch, active_session):
    import javdb.spider.services.dedup as dedup
    from javdb.spider.services.dedup import DedupRecord, append_dedup_record

    repo_cls, repo = _capturing_repo()
    repo.append_dedup_record.return_value = 1
    monkeypatch.setattr(dedup, "OperationsRepo", repo_cls)
    monkeypatch.setattr(dedup, "_ensure_db", lambda: None)
    monkeypatch.setattr(dedup, "_load_pending_paths_cache", lambda: set())

    rec = DedupRecord("A-001", "s", "sub", "gdrive:/p", 100, "cat", "r", "t", "False", "")
    append_dedup_record("", rec)

    assert repo_cls.call_args.kwargs["session_id"] == active_session
    # The session is bound on the ctor, not passed to the write method.
    assert "session_id" not in repo.append_dedup_record.call_args.kwargs


def test_dedup_mark_records_deleted_binds_active_session(monkeypatch, active_session):
    import javdb.spider.services.dedup as dedup
    from javdb.spider.services.dedup import mark_records_deleted

    repo_cls, repo = _capturing_repo()
    repo.mark_records_deleted.return_value = 1
    monkeypatch.setattr(dedup, "OperationsRepo", repo_cls)
    monkeypatch.setattr(dedup, "_ensure_db", lambda: None)

    mark_records_deleted("", [("gdrive:/p", "2026-01-02 00:00:00")])

    assert repo_cls.call_args.kwargs["session_id"] == active_session
    assert "session_id" not in repo.mark_records_deleted.call_args.kwargs


def test_pikpak_append_history_binds_active_session(monkeypatch, active_session):
    import javdb.storage.repos.operations_repo as ops_repo_mod
    import javdb.storage.db as db_mod
    from javdb.integrations.pikpak.bridge.service import save_to_pikpak_history

    repo_cls, repo = _capturing_repo()
    monkeypatch.setattr(ops_repo_mod, "OperationsRepo", repo_cls)
    # The caller is gated on use_sqlite() and calls init_db(); stub both.
    monkeypatch.setattr(
        "javdb.infra.config.use_sqlite", lambda: True,
    )
    monkeypatch.setattr(
        "javdb.infra.config.use_csv", lambda: False,
    )
    monkeypatch.setattr(db_mod, "init_db", lambda *a, **k: None)

    torrent_info = {
        "hash": "abc",
        "name": "Some.Torrent",
        "category": "Uncensored",
        "magnet_uri": "magnet:?xt=...",
        "added_on": 1_700_000_000,
    }
    save_to_pikpak_history(torrent_info, "success")

    assert repo_cls.call_args.kwargs["session_id"] == active_session
    assert "session_id" not in repo.append_pikpak_history.call_args.kwargs


def test_rclone_self_heal_binds_active_session(monkeypatch, active_session):
    """The dedup self-heal binds the resolved session on the repo ctor."""
    import javdb.integrations.rclone.manager.service as rm

    repo_cls, repo = _capturing_repo()
    repo.load_rclone_inventory.return_value = {
        "A": [{"FolderPath": "2025/Actor/A/有码-中字"}],
    }
    repo.load_dedup_records.return_value = [{
        "IsDeleted": 0,
        "ExistingGdrivePath": "2025/Actor/ORPHAN/有码-中字",
        "DeletionReason": "Subtitle upgrade",
    }]
    repo.mark_orphan_records.return_value = 1
    monkeypatch.setattr(rm, "OperationsRepo", repo_cls)
    monkeypatch.setattr(rm, "_write_dedup_orphan_csv", lambda *_, **__: None)

    rm.validate_dedup_records_against_inventory()

    # Two OperationsRepo() instances are built (loads + mark); all bind the
    # resolved session. Assert the last (the mark_orphan_records one).
    assert repo_cls.call_args.kwargs["session_id"] == active_session
    assert "session_id" not in repo.mark_orphan_records.call_args.kwargs
