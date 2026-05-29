"""Rclone manager service package.

The selected legacy exports remain during ADR-015 Phase 4 and are removed by
IMP-ADR015-05 after the bake window.
"""

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult
from javdb.integrations.rclone.manager.service import run_manager
from javdb.integrations.rclone.manager._legacy import (
    export_db_to_csv,
    export_dedup_history,
    list_remote_truth_paths,
    load_inventory_as_folder_structure,
    migrate_strip_drive_names,
    resolve_latest_dedup_file,
    resolve_rclone_root,
    run_execute_from_csv,
    run_execute_inventory_purge_from_csv,
    run_execute_soft_delete_from_csv,
    run_rclone_manager,
    run_report_from_inventory,
    run_validate_inventory,
    scan_inventory,
    validate_dedup_records_against_inventory,
)

__all__ = [
    "RcloneManagerOptions",
    "RcloneManagerResult",
    "run_manager",
    "export_db_to_csv",
    "export_dedup_history",
    "list_remote_truth_paths",
    "load_inventory_as_folder_structure",
    "migrate_strip_drive_names",
    "resolve_latest_dedup_file",
    "resolve_rclone_root",
    "run_execute_from_csv",
    "run_execute_inventory_purge_from_csv",
    "run_execute_soft_delete_from_csv",
    "run_rclone_manager",
    "run_report_from_inventory",
    "run_validate_inventory",
    "scan_inventory",
    "validate_dedup_records_against_inventory",
]
