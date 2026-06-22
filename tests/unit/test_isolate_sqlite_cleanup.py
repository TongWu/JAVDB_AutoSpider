"""Regression: the autouse ``_isolate_sqlite`` fixture must not leak per-test
SQLite files.

The fixture creates a fresh schema'd ``test.db`` (plus ``-wal``/``-shm``,
~0.6-1.7 MB total) under each test's ``tmp_path``. Without teardown cleanup
those files accumulate for the whole pytest session (5k+ tests => several GB)
and have filled self-hosted runners whose ``/tmp`` is a ~5.9 GB tmpfs, failing
the tail of the suite with ``sqlite3.OperationalError: database or disk is
full``. This spawns a real sub-pytest and asserts nothing is left behind.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_isolate_sqlite_leaves_no_db_files(tmp_path):
    """A sub-pytest run must leave zero ``test.db*`` files in its basetemp."""
    basetemp = tmp_path / "bt"
    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "tests/unit/test_workflow_stats_sink.py",
                "-q", "-p", "no:cacheprovider",
                "-o", "addopts=",
                f"--basetemp={basetemp}",
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"inner pytest did not finish within {exc.timeout}s")
    assert proc.returncode == 0, f"inner pytest failed:\n{proc.stdout}\n{proc.stderr}"

    leftovers = [str(p.relative_to(basetemp)) for p in basetemp.rglob("test.db*")]
    assert leftovers == [], (
        "per-test SQLite files leaked into tmpfs (would fill the runner's /tmp): "
        f"{leftovers}"
    )
