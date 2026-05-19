# rclone

Rclone integration: shared scan/parse/health-check primitives plus the unified scan-report-execute CLI manager.

## Files

| File | Purpose |
|---|---|
| `helper.py` | Shared rclone data models, parsing logic, health-check routines, and dedup analysis used by the manager CLI. |
| `manager.py` | Unified rclone manager: composable `--scan`, `--report`, `--execute` flags to scan remote trees, write DB/CSV, and act on results. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.rclone_manager`, weekly dedup workflow.
- Downstream: `javdb.storage` (RcloneInventory, DedupRecords), `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`.
