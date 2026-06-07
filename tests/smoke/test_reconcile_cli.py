import subprocess
import sys


def test_reconcile_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile", "--help"],
        capture_output=True, text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "reconcile" in r.stdout.lower()
