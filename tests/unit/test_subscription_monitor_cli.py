"""CLI smoke tests for apps.cli.ops.subscription_monitor (ADR-054 WS2)."""

from javdb.storage import db as _db
from javdb.storage.db import _db_connection, _db_migrations


def test_dry_run_initializes_empty_history_db(tmp_path, monkeypatch):
    """Dry-run should work on a fresh SQLite mirror, not require pre-created tables."""
    paths = {
        "HISTORY_DB_PATH": str(tmp_path / "history.db"),
        "REPORTS_DB_PATH": str(tmp_path / "reports.db"),
        "OPERATIONS_DB_PATH": str(tmp_path / "operations.db"),
    }
    for name, path in paths.items():
        monkeypatch.setattr(_db, name, path)
        monkeypatch.setattr(_db_connection, name, path)
        monkeypatch.setattr(_db_migrations, name, path)

    from apps.cli.ops.subscription_monitor import main

    assert main(["--dry-run"]) == 0
