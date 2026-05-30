# tests/smoke/test_events_cli.py
import subprocess
import sys


def test_events_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.events", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "replay" in r.stdout.lower()
