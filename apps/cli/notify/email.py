"""Canonical email notification CLI entrypoint.

Aliases :mod:`javdb.integrations.notify.email` so tests can patch
module-level attributes via this import path.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import importlib

_module = importlib.import_module("javdb.integrations.notify.email")
sys.modules[__name__] = _module

if __name__ == "__main__":
    _module.main()
