# tests/smoke/test_sentinel_cli.py
import subprocess
import sys


def test_sentinel_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.sentinel", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "drift" in r.stdout.lower()
