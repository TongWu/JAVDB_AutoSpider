"""Single-select downloader dispatch with failure isolation (ADR-039 Phase 2).

Importing this module registers the built-in plugins (qb, transmission)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.downloader.plugin import DownloadResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.downloader.qb.plugin  # noqa: F401,E402
import javdb.integrations.downloader.transmission.plugin  # noqa: F401,E402

# Discover any third-party downloader plugins installed as entry points.
REGISTRY.discover_entry_points("javdb.downloader_plugins")


def active_downloader_name() -> str:
    """Return the active downloader backend name from DOWNLOADER_BACKEND config.

    Defaults to 'qb' so existing deployments are unchanged.
    """
    return str(cfg("DOWNLOADER_BACKEND", "qb")).strip() or "qb"


def add(
    magnet: str,
    category: str,
    name: str | None = None,
) -> DownloadResult:
    """Route add_torrent to the active downloader backend with failure isolation.

    Unlike the notify fan-out, downloader is single-select: only the backend
    named by DOWNLOADER_BACKEND receives the call.
    """
    backend_name = active_downloader_name()
    plugin = REGISTRY.get("downloader", backend_name)
    if plugin is None:
        return DownloadResult(
            plugin=backend_name, ok=False, detail=f"not registered: {backend_name!r}"
        )
    try:
        if not plugin.is_configured():
            return DownloadResult(plugin=backend_name, ok=False, detail="not configured")
        return plugin.add_torrent(magnet, category, name=name)
    except Exception as exc:  # failure isolation
        return DownloadResult(plugin=backend_name, ok=False, detail=f"error: {exc}")
