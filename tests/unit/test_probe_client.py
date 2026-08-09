"""ADR-024 IMP-10: remote quality_probe client factory."""

from __future__ import annotations

from javdb.quality import probe_client as pc


def test_returns_none_when_url_unconfigured(monkeypatch):
    monkeypatch.setattr(pc, "cfg", lambda k, d=None: "" if k == "QUALITY_PROBE_QB_URL" else d)
    assert pc.build_probe_client() is None


def test_builds_client_with_probe_credentials(monkeypatch):
    values = {
        "QUALITY_PROBE_QB_URL": "https://probe:8080",
        "QUALITY_PROBE_QB_USERNAME": "u",
        "QUALITY_PROBE_QB_PASSWORD": "p",
    }
    monkeypatch.setattr(pc, "cfg", lambda k, d=None: values.get(k, d))

    captured = {}

    def _fake_client(base_urls, username, password, **kw):
        captured.update(base_urls=base_urls, username=username, password=password)
        return object()

    monkeypatch.setattr(pc, "QBittorrentClient", _fake_client)
    client = pc.build_probe_client()
    assert client is not None
    assert captured["base_urls"] == "https://probe:8080"
    assert captured["username"] == "u"


def test_non_string_url_fails_closed(monkeypatch):
    # A misconfigured non-string URL must not crash with AttributeError on
    # .strip(); it should be treated as unconfigured and return None.
    monkeypatch.setattr(pc, "cfg", lambda k, d=None: 12345 if k == "QUALITY_PROBE_QB_URL" else d)
    assert pc.build_probe_client() is None


def test_login_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(pc, "cfg", lambda k, d=None: {
        "QUALITY_PROBE_QB_URL": "https://probe:8080",
    }.get(k, d))

    def _boom(*a, **kw):
        raise Exception("login failed")

    monkeypatch.setattr(pc, "QBittorrentClient", _boom)
    assert pc.build_probe_client() is None
