"""ADR-024 IMP-10: qB client metadata-only probe support."""

from __future__ import annotations

from javdb.integrations.qb.client import (
    WEBAPI_MIN_FOR_STOP_CONDITION,
    webapi_supports_metadata_only,
)


def test_version_at_threshold_supports():
    assert webapi_supports_metadata_only("2.8.3") is True


def test_newer_version_supports():
    assert webapi_supports_metadata_only("2.11.0") is True


def test_older_version_unsupported():
    assert webapi_supports_metadata_only("2.8.2") is False


def test_blank_or_garbage_is_unsupported_fail_closed():
    assert webapi_supports_metadata_only("") is False
    assert webapi_supports_metadata_only("not-a-version") is False


def test_threshold_constant_is_283():
    assert WEBAPI_MIN_FOR_STOP_CONDITION == (2, 8, 3)


class _FakeResp:
    status_code = 200


def test_add_torrent_sends_stop_condition(monkeypatch):
    from javdb.integrations.qb import client as qbmod

    captured = {}

    class _C(qbmod.QBittorrentClient):
        def __init__(self):  # bypass real login/network
            self.base_url = "http://probe"
            self.session = type("S", (), {"post": self._post})()
            self.proxies = None
            self.request_timeout = None

        def _post(self, url, data=None, **kw):
            captured["data"] = data
            return _FakeResp()

    ok = _C().add_torrent(
        "magnet:?xt=urn:btih:abc", category="JavDB Quality Shadow",
        paused=True, stop_condition="MetadataReceived",
    )
    assert ok is True
    assert captured["data"]["stopCondition"] == "MetadataReceived"


def test_add_torrent_without_stop_condition_omits_key(monkeypatch):
    from javdb.integrations.qb import client as qbmod

    captured = {}

    class _C(qbmod.QBittorrentClient):
        def __init__(self):
            self.base_url = "http://probe"
            self.session = type("S", (), {"post": self._post})()
            self.proxies = None
            self.request_timeout = None

        def _post(self, url, data=None, **kw):
            captured["data"] = data
            return _FakeResp()

    _C().add_torrent("magnet:?xt=urn:btih:abc")
    assert "stopCondition" not in captured["data"]  # additive: omitted when not passed


def test_get_torrent_files_delegates_to_readonly(monkeypatch):
    from javdb.integrations.qb import client as qbmod
    from javdb.integrations.qb import readonly

    captured = {}

    def _fake(session, base_url, info_hash, **kw):
        captured.update(base_url=base_url, info_hash=info_hash)
        return [{"name": "x.mkv", "size": 1}]

    monkeypatch.setattr(readonly, "get_torrent_files", _fake)

    class _C(qbmod.QBittorrentClient):
        def __init__(self):
            self.base_url = "http://probe"
            self.session = type("S", (), {"verify": True})()
            self.proxies = None
            self.request_timeout = None

    files = _C().get_torrent_files("HASH1")
    assert files == [{"name": "x.mkv", "size": 1}]
    assert captured == {"base_url": "http://probe", "info_hash": "HASH1"}


def _probe_client_with_session(session):
    from javdb.integrations.qb import client as qbmod

    class _C(qbmod.QBittorrentClient):
        def __init__(self):
            self.base_url = "http://probe"
            self.session = session
            self.proxies = None
            self.request_timeout = None

    return _C()


def test_get_webapi_version_empty_on_non_200():
    class _Resp:
        status_code = 404
        text = "nope"

    class _Sess:
        def get(self, *a, **k):
            return _Resp()

    assert _probe_client_with_session(_Sess()).get_webapi_version() == ""


def test_get_webapi_version_empty_on_exception():
    class _Sess:
        def get(self, *a, **k):
            raise RuntimeError("network down")

    assert _probe_client_with_session(_Sess()).get_webapi_version() == ""


def test_supports_metadata_only_probe_wraps_version(monkeypatch):
    from javdb.integrations.qb import client as qbmod

    class _C(qbmod.QBittorrentClient):
        def __init__(self, version):
            self._version = version

        def get_webapi_version(self):
            return self._version

    assert _C("2.8.3").supports_metadata_only_probe() is True
    assert _C("2.8.2").supports_metadata_only_probe() is False
    assert _C("").supports_metadata_only_probe() is False  # fail-closed
