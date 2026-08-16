# tests/smoke/test_actor_age_cli.py
import subprocess
import sys


def test_actor_age_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.actor_age", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    out = r.stdout.lower()
    assert "list" in out and "refresh" in out


def test_clear_normalizes_href(monkeypatch):
    import contextlib

    import apps.cli.ops.actor_age as cli

    deleted = {}

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def delete(self, href):
            deleted["href"] = href

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ActorMetadataRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["clear", "--href", "https://javdb.com/actors/EvkJ"])
    assert rc == 0
    assert deleted["href"] == "/actors/EvkJ"  # normalized, not the raw URL


def test_list_empty(monkeypatch, capsys):
    import contextlib

    import apps.cli.ops.actor_age as cli

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def list_all(self):
            return []

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ActorMetadataRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["list"])
    assert rc == 0
    assert "No cached" in capsys.readouterr().out


def test_list_with_rows(monkeypatch, capsys):
    import contextlib

    import apps.cli.ops.actor_age as cli

    # Note: refresh calls build_default_resolver() which hits the network.
    # That path is already covered by unit tests for the resolver, so we
    # do NOT add a refresh test here.

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def list_all(self):
            return [
                {
                    "actor_href": "/actors/h",
                    "actor_name": "N",
                    "birthdate": "1990-05-20",
                    "source": "minnano-av",
                    "source_url": "https://m/h",
                    "resolved": 1,
                }
            ]

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ActorMetadataRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "/actors/h" in out
    assert "minnano-av" in out
    # A full date of birth is personal data and this listing lands in CI logs:
    # only the year survives, which is what the cache is debugged against.
    assert "1990-05-20" not in out
    assert "1990-**-**" in out
