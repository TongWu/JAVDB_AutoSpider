# migrations

Database migration runner + tool scripts: schema bumps (v5→v8), data normalisation, drift reconciliation, and ad-hoc one-off cleanups.

## Files

| File | Purpose |
|---|---|
| `migrate_to_current.py` | Top-level migration entrypoint — bumps all SQLite DBs to the current split-layout + MovieHistory v9 schema; flags for datetime normalisation and other optional steps. |
| `0042_system_state_table.sql` | SQL DDL for the `system_state` KV table in `operations.db`. |

## Subdirectories

- `tools/` — Individual migration scripts: `migrate_v5_to_v6`, `migrate_v6_to_v7_split`, `migrate_v7_to_v8`, `csv_to_sqlite`, `normalize_sqlite_datetime_columns`, `reconcile_d1_drift`, `cleanup_history_priorities`, `update_history_format`, plus a dozen other targeted one-offs.

## Write-Class header convention (ADR-042 D6)

New D1 schema lives in `d1/*.sql`. Per [ADR-042](../../docs/design/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) D6, every migration that introduces a **new write surface** — i.e. one that contains `CREATE TABLE` — must declare its write class with a header comment:

```sql
-- Write-Class: additive
```

Allowed values are `authoritative`, `additive`, or `diagnostic` (see the `写入边界分类` section of [CONTEXT.md](../../CONTEXT.md) for definitions). **One migration file = one write class**; if you need to create tables of different classes, split them into separate migrations. Column adds, index changes, version bumps, and drops are not new write surfaces and need no tag.

This is enforced on pull requests by `.github/workflows/validate-d1-write-class.yml` (script: `scripts/ci/validate_d1_write_class.py`), which fails the build when a newly-added `CREATE TABLE` migration lacks a valid `Write-Class:` header. The check only inspects files **added** in the PR — existing migrations are grandfathered.

## Depends on

- Upstream callers: `apps.cli.migration`, `.github/workflows/Migration.yml`.
- Downstream: `javdb.storage.db.*`, `javdb.storage.repos.*`, `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`.
