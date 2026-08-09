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
        """Discover and register third-party plugins via importlib.metadata.

        Scans the named entry-point group (e.g. 'javdb.notify_plugins'),
        derives the category by stripping the prefix and '_plugins' suffix
        (e.g. 'notify'), loads each entry point, and registers the result.

        - If the loaded value is a class (callable), it is instantiated.
        - If the loaded value is already an instance with a .name attr, it is
          registered directly.
        - Failures per entry point are caught and logged at WARNING; they never
          raise and do not affect other entry points' count.
        - A top-level failure from importlib.metadata itself returns 0 silently.

        Returns the count of successfully registered plugins.
        """
        import importlib.metadata

        # Derive category: 'javdb.notify_plugins' → 'notify'
        # Use rsplit + removesuffix (NOT rstrip — rstrip is char-based and fragile).
        category = group.rsplit(".", 1)[-1].removesuffix("_plugins")

        try:
            # entry_points(group=...) is supported on Python 3.9.5+ and is the
            # canonical form in 3.12+ (avoids the dict/.get() API removed in 3.12).
            eps = importlib.metadata.entry_points(group=group)
        except Exception as exc:
            logger.warning("discover_entry_points: metadata query failed: %s", exc)
            return 0

        count = 0
        for ep in eps:
            try:
                loaded = ep.load()
                # Instantiate if it's a class; use as-is if already an instance.
                if isinstance(loaded, type):
                    plugin = loaded()
                else:
                    plugin = loaded
                self.register(category, plugin)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "discover_entry_points: skipping entry point %r in group %r: %s",
                    getattr(ep, "name", ep),
                    group,
                    exc,
                )
        return count


# Process-global registry; built-in plugins register into this at import.
REGISTRY = PluginRegistry()
