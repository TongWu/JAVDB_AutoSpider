import logging
import sqlite3

import pytest

from packages.python.javdb_migrations.tools import migrate_rclone_paths_to_code_dir as migrate


def test_dry_run_integrity_error_message_does_not_reference_backup(
    tmp_path,
    monkeypatch,
    caplog,
):
    db_path = tmp_path / "operations.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE RcloneInventory (Id INTEGER PRIMARY KEY, FolderPath TEXT)")
        conn.execute("CREATE TABLE DedupRecords (Id INTEGER PRIMARY KEY, ExistingGdrivePath TEXT)")

    calls = []

    def fake_migrate_table(conn, *, table, column, pk="Id", dry_run):
        calls.append((table, column, pk, dry_run))
        if table == "DedupRecords":
            raise sqlite3.IntegrityError("duplicate path")
        return 0, 0

    monkeypatch.setattr(migrate, "_migrate_table", fake_migrate_table)
    caplog.set_level(logging.ERROR, logger=migrate.logger.name)

    with pytest.raises(sqlite3.IntegrityError, match="duplicate path"):
        migrate.migrate_db(str(db_path), dry_run=True)

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "dry-run" in message
    assert "No backup was created" in message
    assert "no database changes were committed" in message
    assert "committed=False" in message
    assert "backup=None" not in message
    assert "Restore from the backup path" not in message
    assert "RcloneInventory changes were committed" not in message
    assert calls == [
        ("RcloneInventory", "FolderPath", "Id", True),
        ("DedupRecords", "ExistingGdrivePath", "Id", True),
    ]


def test_integrity_error_message_reports_no_rclone_inventory_commit(
    tmp_path,
    monkeypatch,
    caplog,
):
    db_path = tmp_path / "operations.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE RcloneInventory (Id INTEGER PRIMARY KEY, FolderPath TEXT)")
        conn.execute("CREATE TABLE DedupRecords (Id INTEGER PRIMARY KEY, ExistingGdrivePath TEXT)")

    def fake_migrate_table(conn, *, table, column, pk="Id", dry_run):
        if table == "DedupRecords":
            raise sqlite3.IntegrityError("duplicate path")
        return 0, 0

    monkeypatch.setattr(migrate, "_migrate_table", fake_migrate_table)
    caplog.set_level(logging.ERROR, logger=migrate.logger.name)

    with pytest.raises(sqlite3.IntegrityError, match="duplicate path"):
        migrate.migrate_db(str(db_path), dry_run=False)

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "RcloneInventory existed=True, rewrites=0, committed=False" in message
    assert "No RcloneInventory rewrite was committed" in message
    assert "Restore from the backup path" not in message


def test_migrate_db_does_not_log_updated_when_no_rows_change(tmp_path, caplog):
    db_path = tmp_path / "operations.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE RcloneInventory (Id INTEGER PRIMARY KEY, FolderPath TEXT)")
        conn.execute("CREATE TABLE DedupRecords (Id INTEGER PRIMARY KEY, ExistingGdrivePath TEXT)")
        conn.execute("INSERT INTO RcloneInventory (FolderPath) VALUES ('already/new/layout')")
        conn.execute("INSERT INTO DedupRecords (ExistingGdrivePath) VALUES ('already/new/layout')")

    caplog.set_level(logging.INFO, logger=migrate.logger.name)

    migrate.migrate_db(str(db_path), dry_run=False)

    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "DB updated (RcloneInventory)" not in message
    assert "DB updated (DedupRecords)" not in message
