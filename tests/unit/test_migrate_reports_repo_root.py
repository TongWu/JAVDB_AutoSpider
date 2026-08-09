"""Regression: migrate_reports_to_dated_dirs.main() chdirs to the repo root,
not its parent.

The script lives at javdb/migrations/tools/migrate_reports_to_dated_dirs.py
(three levels below the repo root), so main() must chdir to
``Path(__file__).resolve().parents[3]``. It used ``parents[4]`` (correct only at
the deeper pre-ADR-007 ``packages/python/...`` location), which resolved to the
repo's parent dir — every relative path the migration touches (reports/,
'Daily Report/', 'Ad Hoc/') would then resolve outside the repo.

The chdir happens inside main(), not at import, so the check drives main() in a
subprocess with the actual migration stubbed out (non-destructive) and asserts
the resulting cwd. Running from an unrelated dir proves the chdir lands on the
repo root rather than merely staying put.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_migrate_reports_main_chdirs_to_repo_root(tmp_path):
    code = (
        "import os, types\n"
        "import javdb.migrations.tools.migrate_reports_to_dated_dirs as m\n"
        # Stub out the real migration + arg parsing so main() only exercises chdir.
        "m.parse_arguments = lambda: types.SimpleNamespace(dry_run=True, force=False)\n"
        "m.run_migration = lambda **kw: True\n"
        "m.main()\n"
        "print('__CWD__=' + os.getcwd())\n"
    )
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
    marker = "__CWD__="
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(marker)]
    assert lines, f"main() did not emit cwd marker. stdout={r.stdout!r}, stderr={r.stderr!r}"
    actual = lines[-1][len(marker):].strip()
    assert actual == str(REPO_ROOT), (
        f"main() chdir'd to {actual!r}, expected repo root {str(REPO_ROOT)!r}"
    )
