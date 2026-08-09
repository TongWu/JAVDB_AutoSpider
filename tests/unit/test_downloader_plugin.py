"""Tests for the downloader category (ADR-039 Phase 2)."""
from __future__ import annotations
# --- Task 3: Protocol + DownloadResult ---

from javdb.integrations.downloader.plugin import DownloadResult


def test_download_result_defaults():
    r = DownloadResult(plugin="qb", ok=True)
    assert r.ok is True
    assert r.detail is None


def test_download_result_failure():
    r = DownloadResult(plugin="qb", ok=False, detail="connection refused")
    assert r.ok is False
    assert r.detail == "connection refused"


def test_downloader_plugin_protocol_conformance():
    """Any object satisfying DownloaderPlugin can be used as one — duck-typed check."""
    from javdb.integrations.downloader.plugin import DownloaderPlugin
    from typing import runtime_checkable, Protocol

    class _Stub:
        name = "stub"
        def is_configured(self) -> bool:
            return True
        def add_torrent(self, magnet: str, category: str, name=None) -> DownloadResult:
            return DownloadResult(plugin=self.name, ok=True)

    # Protocol structural check: the stub satisfies the interface.
    # (DownloaderPlugin need not be @runtime_checkable for this test —
    # we call the methods directly and check the return type.)
    stub = _Stub()
    result = stub.add_torrent("magnet:?xt=urn:btih:abc", "JavDB")
    assert isinstance(result, DownloadResult)
    assert result.ok is True


# --- Task 4: QbDownloaderPlugin ---

import javdb.integrations.downloader.qb.plugin as qb_plugin


def test_qb_is_configured_true(monkeypatch):
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {"QB_HOST": "192.168.1.1", "QB_USERNAME": "admin"}.get(name, default),
    )
    assert qb_plugin.QbDownloaderPlugin().is_configured() is True


def test_qb_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(qb_plugin, "cfg", lambda name, default: default)
    assert qb_plugin.QbDownloaderPlugin().is_configured() is False


def test_qb_add_torrent_calls_client(monkeypatch):
    """add_torrent constructs a QBittorrentClient and calls add_torrent with the right args."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "192.168.1.1",
            "QB_USERNAME": "admin",
            "QB_PASSWORD": "pass",
        }.get(name, default),
    )

    calls = {}

    class _FakeClient:
        def __init__(self, base_urls, username, password, **kw):
            calls["init"] = dict(base_urls=base_urls, username=username, password=password)
        def add_torrent(self, magnet_link, name=None, category=None, **kw):
            calls["add"] = dict(magnet_link=magnet_link, name=name, category=category)
            return True

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://192.168.1.1:8080"])

    plugin = qb_plugin.QbDownloaderPlugin()
    result = plugin.add_torrent("magnet:?xt=urn:btih:abc", category="JavDB", name="MOVIE-001")

    assert calls["add"]["magnet_link"] == "magnet:?xt=urn:btih:abc"
    assert calls["add"]["category"] == "JavDB"
    assert calls["add"]["name"] == "MOVIE-001"
    assert result.ok is True
    assert result.plugin == "qb"


def test_qb_add_torrent_forwards_download_config(monkeypatch):
    """request_timeout + save_path/paused/skip_checking are read from cfg and forwarded."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "h",
            "QB_USERNAME": "u",
            "QB_PASSWORD": "p",
            "REQUEST_TIMEOUT": 15,
            "TORRENT_SAVE_PATH": "/data/av",
            "AUTO_START": False,
            "SKIP_CHECKING": True,
        }.get(name, default),
    )

    calls = {}

    class _FakeClient:
        def __init__(self, **kw):
            calls["init"] = kw
        def add_torrent(self, **kw):
            calls["add"] = kw
            return True

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://h:8080"])

    result = qb_plugin.QbDownloaderPlugin().add_torrent("magnet:?xt=urn:btih:abc", "JavDB")

    assert result.ok is True
    assert calls["init"]["request_timeout"] == 15
    assert calls["add"]["save_path"] == "/data/av"
    assert calls["add"]["paused"] is True  # AUTO_START False → add paused
    assert calls["add"]["skip_checking"] is True


