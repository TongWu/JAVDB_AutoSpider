"""Thin ASGI/bootstrap alias for the canonical API runtime module."""

from __future__ import annotations

from compat import alias_module

alias_module(__name__, "apps.api.services.runtime")
