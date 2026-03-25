"""Compatibility wrapper for the canonical packages.python.javdb_core.magnet_extractor module."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compat import alias_module

alias_module(__name__, "packages.python.javdb_core.magnet_extractor")
