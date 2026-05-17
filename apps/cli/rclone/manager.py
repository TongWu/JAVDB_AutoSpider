"""Canonical rclone manager CLI entrypoint.

Aliases :mod:`javdb.integrations.rclone.manager` so that tests can patch
module-level attributes (e.g. ``RCLONE_FOLDER_PATH``) via this import path.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compat import alias_module

_module = alias_module(__name__, "javdb.integrations.rclone.manager")

if __name__ == "__main__":
    raise SystemExit(_module.main())
