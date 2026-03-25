"""Compatibility wrapper for the canonical rclone manager implementation."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compat import alias_module

_module = alias_module(__name__, "packages.python.javdb_integrations.rclone_manager")

if __name__ == "__main__":
    raise SystemExit(_module.main())
