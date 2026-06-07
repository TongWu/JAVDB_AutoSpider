import sqlite3
import subprocess
import sys

import pytest

from apps.cli.ops import content_filter


_DDL = """
CREATE TABLE ContentFilterRule (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension  TEXT NOT NULL,
    mode       TEXT NOT NULL,
    value      TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT ''
);
"""


@pytest.fixture
def cli_conn(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    monkeypatch.setattr(content_filter, "get_db", lambda _path: conn)
    monkeypatch.setattr(content_filter, "setup_logging", lambda **_kwargs: None)
    yield conn
    conn.close()


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


def test_content_filter_cli_add_list_enable_remove_roundtrip(cli_conn, capsys):
    assert content_filter.main([
        "add",
        "--dimension",
        "actor",
        "--mode",
        "exclude",
        "--value",
        " /actors/abc ",
    ]) == 0
    assert "Added content filter rule 1" in capsys.readouterr().out

    assert content_filter.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "/actors/abc" in listed
    assert "yes" in listed

    assert content_filter.main(["enable", "--id", "1", "--off"]) == 0
    assert content_filter.main(["list"]) == 0
    assert "no" in capsys.readouterr().out

    assert content_filter.main(["enable", "--id", "1"]) == 0
    assert content_filter.main(["remove", "--id", "1"]) == 0
    assert content_filter.main(["list"]) == 0
    assert "No content filter rules." in capsys.readouterr().out


def test_content_filter_cli_allows_all_male_rule_without_value(cli_conn, capsys):
    assert content_filter.main([
        "add",
        "--dimension",
        "gender",
        "--mode",
        "exclude_all_male",
    ]) == 0

    assert content_filter.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "gender\texclude_all_male" in listed


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["add", "--dimension", "actor", "--mode", "include", "--value", "A"],
            "do not support",
        ),
        (
            ["add", "--dimension", "tag", "--mode", "require_lead", "--value", "subtitle"],
            "do not support",
        ),
        (
            ["add", "--dimension", "actor", "--mode", "exclude"],
            "require --value",
        ),
        (
            ["add", "--dimension", "tag", "--mode", "include", "--value", " "],
            "require --value",
        ),
        (
            ["add", "--dimension", "gender", "--mode", "require_lead"],
            "require --value",
        ),
        (
            ["add", "--dimension", "gender", "--mode", "require_lead", "--value", "unknown"],
            "female",
        ),
        (
            ["add", "--dimension", "gender", "--mode", "exclude_all_male", "--value", "male"],
            "do not accept --value",
        ),
    ],
)
def test_content_filter_cli_rejects_invalid_rule_shapes(cli_conn, capsys, argv, message):
    with pytest.raises(SystemExit) as exc_info:
        content_filter.main(argv)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_content_filter_cli_reports_missing_ids(cli_conn, capsys):
    assert content_filter.main(["remove", "--id", "99"]) == 1
    assert "not found" in capsys.readouterr().err

    assert content_filter.main(["enable", "--id", "99"]) == 1
    assert "not found" in capsys.readouterr().err


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


def test_add_age_rule(monkeypatch):
    import contextlib

    import apps.cli.ops.content_filter as cli

    captured = {}

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def add_rule(self, dimension, mode, value):
            captured.update(dimension=dimension, mode=mode, value=value)
            return 7

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ContentFilterRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["add", "--dimension", "age", "--mode", "min_age", "--value", "18"])
    assert rc == 0
    assert captured == {"dimension": "age", "mode": "min_age", "value": "18"}


def test_add_age_rule_normalizes_leading_zeros(monkeypatch):
    import contextlib

    import apps.cli.ops.content_filter as cli

    captured = {}

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def add_rule(self, dimension, mode, value):
            captured.update(dimension=dimension, mode=mode, value=value)
            return 8

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ContentFilterRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["add", "--dimension", "age", "--mode", "min_age", "--value", "0018"])
    assert rc == 0
    assert captured["value"] == "18"


def test_add_age_rule_max_age(monkeypatch):
    import contextlib

    import apps.cli.ops.content_filter as cli

    captured = {}

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def add_rule(self, dimension, mode, value):
            captured.update(dimension=dimension, mode=mode, value=value)
            return 9

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ContentFilterRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["add", "--dimension", "age", "--mode", "max_age", "--value", "40"])
    assert rc == 0
    assert captured == {"dimension": "age", "mode": "max_age", "value": "40"}


def test_add_age_rule_rejects_non_numeric(monkeypatch):
    import contextlib

    import apps.cli.ops.content_filter as cli

    add_calls = []

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def add_rule(self, dimension, mode, value):
            add_calls.append((dimension, mode, value))
            return 10

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ContentFilterRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    with pytest.raises(SystemExit):
        cli.main(["add", "--dimension", "age", "--mode", "min_age", "--value", "abc"])
    assert add_calls == []
