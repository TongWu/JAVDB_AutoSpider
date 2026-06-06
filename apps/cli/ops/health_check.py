"""Canonical health check CLI entrypoint.

Thin adapter that delegates to :mod:`javdb.infra.health_check`. Importing this
module yields a real module exposing ``main`` (not a ``sys.modules`` self-alias),
so ``python -m apps.cli.ops.health_check`` and any ``import apps.cli.ops.health_check``
behave conventionally.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.health_check import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
