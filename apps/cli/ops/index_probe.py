"""Canonical index-probe CLI entrypoint.

Thin adapter that delegates to :mod:`javdb.ops.index_probe`, matching the
``apps.cli.ops.fetch_page`` pattern so ``python -m apps.cli.ops.index_probe``
and ``import apps.cli.ops.index_probe`` both behave conventionally.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.ops.index_probe import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
