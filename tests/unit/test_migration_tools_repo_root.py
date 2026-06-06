"""Regression: migration tools under javdb/migrations/tools/ resolve REPO_ROOT to
the actual repo root, not its parent.

These tools live three levels below the repo root, so REPO_ROOT must be
``Path(__file__).resolve().parents[3]``. A batch of them used ``parents[4]``
(correct only at the deeper pre-ADR-007 ``packages/python/...`` location), which
resolved to the repo's parent dir. For the ones that ``os.chdir(REPO_ROOT)`` at
import time that leaked cwd into any in-process importer; for all of them it
pointed sys.path / relative data paths outside the repo tree.

The check imports each module in a subprocess started from an unrelated
directory (so an import-time chdir can never pollute the test runner) and
asserts the module's resolved REPO_ROOT equals the repo root — which, for the
chdir tools, is exactly the directory they chdir into.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every migration tool that defines a module-level REPO_ROOT used for sys.path,
# os.chdir, or relative data paths.
MIGRATION_TOOLS = (
    "align_inventory_with_moviehistory",
    "backfill_torrent_size",
    "cleanup_history_priorities",
    "csv_to_sqlite",
    "migrate_rclone_paths_to_code_dir",
    "migrate_v5_to_v6",
    "migrate_v7_to_v8",
    "normalize_sqlite_datetime_columns",
    "reclassify_c_hacked_torrents",
    "reconcile_d1_drift",
    "rename_columns_add_last_visited",
    "restore_moviehistory_supporting_actors_from_csv",
    "strip_rclone_root_folder",
    "update_history_format",
)


@pytest.mark.parametrize("tool", MIGRATION_TOOLS)
def test_migration_tool_repo_root_resolves_to_repo(tool, tmp_path):
    module = f"javdb.migrations.tools.{tool}"
    code = f"import {module} as m; print('__REPO_ROOT__=' + str(m.REPO_ROOT))"
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
    # line would otherwise break an exact-equality assertion.
    marker = "__REPO_ROOT__="
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(marker)]
    assert lines, f"{module} did not emit REPO_ROOT marker. stdout={r.stdout!r}, stderr={r.stderr!r}"
    actual = lines[-1][len(marker):].strip()
    assert actual == str(REPO_ROOT), (
        f"{module}.REPO_ROOT = {actual!r}, expected repo root {str(REPO_ROOT)!r}"
    )
