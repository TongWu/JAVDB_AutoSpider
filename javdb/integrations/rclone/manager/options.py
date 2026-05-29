from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RcloneManagerOptions:
    scan: bool = False
    report: bool = False
    execute: bool = False
    execute_soft_delete: bool = False
    validate: bool = False
    root_path: str | None = None
    years: Sequence[str] | None = None
    workers: int = 4
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    output: str | None = None
    incremental: bool = False
    dry_run: bool = False
    dedup_csv: str | None = None
    soft_delete_csv: str | None = None
    soft_delete_backup_prefix: str = ""
    validate_prune: bool = True
