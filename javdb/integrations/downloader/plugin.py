"""The downloader-category plugin contract (ADR-039 Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class DownloadResult:
    plugin: str
    ok: bool
    detail: Optional[str] = None


class DownloaderPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def add_torrent(
        self,
        magnet: str,
        category: str,
        name: Optional[str] = None,
    ) -> DownloadResult: ...
