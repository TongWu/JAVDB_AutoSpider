"""Compatibility helpers for bridging legacy module paths to canonical ones."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import MutableSequence


REPO_ROOT = Path(__file__).resolve().parent


def repo_path(*parts: str) -> str:
    """Return an absolute path under the repository root."""

    return str(REPO_ROOT.joinpath(*parts))


def activate_repo_root() -> str:
    """Ensure the repository root is the working directory and import base."""

    root = str(REPO_ROOT)
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def extend_package_path(path_list: MutableSequence[str], *target_parts: str) -> None:
    """Append a canonical implementation directory to a legacy package path."""

    target = repo_path(*target_parts)
    if target not in path_list:
        path_list.append(target)


def alias_module(legacy_name: str, canonical_name: str):
    """Load *canonical_name* and register it as *legacy_name*."""

    module = importlib.import_module(canonical_name)
    sys.modules[legacy_name] = module
    return module
