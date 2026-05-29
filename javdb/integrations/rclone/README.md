# rclone

Rclone integration: shared scan/parse/health-check primitives plus the unified scan-report-execute CLI manager.

## Files

| File | Purpose |
|---|---|
| `helper.py` | Shared rclone data models, parsing logic, health-check routines, and dedup analysis used by the manager CLI. |

## Subdirectories

| Subdirectory | Purpose |
|---|---|
| `manager/` | Unified rclone manager command-service package (ADR-015 Phase 4). `options.py`/`result.py` hold the `RcloneManagerOptions`/`RcloneManagerResult` contract; `service.py` exposes `run_manager`; `_legacy.py` is the bake implementation (scan/report/execute/execute-soft-delete/validate orchestration) plus the legacy `parse_arguments`/`main` parser, re-exported during the Phase 4 bake window until IMP-ADR015-05 removes it. CLI parsing + exit-code mapping now live in `apps.cli.rclone.manager`. Scan persistence routes through `OperationsRepo` + `SessionLifecycleRepo` (Issue #79). |

## Depends on

- Upstream callers: `apps.cli.rclone_manager`, weekly dedup workflow.
- Downstream: `javdb.storage` (RcloneInventory, DedupRecords), `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.paths`.
