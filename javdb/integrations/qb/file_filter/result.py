from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QbFileFilterResult:
    torrents_processed: int = 0
    torrents_with_filtered_files: int = 0
    files_filtered: int = 0
    files_kept: int = 0
    size_saved: int = 0
    local_files_deleted: int = 0
    local_size_deleted: int = 0
    pending_metadata: int = 0
    errors: int = 0
    # Per-torrent rows copied verbatim from the legacy stats dict, which stores
    # tuples: (torrent_name, filtered_files_count, filtered_size, deleted_count,
    # deleted_size). No consumer reads this field today (only ``exit_code`` is
    # used); kept for parity with the legacy summary payload.
    details: list[tuple] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.errors > 0 and self.torrents_processed == 0:
            return 1
        return 0