def test_qb_add_torrent_download_config_defaults(monkeypatch):
    """Without explicit config, uploader-aligned defaults apply (timeout 30, autostart on)."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "h", "QB_USERNAME": "u", "QB_PASSWORD": "p",
        }.get(name, default),
    )

    calls = {}

    class _FakeClient:
        def __init__(self, **kw):
            calls["init"] = kw
        def add_torrent(self, **kw):
            calls["add"] = kw
            return True

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://h:8080"])

    qb_plugin.QbDownloaderPlugin().add_torrent("magnet:?xt=urn:btih:abc", "JavDB")

    assert calls["init"]["request_timeout"] == 30
    assert calls["add"]["save_path"] == ""
    assert calls["add"]["paused"] is False  # AUTO_START default True → not paused
    assert calls["add"]["skip_checking"] is False


def test_qb_add_torrent_maps_false_to_failure(monkeypatch):
    """QBittorrentClient.add_torrent returning False → DownloadResult(ok=False)."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "h", "QB_USERNAME": "u", "QB_PASSWORD": "p",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def add_torrent(self, *a, **k): return False

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://h:8080"])

    result = qb_plugin.QbDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert result.detail is not None


def test_qb_add_torrent_isolates_exception(monkeypatch):
    """Network exception from QBittorrentClient → DownloadResult(ok=False), not raised."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "h", "QB_USERNAME": "u", "QB_PASSWORD": "p",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def add_torrent(self, *a, **k): raise ConnectionError("refused")

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://h:8080"])

    result = qb_plugin.QbDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert "refused" in (result.detail or "")


# --- Task 5: TransmissionRpcClient ---

import javdb.integrations.downloader.transmission.client as tr_client


def _make_resp(status_code, headers, json_data):
    """Build a minimal fake requests.Response with raise_for_status."""
    class _R:
        pass
    r = _R()
    r.status_code = status_code
    r.headers = headers
    r.json = lambda: json_data
    r.raise_for_status = lambda: None  # 200-level responses don't raise
    return r


def test_transmission_torrent_add_happy_path(monkeypatch):
    """Happy path: session-id negotiation + torrent-add, returns (True, None)."""
    session_responses = iter([
        # First request: 409 with session-id header
        _make_resp(409, {"X-Transmission-Session-Id": "SID123"}, {}),
        # Second request: 200 with result "success"
        _make_resp(200, {}, {"result": "success"}),
    ])

    monkeypatch.setattr(tr_client.requests, "post", lambda *a, **kw: next(session_responses))
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    ok, detail = client.torrent_add("magnet:?xt=urn:btih:abc", download_dir="/downloads", labels=["JavDB"])
    assert ok is True
    assert detail is None


def test_transmission_torrent_add_session_id_negotiation(monkeypatch):
    """409 reply updates the session-id stored on the client."""
    responses = iter([
        _make_resp(409, {"X-Transmission-Session-Id": "NEW-SID"}, {}),
        _make_resp(200, {}, {"result": "success"}),
    ])
    calls = []
    def _post(url, **kw):
        calls.append(kw.get("headers", {}).get("X-Transmission-Session-Id"))
        return next(responses)
    monkeypatch.setattr(tr_client.requests, "post", _post)
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    client.torrent_add("magnet:...", download_dir="/d", labels=[])
    # Second call must use the new session-id
    assert calls[1] == "NEW-SID"


def test_transmission_torrent_add_error_result(monkeypatch):
    """result != 'success' → ok=False with detail."""
    responses = iter([
        _make_resp(409, {"X-Transmission-Session-Id": "SID"}, {}),
        _make_resp(200, {}, {"result": "duplicate torrent"}),
    ])
    monkeypatch.setattr(tr_client.requests, "post", lambda *a, **kw: next(responses))
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    ok, detail = client.torrent_add("magnet:...", download_dir="/d", labels=[])
    assert ok is False
    assert "duplicate torrent" in (detail or "")


def test_transmission_network_exception(monkeypatch):
    """requests.post raises → propagates to caller (plugin layer catches it)."""
    monkeypatch.setattr(tr_client.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("refused")))
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    import pytest
    with pytest.raises(ConnectionError):
        client.torrent_add("magnet:...", download_dir="/d", labels=[])


# --- Task 6: TransmissionDownloaderPlugin ---

import javdb.integrations.downloader.transmission.plugin as tr_plugin


def test_transmission_is_configured_true(monkeypatch):
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "192.168.1.10",
            "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )
    assert tr_plugin.TransmissionDownloaderPlugin().is_configured() is True


def test_transmission_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(tr_plugin, "cfg", lambda name, default: default)
    assert tr_plugin.TransmissionDownloaderPlugin().is_configured() is False


def test_transmission_add_torrent_calls_rpc_client(monkeypatch):
    """add_torrent constructs a TransmissionRpcClient and calls torrent_add."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "192.168.1.10",
            "TRANSMISSION_PORT": "9091",
            "TRANSMISSION_USERNAME": "user",
            "TRANSMISSION_PASSWORD": "pass",
            "TRANSMISSION_DOWNLOAD_DIR": "/downloads",
        }.get(name, default),
    )

    calls = {}

    class _FakeClient:
        def __init__(self, base_url, username, password, **kw):
            calls["init"] = dict(base_url=base_url, username=username, password=password)
        def torrent_add(self, magnet, download_dir, labels=None):
            calls["torrent_add"] = dict(magnet=magnet, download_dir=download_dir, labels=labels)
            return True, None

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _FakeClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent(
        "magnet:?xt=urn:btih:abc", "JavDB"
    )
    assert result.ok is True
    assert calls["torrent_add"]["magnet"] == "magnet:?xt=urn:btih:abc"
    assert calls["torrent_add"]["labels"] == ["JavDB"]


