from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PikPakBridgeResult:
    total_torrents: int = 0
    filtered_old: int = 0
    successful_count: int = 0
    failed_count: int = 0
    uploaded_count: int = 0
    delete_failed_count: int = 0
    dry_run: bool = False
    errors: Sequence[str] = field(default_factory=tuple)

    @property
    def exit_code(self) -> int:
        return 0
