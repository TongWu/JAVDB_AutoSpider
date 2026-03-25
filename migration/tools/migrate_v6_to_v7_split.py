"""Compatibility wrapper for the canonical v6-to-v7-split migration tool."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compat import alias_module

_module = alias_module(
    __name__,
    "packages.python.javdb_migrations.tools.migrate_v6_to_v7_split",
)

if __name__ == "__main__":
    raise SystemExit(_module.main())
