# rollback

Rollback library: plans and applies session-scoped rollback across history/reports/operations DBs in either pending-delete or audit-restore mode.

## Files

| File | Purpose |
|---|---|
| `core.py` | `plan_rollback` / `apply_rollback` with `RollbackRequest` / `RollbackPlan` / `RollbackResult` dataclasses; mirrors CLI flags 1:1 plus HTTP-friendly aliases. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.rollback`, `apps.api.routers.sessions` (rollback endpoint).
- Downstream: `javdb.storage.db.db_rollback`, `javdb.storage.db.db_reports`, `javdb.storage.db.db_session`, `javdb.infra.logging`.
