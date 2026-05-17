# db

Low-level SQLite/D1 database modules: connection routing, per-table CRUD, migrations, and the session-state singleton used by the pipeline.

## Files

| File | Purpose |
|---|---|
| `db.py` | Top-level SQLite database management; three-DB split layout (history/reports/operations). |
| `db_connection.py` | Connection pooling, backend routing (SQLite/D1/Dual), WAL setup; backend selected by `STORAGE_BACKEND` env var. |
| `db_history_read.py` | Reads from `MovieHistory` and `TorrentHistory` tables in `history.db`. |
| `db_history_write.py` | Writes to `MovieHistory` and `TorrentHistory` tables; supports `pending` (default target) and `audit` (legacy) modes. |
| `db_migrations.py` | Schema initialisation, version detection, and migration helpers (v5→v6→v7→v8 schema bumps). |
| `db_operations.py` | CRUD for `RcloneInventory`, `DedupRecords`, `PikpakHistory` in `operations.db`. |
| `db_reports.py` | `ReportSessions` / `ReportMovies` / `ReportTorrents` management in `reports.db` (session lifecycle states). |
| `db_rollback.py` | Coordinator that orchestrates per-DB rollback across history/reports/operations (pending-delete or audit-restore). |
| `db_session.py` | Active-session state singleton (Session ID, Run ID, Write Mode) shared across subprocesses. |
| `db_stats.py` | `SpiderStats` / `UploaderStats` / `PikpakStats` reads and writes (idempotent on SessionId). |

## Subdirectories

(none)

## Depends on

- Upstream callers: `javdb.storage.repos.*`, `javdb.storage.history_manager`, `javdb.pipeline`, `javdb.spider`, `javdb.migrations.tools.*`, `apps.api`, `apps.cli`.
- Downstream: `javdb.storage.d1_client`, `javdb.storage.dual_connection`, `javdb.storage.sqlite_datetime`, `javdb.infra.config`, `javdb.infra.logging`.
