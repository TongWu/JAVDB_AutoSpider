from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class QbFileFilterOptions:
    min_size_mb: float
    days: int = 2
    proxy_override: bool | None = None
    dry_run: bool = False
    category: str | None = None
    categories: Sequence[str] | None = None
    delete_local_files: bool = False
