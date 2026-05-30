import subprocess
import sys


def test_content_filter_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.content_filter", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "rule" in r.stdout.lower()
    assert "add" in r.stdout
    assert "list" in r.stdout
    assert "remove" in r.stdout
    assert "enable" in r.stdout


def test_content_filter_cli_rejects_invalid_dimension():
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "apps.cli.ops.content_filter",
            "add",
            "--dimension",
            "studio",
            "--mode",
            "exclude",
            "--value",
            "Example",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "invalid choice" in r.stderr.lower()


def test_content_filter_cli_rejects_invalid_mode():
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "apps.cli.ops.content_filter",
            "add",
            "--dimension",
            "tag",
            "--mode",
            "block",
            "--value",
            "Example",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "invalid choice" in r.stderr.lower()
