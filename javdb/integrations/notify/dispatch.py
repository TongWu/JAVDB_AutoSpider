"""Fan-out notify dispatch with failure isolation (ADR-039 D4).

Importing this module registers the built-in plugins (email, telegram)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.notify.email.plugin  # noqa: F401,E402
import javdb.integrations.notify.telegram.plugin  # noqa: F401,E402


def active_names() -> list[str]:
    val = cfg("NOTIFY_BACKENDS", ["email"])
    if isinstance(val, str):
        names = [v.strip() for v in val.split(",") if v.strip()]
    elif isinstance(val, (list, tuple)):
        names = list(val) if val else []
    else:
        # A non-iterable / unexpected config value (e.g. an int) must degrade to
        # the default rather than crash dispatch — notify is itself the channel
        # that reports failures, so it must never raise on a config typo.
        names = []
    return names or ["email"]


def send(message: NotifyMessage) -> list[NotifyResult]:
    results: list[NotifyResult] = []
    for name in active_names():
        plugin = REGISTRY.get("notify", name)
        if plugin is None:
            results.append(NotifyResult(plugin=name, ok=False, detail="not registered"))
            continue
        try:
            if not plugin.is_configured():
                results.append(NotifyResult(plugin=name, ok=False, detail="not configured"))
                continue
            results.append(plugin.send(message))
        except Exception as exc:  # failure isolation (incl. is_configured errors)
            results.append(NotifyResult(plugin=name, ok=False, detail=f"error: {exc}"))
    return results
