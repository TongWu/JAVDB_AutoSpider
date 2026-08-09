from __future__ import annotations

from contextlib import contextmanager

from javdb.ops.reconcile import persistence
from javdb.storage import db as _db


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

    # OPERATIONS_DB_PATH is resolved at call time (BFR-016): read it from the
    # package the same way open_outcome_repo does so the autouse _isolate_sqlite
    # path monkeypatch is reflected on both sides.
    assert seen_paths == [_db.OPERATIONS_DB_PATH]
