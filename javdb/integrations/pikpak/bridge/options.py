from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PikPakBridgeOptions:
    days: int = 3
    dry_run: bool = False
    batch_mode: bool = True
    proxy_override: bool | None = None
    from_pipeline: bool = False
    session_id: str | None = None
    root_folder: str | None = None
