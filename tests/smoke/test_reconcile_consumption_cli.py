import subprocess
import sys


def test_reconcile_pass_consumption_dry_run_is_sane():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile",
         "--pass", "consumption", "--dry-run", "--json", "--log-level", "WARNING"],
        capture_output=True, text=True,
    )
    # No MEDIA_SERVERS configured in CI → empty pass, exit 0 (or 2 if an
    # instance error was captured). A traceback / non-{0,2} exit is a failure.
    assert r.returncode in (0, 2), r.stderr


def test_reconcile_help_mentions_consumption():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "consumption" in r.stdout.lower()
