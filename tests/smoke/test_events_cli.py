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


def test_events_cli_rejects_non_positive_batch():
    # A 0/negative page size would spin run_once forever (reads 0 rows, never
    # satisfies n < batch). argparse must reject it before the loop runs.
    for bad in ("0", "-5"):
        r = subprocess.run(
            [sys.executable, "-m", "apps.cli.ops.events", "--batch", bad],
            capture_output=True, text=True,
        )
        assert r.returncode != 0, bad
        assert "must be >= 1" in r.stderr.lower()
