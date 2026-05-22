# repos

Repository-pattern wrappers over the raw `javdb.storage.db` modules — provide higher-level, typed APIs for application, API, and legacy facade layers.

Two Repo shapes coexist (ADR-005 amendment 2):

- **Read-Repos** (`SessionsRepo`, `SystemStateRepo`): `__init__(conn)` — caller owns the connection; used for short API reads.
- **Write-Repos** (`HistoryRepo`, `OperationsRepo`, `StatsRepo`): `__init__(*, db_path=None)` — the underlying function family manages its own transactions; `session_id` flows per method call.

## Files

| File | Purpose |
|---|---|
| `history_repo.py` | Module-level history helpers + `HistoryRepo` class wrapping `db_history_read` / `db_history_write`. |
| `operations_repo.py` | Module-level rclone staging helpers + `OperationsRepo` class wrapping `db_operations`. |
| `stats_repo.py` | `StatsRepo` class wrapping `db_stats` (SpiderStats / UploaderStats / PikpakStats). |
| `sessions_repo.py` | Cursor-paginated listing and per-session detail queries over `ReportSessions`. |
| `system_state_repo.py` | Generic key-value store against the `system_state` table in `operations.db`. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.api.routers.*`, `apps.cli.rollback`, `apps.cli.migration`, `javdb.pipeline.service`, and selected legacy `javdb.storage.db.db` facade functions during ADR-005 PR-2 migration.
- Downstream: `javdb.storage.db.*`, `apps.api.parsers.common`, `javdb.infra.logging`.
