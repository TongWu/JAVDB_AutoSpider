# storage

Persistence layer: SQLite + Cloudflare D1 backends, dual-write coordination, session-aware history management, and migration-aware schema bootstrapping.

## Files

| File | Purpose |
|---|---|
| `d1_client.py` | Cloudflare D1 HTTP client with a sqlite3-Connection-compatible facade. |
| `dual_connection.py` | Dual-write SQLite + D1 connection facade used during the SQLite→D1 migration parallel-test phase. |
| `history_manager.py` | History reader/writer with `STORAGE_MODE` switching (`db`/`csv`/`duo`); Rust-accelerated CSV path when available. |
| `sqlite_datetime.py` | Normalises TEXT datetime values stored in SQLite to naive SGT `YYYY-MM-DD HH:MM:SS`. |

## Subdirectories

- `db/` — Low-level SQLite connection management and per-domain CRUD modules (history, reports, operations, stats, sessions, rollback, migrations).
- `repos/` — High-level repository pattern wrappers over `db/` (history, operations, sessions, system_state).
- `sessions/` — Session commit library (force a session into `committed` state).
- `rollback/` — Rollback library (plan + apply rollback by session-id or GH run identity).

## Depends on

- Upstream callers: `javdb.pipeline.service`, `javdb.spider.compat.csv_builder`, `apps.cli.rollback`, `apps.cli.migration`, `apps.api`, all `javdb.migrations.tools.*`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`, `javdb.spider.contracts`, `javdb.rust_core` (for CSV path).
