"""Fan-out notify dispatch with failure isolation (ADR-039 D4).

Importing this module registers the built-in plugins (email, telegram)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.notify.email.plugin  # noqa: F401,E402
import javdb.integrations.notify.telegram.plugin  # noqa: F401,E402

# Discover any third-party notify plugins installed as entry points.
# Returns 0 when no packages with 'javdb.notify_plugins' entry points are installed.
REGISTRY.discover_entry_points("javdb.notify_plugins")


def active_names() -> list[str]:
    val = cfg("NOTIFY_BACKENDS", ["email"])
    if isinstance(val, str):
        names = [v.strip() for v in val.split(",") if v.strip()]
    elif isinstance(val, (list, tuple)):
        # Keep only non-empty strings: a stray non-string / unhashable element
        # (e.g. a typo'd ['email', {}]) would otherwise reach send()'s
        # `name in excluded` / REGISTRY lookup and raise TypeError, breaking the
        # fan-out — notify is the channel that reports failures, so it must
        # never raise on a config typo.
        names = [v.strip() for v in val if isinstance(v, str) and v.strip()]
    else:
        # A non-iterable / unexpected config value (e.g. an int) must degrade to
        # the default rather than crash dispatch — same fail-safe rationale.
        names = []
    return names or ["email"]


def send(message: NotifyMessage, exclude: set[str] | None = None) -> list[NotifyResult]:
    # ``exclude`` (ADR-039 D4) lets a caller skip backends it has already handled
    # by another route — e.g. the pipeline CLI delivers the rich HTML report to
    # ``email`` directly, then fans the plaintext summary out to the *other*
    # active backends via ``send(message, exclude={'email'})`` so email is never
    # double-notified.
    excluded = exclude or set()
    results: list[NotifyResult] = []
    for name in active_names():
        if name in excluded:
            continue
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
