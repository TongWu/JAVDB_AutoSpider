# storage

Persistence layer: SQLite + Cloudflare D1 backends, dual-write coordination, session-aware history management, and migration-aware schema bootstrapping.

## Write-Class Boundary

New D1-backed writes are not one undifferentiated category. Any new write path must declare its class before it lands:

- **authoritative** — session-scoped, fail closed; decides whether the session commits.
- **additive** — replayable / rebuildable; never decides session success.
- **diagnostic** — explains drift / recovery; never decides session truth.

See [ADR-042](../../docs/design/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) and the `写入边界分类` section of `CONTEXT.md` for the canonical definitions.

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
