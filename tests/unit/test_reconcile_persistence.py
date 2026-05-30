from __future__ import annotations

from contextlib import contextmanager

from javdb.ops.reconcile import persistence
from javdb.storage.db import OPERATIONS_DB_PATH


def test_open_outcome_repo_uses_operations_db_path(monkeypatch):
    seen_paths = []
    fake_conn = object()
    fake_repo = object()

    @contextmanager
    def fake_get_db(path):
        seen_paths.append(path)
        yield fake_conn

    monkeypatch.setattr(persistence, "get_db", fake_get_db)
    monkeypatch.setattr(persistence, "AcquisitionOutcomeRepo", lambda conn: fake_repo)

    with persistence.open_outcome_repo() as repo:
        assert repo is fake_repo

    assert seen_paths == [OPERATIONS_DB_PATH]
