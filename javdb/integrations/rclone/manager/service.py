from __future__ import annotations

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult


def run_manager(options: RcloneManagerOptions) -> RcloneManagerResult:
    from javdb.integrations.rclone.manager import _legacy

    exit_code = _legacy.run_manager_from_options(options)
    return RcloneManagerResult(exit_code=exit_code)
