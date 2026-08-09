# rclone

Rclone integration: shared scan/parse/health-check primitives plus the unified scan-report-execute CLI manager.

## Files

| File | Purpose |
|---|---|
| `types.py` | Shared rclone data classes, category constants, and scan/dedup tuning constants. |
| `path_utils.py` | Pure rclone drive/root/path normalization helpers. |
| `scan.py` | Rclone health checks, folder parsing, folder cache, and scan engine. |
| `dedup.py` | Cleanup-time folder dedup cascade, deletion execution, CSV report, and summary helpers. |

## Subdirectories

| Subdirectory | Purpose |
|---|---|
| `manager/` | Unified rclone manager command-service package (ADR-015):<br>• **Contract** — `options.py` / `result.py` (`RcloneManagerOptions` / `RcloneManagerResult`).<br>• **Orchestration** — `service.py` owns scan / report / execute / execute-soft-delete / validate and exposes `run_manager` (plus `run_rclone_manager` and `run_execute_inventory_purge_from_csv` for programmatic callers).<br>• **CLI boundary** — parsing + exit-code mapping in `apps.cli.rclone.manager`.<br>• **Persistence** — scan routes through `OperationsRepo` + `SessionLifecycleRepo` (Issue #79). |

## Depends on

- Upstream callers: `apps.cli.rclone.manager`, weekly dedup workflow.
- Downstream: `javdb.storage` (RcloneInventory, DedupRecords), `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`.
