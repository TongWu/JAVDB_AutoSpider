"""Compatibility wrapper for the canonical pipeline CLI implementation."""

from __future__ import annotations

from compat import alias_module

_module = alias_module(__name__, "apps.cli.pipeline")

if __name__ == "__main__":
    raise SystemExit(_module.main())
