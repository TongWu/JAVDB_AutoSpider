# rclone

Rclone integration: shared scan/parse/health-check primitives plus the unified scan-report-execute CLI manager.

## Files

| File | Purpose |
|---|---|
| `helper.py` | Shared rclone data models, parsing logic, health-check routines, and dedup analysis used by the manager service. Still a large internal helper awaiting a follow-up ADR; not deep-split in ADR-015 Phase 5. |

## Subdirectories

| Subdirectory | Purpose |
|---|---|
| `manager/` | Unified rclone manager command-service package (ADR-015). `options.py`/`result.py` hold the `RcloneManagerOptions`/`RcloneManagerResult` contract; `service.py` owns all scan/report/execute/execute-soft-delete/validate orchestration and exposes `run_manager` (plus `run_rclone_manager`/`run_execute_inventory_purge_from_csv` for programmatic callers). CLI parsing + exit-code mapping live in `apps.cli.rclone.manager`. Scan persistence routes through `OperationsRepo` + `SessionLifecycleRepo` (Issue #79). |

## Depends on

- Upstream callers: `apps.cli.rclone_manager`, weekly dedup workflow.
- Downstream: `javdb.storage` (RcloneInventory, DedupRecords), `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`.
