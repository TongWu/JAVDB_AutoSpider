# tests/smoke/test_sentinel_cli_canary.py
import subprocess
import sys


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.sentinel", *args],
        capture_output=True, text=True,
    )


def test_help_mentions_canary():
    r = _run("--help")
    assert r.returncode == 0
    assert "--canary" in r.stdout
    assert "--capture-anchors" in r.stdout


def test_no_mode_and_no_session_errors():
    # Neither --canary nor --session-id -> usage error, exit 2.
    r = _run()
    assert r.returncode == 2
    assert "session-id" in (r.stdout + r.stderr).lower()
