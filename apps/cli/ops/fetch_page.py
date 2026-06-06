"""Canonical fetch-page CLI entrypoint.

Thin adapter that delegates to :mod:`javdb.infra.fetch_page`. Importing this
module yields a real module exposing ``main`` (not a ``sys.modules`` self-alias),
so ``python -m apps.cli.ops.fetch_page`` and any ``import apps.cli.ops.fetch_page``
behave conventionally.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.fetch_page import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
