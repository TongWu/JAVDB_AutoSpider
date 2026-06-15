"""The indexer-category plugin contract (ADR-039 / ADR-054 WS3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass
class IndexerMagnet:
    """A single magnet row from an external indexer."""

    magnet_uri: str
    name: str
    source: str
    info_hash: Optional[str] = None
    size: str = ""
    tags: List[str] = field(default_factory=list)
    file_count: int = 0


@dataclass
class IndexerResult:
    """The outcome of querying one source."""

    source: str
    ok: bool
    magnets: List[IndexerMagnet] = field(default_factory=list)
    detail: Optional[str] = None


class IndexerPlugin(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    def search(self, video_code: str) -> IndexerResult: ...
