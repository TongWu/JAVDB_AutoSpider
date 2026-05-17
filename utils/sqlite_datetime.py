"""Compatibility wrapper for canonical SQLite datetime helpers."""

from __future__ import annotations

from compat import alias_module

alias_module(__name__, "javdb.storage.sqlite_datetime")
