"""PikPak bridge service package.

``pikpak_bridge(days, dry_run, ...)`` is the programmatic entry point consumed
by the REST layer (``apps/api/routers/operations.py``) and is re-exported here
with its original signature and session set/clear wrapper. ``run_bridge`` is the
CLI-facing service wrapper. Domain helpers imported by the unit tests are also
re-exported so the package import path stays stable.
"""

from javdb.integrations.pikpak.bridge.options import PikPakBridgeOptions
from javdb.integrations.pikpak.bridge.result import PikPakBridgeResult
from javdb.integrations.pikpak.bridge.service import (
    PIKPAK_ROOT_FOLDER_DEFAULT,
    _build_pikpak_target_path,
    pikpak_bridge,
    process_pikpak_batch,
    remove_completed_torrents_keep_files,
    run_bridge,
)

__all__ = [
    "PikPakBridgeOptions",
    "PikPakBridgeResult",
    "pikpak_bridge",
    "run_bridge",
    "PIKPAK_ROOT_FOLDER_DEFAULT",
    "_build_pikpak_target_path",
    "process_pikpak_batch",
    "remove_completed_torrents_keep_files",
]
