"""Unit tests for the login-state publish helper used by ``apps.cli.login``.

Covers the acquire-lease -> publish -> release sequence and every fail-open
branch (unconfigured, unhealthy, lease held by a peer, DO error). Lives in its
own module because ``login.py`` ``sys.exit``s at import when config.py is
absent, but ``login_state_publish`` imports cleanly.
"""

from unittest.mock import MagicMock, patch

from javdb.spider.auth.login_state_publish import publish_login_state
from javdb.proxy.coordinator.login_state_client import LoginStateUnavailable

_CLIENT_PATH = "javdb.proxy.coordinator.login_state_client.LoginStateClient"
_CFG_PATH = "javdb.spider.auth.login_state_publish.cfg"


def _cfg_configured(name, default):
    return {
        "PROXY_COORDINATOR_URL": "https://coord.example.workers.dev",
        "PROXY_COORDINATOR_TOKEN": "tok",
    }.get(name, default)


def _make_client(*, healthy=True, acquired=True):
    client = MagicMock()
    client.health_check.return_value = healthy
    client.acquire_lease.return_value = MagicMock(acquired=acquired, holder_id="peer")
    client.publish.return_value = MagicMock(version=7)
    return client


def test_skips_when_coordinator_unconfigured(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, lambda name, default: default)
    with patch(_CLIENT_PATH) as cls:
        assert publish_login_state("cookie", "Proxy-1") is False
    cls.assert_not_called()


def test_skips_when_cookie_or_proxy_missing(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    with patch(_CLIENT_PATH) as cls:
        assert publish_login_state("", "Proxy-1") is False
        assert publish_login_state("cookie", "") is False
    cls.assert_not_called()


def test_publishes_and_releases(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client()
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie-val", "Proxy-1") is True

    client.acquire_lease.assert_called_once()
    holder_id, proxy_name, cookie = client.publish.call_args.args
    assert proxy_name == "Proxy-1"
    assert cookie == "cookie-val"
    # The lease and the publish must use the same holder_id.
    assert client.acquire_lease.call_args.args[0] == holder_id
    client.release_lease.assert_called_once_with(holder_id)
    client.close.assert_called_once()


def test_skips_publish_when_lease_held_by_peer(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client(acquired=False)
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie", "Proxy-1") is False

    client.publish.assert_not_called()
    # Never release a lease we do not own.
    client.release_lease.assert_not_called()
    client.close.assert_called_once()


def test_skips_when_unhealthy(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client(healthy=False)
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie", "Proxy-1") is False

    client.acquire_lease.assert_not_called()
    client.publish.assert_not_called()
    client.close.assert_called_once()


def test_swallows_do_errors(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client()
    client.acquire_lease.side_effect = LoginStateUnavailable("boom")
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie", "Proxy-1") is False

    client.publish.assert_not_called()
    client.close.assert_called_once()


def test_warns_when_published_name_wont_bind(monkeypatch, caplog):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client()
    with patch(_CLIENT_PATH, return_value=client):
        with caplog.at_level("WARNING"):
            assert publish_login_state("cookie", "direct") is True
    assert any("LOGIN_PROXY_NAME" in r.message for r in caplog.records)


def test_no_bind_warning_for_pooled_name(monkeypatch, caplog):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client()
    with patch(_CLIENT_PATH, return_value=client):
        with caplog.at_level("WARNING"):
            assert publish_login_state("cookie", "Proxy-1") is True
    assert not any("LOGIN_PROXY_NAME" in r.message for r in caplog.records)


def test_client_construction_error_is_swallowed(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    with patch(_CLIENT_PATH, side_effect=RuntimeError("boom")):
        assert publish_login_state("cookie", "Proxy-1") is False


def test_non_string_cfg_does_not_crash(monkeypatch):
    # cfg() is untyped; a misconfigured int/bool must not raise AttributeError
    # on .strip() before the fail-open try block.
    monkeypatch.setattr(_CFG_PATH, lambda _name, _default: 12345)
    client = _make_client(healthy=False)
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie", "Proxy-1") is False


def test_close_error_is_swallowed(monkeypatch):
    # A close() error in the finally block must not propagate to the caller.
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client()
    client.close.side_effect = RuntimeError("close boom")
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie", "Proxy-1") is True
    client.close.assert_called_once()


def test_releases_even_when_publish_raises(monkeypatch):
    monkeypatch.setattr(_CFG_PATH, _cfg_configured)
    client = _make_client()
    client.publish.side_effect = LoginStateUnavailable("publish failed")
    with patch(_CLIENT_PATH, return_value=client):
        assert publish_login_state("cookie", "Proxy-1") is False

    client.release_lease.assert_called_once()
    client.close.assert_called_once()
