"""Regression: the canonical CLI modules under javdb/infra/ resolve REPO_ROOT to
the actual repo root and chdir there on import — not to the repo's PARENT.

javdb/infra/health_check.py and javdb/infra/fetch_page.py live two levels below
the repo root, so the root is ``Path(__file__).resolve().parents[2]``. They used
``parents[3]`` (an off-by-one left over from an earlier, deeper location), which
resolved to the repo's parent dir and made ``os.chdir`` jump out of the tree on
import — silently breaking relative-path I/O and leaking cwd into any sibling
test that imported them in-process.

The check runs in a subprocess started from an unrelated directory so it proves
the chdir actually lands on the repo root (and never pollutes this test's cwd).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module", ["javdb.infra.health_check", "javdb.infra.fetch_page"])
def test_canonical_cli_chdirs_to_repo_root(module, tmp_path):
    code = f"import os, {module}; print('__CWD__=' + os.getcwd())"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    # Parse a prefixed marker rather than the whole stdout: an import-time log
    # line (e.g. the "Rust core unavailable" warning) would otherwise break an
    # exact-equality assertion.
    marker = "__CWD__="
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(marker)]
    assert lines, f"{module} did not emit cwd marker. stdout={r.stdout!r}, stderr={r.stderr!r}"
    actual = lines[-1][len(marker):].strip()
    assert actual == str(REPO_ROOT), (
        f"{module} chdir'd to {actual!r}, expected repo root {str(REPO_ROOT)!r}"
    )
