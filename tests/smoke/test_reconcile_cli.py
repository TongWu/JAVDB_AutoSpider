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


def test_reconcile_cli_help_shows_pass_selector():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "--pass" in r.stdout
    assert "ownership" in r.stdout


def test_reconcile_cli_pass_ownership_dry_run_runs():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile",
         "--pass", "ownership", "--dry-run", "--json", "--log-level", "WARNING"],
        capture_output=True, text=True,
    )
    # dry-run writes nothing; either clean exit or a captured error, never a traceback.
    assert r.returncode in (0, 2)
    assert "Traceback" not in r.stderr
