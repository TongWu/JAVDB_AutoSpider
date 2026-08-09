"""Media-server adapters for the ADR-033 consumption signal.

Each adapter is a read-only integration (ADR-015 seam): typed MediaServerConfig
in, list[MediaItem] out, no DB writes. The reconcile service orchestrates them
and is the only writer."""

from __future__ import annotations

from typing import Optional, Protocol

from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import MediaItem


class MediaServerAdapter(Protocol):
    config: MediaServerConfig

    def list_items(self, since: Optional[str]) -> list[MediaItem]: ...


def build_adapter(config: MediaServerConfig) -> MediaServerAdapter:
    """Instantiate the adapter for a config's source_type."""
    if config.source_type == "emby":
        from javdb.integrations.media_servers.emby.adapter import EmbyAdapter
        return EmbyAdapter(config)
    if config.source_type == "plex":
        from javdb.integrations.media_servers.plex.adapter import PlexAdapter
        return PlexAdapter(config)
    raise ValueError(f"no adapter for source_type: {config.source_type!r}")
