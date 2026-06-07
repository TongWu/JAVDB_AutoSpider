from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QbUploaderResult:
    total_torrents: int = 0
    duplicate_count: int = 0
    attempted: int = 0
    successfully_added: int = 0
    failed_count: int = 0
    hacked_subtitle_count: int = 0
    hacked_no_subtitle_count: int = 0
    subtitle_count: int = 0
    no_subtitle_count: int = 0
    csv_path: str | None = None
    csv_ok: bool = True
    error_reason: str | None = None

    @property
    def exit_code(self) -> int:
        if self.error_reason:
            return 1
        if not self.csv_ok:
            return 1
        if self.attempted > 0 and self.successfully_added == 0:
            return 1
        return 0
