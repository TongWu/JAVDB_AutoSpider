"""Canonical runtime-config accessor for core (``javdb.*``) code (issue #228).

Core modules — e.g. the indexer source plugins — need the merged runtime config
(``PROXY_POOL`` and friends) but must not import the application layer
(``apps.*``). Doing so inverts the intended dependency direction (core ← app)
and couples the library to the API's config-override store, which is an
application concern.

Instead the application registers a provider once at startup
(``register_provider``); core code reads through ``get_runtime_config``.

The accessor **fails closed**: if no provider is registered it raises rather
than returning an empty config, so a caller that depends on proxy settings can
never silently fall back to a proxy-less direct request (the privacy regression
fixed in issue #226).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

RuntimeConfigProvider = Callable[[], Dict[str, Any]]

# Process-wide provider, registered by the application layer (see
# apps.api.services.config_service). Module-private so callers go through the
# accessor functions below.
_provider: Optional[RuntimeConfigProvider] = None


def register_provider(provider: RuntimeConfigProvider) -> None:
    """Register the process-wide runtime-config loader.

    Called by the application layer (which owns config.py + the override store).
    The last registration wins; registering is idempotent for the same loader.
    """
    global _provider
    _provider = provider


def get_runtime_config() -> Dict[str, Any]:
    """Return the merged runtime config (config.py + override store + defaults).

    Fails closed: raises ``RuntimeError`` when no provider has been registered,
    so proxy-dependent callers never degrade to a direct (de-proxied) request.
    """
    provider = _provider
    if provider is None:
        raise RuntimeError(
            "runtime config provider is not registered; the application layer "
            "must call javdb.infra.runtime_config.register_provider() before "
            "core code reads runtime config"
        )
    return provider()


__all__ = ["RuntimeConfigProvider", "get_runtime_config", "register_provider"]
