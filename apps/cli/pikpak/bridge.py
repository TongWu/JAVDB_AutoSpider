"""Canonical PikPak bridge CLI entrypoint.

Aliases :mod:`javdb.integrations.pikpak.bridge` so tests can patch
module-level attributes via this import path.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compat import alias_module

_module = alias_module(__name__, "javdb.integrations.pikpak.bridge")

if __name__ == "__main__":
    _module.main()
