"""Plugin registry keyed by (category, name) — built-in now, entry-point-ready."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[tuple[str, str], object] = {}

    def register(self, category: str, plugin) -> None:
        key = (category, plugin.name)
        if key in self._plugins:
            logger.debug("plugin already registered: %s", key)
        self._plugins[key] = plugin

    def get(self, category: str, name: str):
        return self._plugins.get((category, name))

    def list(self, category: str) -> list:
        return [p for (cat, _name), p in self._plugins.items() if cat == category]

    def discover_entry_points(self, group: str) -> int:
        """Phase-2 seam: discover third-party plugins via importlib.metadata.
        Phase 1 is a deliberate no-op (reserved interface)."""
        return 0


# Process-global registry; built-in plugins register into this at import.
REGISTRY = PluginRegistry()