def test_transmission_add_torrent_rejects_name(monkeypatch):
    """name= is unsupported → ok=False with explicit detail, RPC client never constructed."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "h", "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )

    class _BoomClient:
        def __init__(self, *a, **k):
            raise AssertionError("RPC client must not be constructed when name is given")

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _BoomClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent(
        "magnet:?xt=urn:btih:abc", "JavDB", name="X"
    )
    assert result.ok is False
    assert "not supported" in (result.detail or "")
    assert "--name" in (result.detail or "")


def test_transmission_add_torrent_maps_rpc_failure(monkeypatch):
    """torrent_add returning (False, detail) → DownloadResult(ok=False)."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "h", "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def torrent_add(self, *a, **k): return False, "duplicate torrent"

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _FakeClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert "duplicate torrent" in (result.detail or "")


def test_transmission_add_torrent_isolates_network_exception(monkeypatch):
    """Network exception from TransmissionRpcClient → DownloadResult(ok=False), not raised."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "h", "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def torrent_add(self, *a, **k): raise ConnectionError("refused")

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _FakeClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert "refused" in (result.detail or "")


# --- Task 7: downloader dispatch ---

import javdb.integrations.downloader.dispatch as dl_dispatch
from javdb.integrations.plugins.registry import PluginRegistry


class _DlPlugin:
    def __init__(self, name, configured=True, raises=False):
        self.name = name
        self._configured = configured
        self._raises = raises
    def is_configured(self):
        return self._configured
    def add_torrent(self, magnet, category, name=None):
        if self._raises:
            raise RuntimeError("dl boom")
        return DownloadResult(plugin=self.name, ok=True)


def _dl_registry(*plugins):
    reg = PluginRegistry()
    for p in plugins:
        reg.register("downloader", p)
    return reg


def test_active_downloader_name_default_qb(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: default)
    assert dl_dispatch.active_downloader_name() == "qb"


def test_active_downloader_name_reads_config(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "transmission")
    assert dl_dispatch.active_downloader_name() == "transmission"


def test_dispatch_add_routes_to_active_backend(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb")))
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is True
    assert result.plugin == "qb"


def test_dispatch_add_not_registered(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "ghost")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry())
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is False
    assert "not registered" in (result.detail or "")


def test_dispatch_add_not_configured(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb", configured=False)))
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is False
    assert "not configured" in (result.detail or "")


def test_dispatch_add_failure_isolated(monkeypatch):
    """An exception from the backend is caught and returned as DownloadResult(ok=False)."""
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb", raises=True)))
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is False
    assert "dl boom" in (result.detail or "")


# --- Task 8: CLI smoke ---

def test_cli_add_exits_zero_on_success(monkeypatch):
    """CLI main() returns 0 on DownloadResult(ok=True)."""
    import javdb.integrations.downloader.dispatch as _dispatch
    monkeypatch.setattr(_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb")))

    import importlib, apps.cli.download.add as add_cli
    importlib.reload(add_cli)  # reload so module-level imports re-run with patched env

    # Provide the monkeypatched dispatch to the CLI module directly.
    monkeypatch.setattr(add_cli, "dispatch", _dispatch)

    exit_code = add_cli.main(["--magnet", "magnet:?xt=urn:btih:abc", "--category", "JavDB"])
    assert exit_code == 0


def test_cli_add_exits_nonzero_on_failure(monkeypatch):
    """CLI main() returns 1 on DownloadResult(ok=False)."""
    import javdb.integrations.downloader.dispatch as _dispatch
    monkeypatch.setattr(_dispatch, "cfg", lambda name, default: "ghost")
    monkeypatch.setattr(_dispatch, "REGISTRY", _dl_registry())

    import apps.cli.download.add as add_cli
    monkeypatch.setattr(add_cli, "dispatch", _dispatch)

    exit_code = add_cli.main(["--magnet", "magnet:?xt=urn:btih:abc", "--category", "JavDB"])
    assert exit_code == 1
