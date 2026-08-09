"""Import isolation checks for skip-time dedup data types."""

from __future__ import annotations

import json
import subprocess
import sys


def test_dedup_types_import_does_not_load_heavy_modules():
    code = """
import importlib
import json
import sys

importlib.import_module("javdb.spider.services.dedup_types")
bad = [
    "javdb.storage.repos.operations_repo",
    "javdb.rust_core",
    "javdb.ops.reconcile",
    "javdb.storage.db",
]
print(json.dumps({k: k in sys.modules for k in bad}, sort_keys=True))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    loaded = json.loads(result.stdout)
    assert loaded == {
        "javdb.ops.reconcile": False,
        "javdb.rust_core": False,
        "javdb.storage.db": False,
        "javdb.storage.repos.operations_repo": False,
    }
