# migrations

Database migration runner + tool scripts: schema bumps (v5→v8), data normalisation, drift reconciliation, and ad-hoc one-off cleanups.

## Files

| File | Purpose |
|---|---|
| `migrate_to_current.py` | Top-level migration entrypoint — bumps all SQLite DBs to the current split-layout + MovieHistory v9 schema; flags for datetime normalisation and other optional steps. |
| `0042_system_state_table.sql` | SQL DDL for the `system_state` KV table in `operations.db`. |

## Subdirectories

- `tools/` — Individual migration scripts: `migrate_v5_to_v6`, `migrate_v6_to_v7_split`, `migrate_v7_to_v8`, `csv_to_sqlite`, `normalize_sqlite_datetime_columns`, `reconcile_d1_drift`, `cleanup_history_priorities`, `update_history_format`, plus a dozen other targeted one-offs.

## Depends on

- Upstream callers: `apps.cli.migration`, `.github/workflows/Migration.yml`.
- Downstream: `javdb.storage.db.*`, `javdb.storage.repos.*`, `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`.
