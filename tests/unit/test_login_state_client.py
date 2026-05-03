"""Tests for :class:`LoginStateClient`.

Mirror the structure of ``test_proxy_coordinator_client.py``: keep the
network mocked at the ``requests.Session`` boundary so the suite is
deterministic and runs offline, and assert that every failure path
collapses into :class:`LoginStateUnavailable` (the fail-open contract
documented in ``login_state_client.py``).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.login_state_client import (  # noqa: E402
    AcquireLeaseResult,
    InvalidateResult,
    LEASE_TTL_MAX_MS,
    LEASE_TTL_MIN_MS,
    LoginStateClient,
    LoginStateGetResult,
    LoginStateUnavailable,
    PublishResult,
    ReleaseLeaseResult,
    create_login_state_client_from_env,
    _extract_server_time_ms,
)


def _make_client() -> LoginStateClient:
    """Build a client without any network I/O during construction."""
    return LoginStateClient(
        base_url="https://coord.example.test",
        token="dummy",
    )


def _mock_response(status: int = 200, json_body=None, text: str = "") -> MagicMock:
    """Build a minimal ``requests.Response``-shaped MagicMock."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = text
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no JSON")
    return resp


# ── construction ────────────────────────────────────────────────────────────


def test_init_rejects_empty_base_url():
    with pytest.raises(ValueError, match="base_url"):
        LoginStateClient(base_url="", token="t")


def test_init_rejects_empty_token():
    with pytest.raises(ValueError, match="token"):
        LoginStateClient(base_url="https://coord.test", token="")


def test_init_strips_trailing_slash_from_base_url():
    c = LoginStateClient(base_url="https://coord.test/", token="t")
    assert c.base_url == "https://coord.test"


def test_init_sets_bearer_header():
    c = _make_client()
    assert c._session.headers["Authorization"] == "Bearer dummy"


# ── get_state ───────────────────────────────────────────────────────────────


def test_get_state_returns_typed_result_on_success():
    c = _make_client()
    body = {
        "proxy_name": "JP-1",
        "cookie": "_jdb=abc",
        "version": 7,
        "last_verified_at": 12_345,
        "has_active_lease": True,
        "server_time": 99_999,
    }
    with patch.object(c._session, "get", return_value=_mock_response(200, body)):
        r = c.get_state()
    assert isinstance(r, LoginStateGetResult)
    assert r.proxy_name == "JP-1"
    assert r.cookie == "_jdb=abc"
    assert r.version == 7
    assert r.last_verified_at == 12_345
    assert r.has_active_lease is True
    assert r.server_time_ms == 99_999


def test_get_state_handles_null_cookie_and_proxy_name():
    """Empty / never-published state surfaces as ``None`` (not raise)."""
    c = _make_client()
    body = {
        "proxy_name": None,
        "cookie": None,
        "version": 0,
        "last_verified_at": 0,
        "has_active_lease": False,
        "server_time_ms": 1,
    }
    with patch.object(c._session, "get", return_value=_mock_response(200, body)):
        r = c.get_state()
    assert r.proxy_name is None
    assert r.cookie is None
    assert r.version == 0


def test_get_state_raises_on_timeout():
    c = _make_client()
    with patch.object(c._session, "get", side_effect=requests.Timeout("slow")):
        with pytest.raises(LoginStateUnavailable, match="network error"):
            c.get_state()


def test_get_state_raises_on_connection_error():
    c = _make_client()
    with patch.object(c._session, "get", side_effect=requests.ConnectionError("boom")):
        with pytest.raises(LoginStateUnavailable, match="network error"):
            c.get_state()


def test_get_state_raises_on_non_2xx():
    c = _make_client()
    resp = _mock_response(status=500, text="upstream error")
    with patch.object(c._session, "get", return_value=resp):
        with pytest.raises(LoginStateUnavailable, match="HTTP 500"):
            c.get_state()


def test_get_state_raises_on_invalid_json():
    c = _make_client()
    resp = _mock_response(status=200)  # json() will raise ValueError
    with patch.object(c._session, "get", return_value=resp):
        with pytest.raises(LoginStateUnavailable, match="invalid JSON"):
            c.get_state()


def test_get_state_raises_on_malformed_payload():
    c = _make_client()
    resp = _mock_response(200, {"version": "not-an-int"})
    with patch.object(c._session, "get", return_value=resp):
        with pytest.raises(LoginStateUnavailable, match="malformed get_state response"):
            c.get_state()


# ── acquire_lease ───────────────────────────────────────────────────────────


def test_acquire_lease_acquired():
    c = _make_client()
    body = {
        "acquired": True,
        "holder_id": "runner-A",
        "target_proxy_name": "JP-1",
        "lease_expires_at": 100_000,
        "server_time": 50_000,
    }
    with patch.object(c._session, "post", return_value=_mock_response(200, body)) as p:
        r = c.acquire_lease("runner-A", "JP-1", 60_000)
    assert isinstance(r, AcquireLeaseResult)
    assert r.acquired is True
    assert r.holder_id == "runner-A"
    assert r.target_proxy_name == "JP-1"
    assert r.lease_expires_at == 100_000
    # Verify the request payload that was sent.
    args, kwargs = p.call_args
    assert args[0].endswith("/login_state/acquire_lease")
    assert kwargs["json"] == {
        "holder_id": "runner-A",
        "target_proxy_name": "JP-1",
        "ttl_ms": 60_000,
    }


def test_acquire_lease_not_acquired_is_a_value_not_an_exception():
    """Holder mismatch is the routine 'park and retry' path — must NOT raise."""
    c = _make_client()
    body = {
        "acquired": False,
        "holder_id": "runner-B",
        "target_proxy_name": "JP-1",
        "lease_expires_at": 100_000,
        "server_time": 50_000,
    }
    with patch.object(c._session, "post", return_value=_mock_response(200, body)):
        r = c.acquire_lease("runner-A", "JP-1", 60_000)
    assert r.acquired is False
    assert r.holder_id == "runner-B"


def test_acquire_lease_rejects_empty_holder_id():
    c = _make_client()
    with pytest.raises(LoginStateUnavailable, match="holder_id"):
        c.acquire_lease("", "JP-1", 60_000)


def test_acquire_lease_rejects_empty_target_proxy_name():
    c = _make_client()
    with pytest.raises(LoginStateUnavailable, match="target_proxy_name"):
        c.acquire_lease("runner-A", "", 60_000)


def test_acquire_lease_raises_on_network_error():
    c = _make_client()
    with patch.object(c._session, "post", side_effect=requests.ConnectionError("nope")):
        with pytest.raises(LoginStateUnavailable, match="network error"):
            c.acquire_lease("runner-A", "JP-1", 60_000)


# ── publish ─────────────────────────────────────────────────────────────────


def test_publish_returns_new_version():
    c = _make_client()
    body = {"ok": True, "version": 4, "server_time": 7_777}
    with patch.object(c._session, "post", return_value=_mock_response(200, body)) as p:
        r = c.publish("runner-A", "JP-1", "_jdb=alpha")
    assert isinstance(r, PublishResult)
    assert r.ok is True
    assert r.version == 4
    args, kwargs = p.call_args
    assert args[0].endswith("/login_state/publish")
    assert kwargs["json"] == {
        "holder_id": "runner-A",
        "proxy_name": "JP-1",
        "cookie": "_jdb=alpha",
    }


def test_publish_409_lease_required_surfaces_as_unavailable():
    """The Worker returns 409 when the caller is not the lease holder.

    The client surfaces this as :class:`LoginStateUnavailable`; callers
    typically respond by polling :meth:`get_state` and parking their
    work — never by retrying the publish.
    """
    c = _make_client()
    resp = _mock_response(status=409, text='{"error":"lease_required"}')
    with patch.object(c._session, "post", return_value=resp):
        with pytest.raises(LoginStateUnavailable, match="HTTP 409"):
            c.publish("runner-A", "JP-1", "_jdb=alpha")


def test_publish_413_oversized_cookie_surfaces_as_unavailable():
    c = _make_client()
    resp = _mock_response(status=413, text='{"error":"cookie too large"}')
    with patch.object(c._session, "post", return_value=resp):
        with pytest.raises(LoginStateUnavailable, match="HTTP 413"):
            c.publish("runner-A", "JP-1", "x" * 20000)


def test_publish_rejects_missing_fields():
    c = _make_client()
    with pytest.raises(LoginStateUnavailable):
        c.publish("", "JP-1", "x")
    with pytest.raises(LoginStateUnavailable):
        c.publish("runner-A", "", "x")
    with pytest.raises(LoginStateUnavailable):
        c.publish("runner-A", "JP-1", "")


# ── invalidate ──────────────────────────────────────────────────────────────


def test_invalidate_with_matching_version():
    c = _make_client()
    body = {"invalidated": True, "current_version": 8, "server_time": 1}
    with patch.object(c._session, "post", return_value=_mock_response(200, body)) as p:
        r = c.invalidate(7)
    assert isinstance(r, InvalidateResult)
    assert r.invalidated is True
    assert r.current_version == 8
    args, kwargs = p.call_args
    assert args[0].endswith("/login_state/invalidate")
    assert kwargs["json"] == {"version": 7}


def test_invalidate_with_stale_version_returns_invalidated_false():
    """Optimistic-lock failure is a routine signal, not an error."""
    c = _make_client()
    body = {"invalidated": False, "current_version": 9, "server_time": 1}
    with patch.object(c._session, "post", return_value=_mock_response(200, body)):
        r = c.invalidate(5)
    assert r.invalidated is False
    assert r.current_version == 9


# ── release_lease ───────────────────────────────────────────────────────────


def test_release_lease_owner():
    c = _make_client()
    body = {"released": True, "server_time": 1}
    with patch.object(c._session, "post", return_value=_mock_response(200, body)) as p:
        r = c.release_lease("runner-A")
    assert isinstance(r, ReleaseLeaseResult)
    assert r.released is True
    args, kwargs = p.call_args
    assert args[0].endswith("/login_state/release_lease")
    assert kwargs["json"] == {"holder_id": "runner-A"}


def test_release_lease_non_owner_returns_released_false():
    c = _make_client()
    body = {"released": False, "server_time": 1}
    with patch.object(c._session, "post", return_value=_mock_response(200, body)):
        r = c.release_lease("runner-X")
    assert r.released is False


def test_release_lease_rejects_empty_holder_id():
    c = _make_client()
    with pytest.raises(LoginStateUnavailable, match="holder_id"):
        c.release_lease("")


# ── health_check ────────────────────────────────────────────────────────────


def test_health_check_returns_true_on_200():
    c = _make_client()
    resp = MagicMock(status_code=200)
    with patch.object(c._session, "get", return_value=resp):
        assert c.health_check() is True


def test_health_check_returns_false_on_non_200():
    c = _make_client()
    resp = MagicMock(status_code=503)
    with patch.object(c._session, "get", return_value=resp):
        assert c.health_check() is False


def test_health_check_swallows_exceptions():
    c = _make_client()
    with patch.object(c._session, "get", side_effect=requests.ConnectionError):
        assert c.health_check() is False


# ── factory + env wiring ────────────────────────────────────────────────────


def test_factory_returns_none_when_url_unset(monkeypatch):
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    monkeypatch.delenv("PROXY_COORDINATOR_TOKEN", raising=False)
    assert create_login_state_client_from_env() is None


def test_factory_returns_none_when_only_token_set(monkeypatch):
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "tok")
    assert create_login_state_client_from_env() is None


def test_factory_returns_none_when_health_fails(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "tok")
    with patch.object(LoginStateClient, "health_check", return_value=False):
        assert create_login_state_client_from_env() is None


def test_factory_returns_client_when_health_passes(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "tok")
    with patch.object(LoginStateClient, "health_check", return_value=True):
        client = create_login_state_client_from_env()
    assert isinstance(client, LoginStateClient)
    assert client.base_url == "https://coord.test"


# ── helpers ─────────────────────────────────────────────────────────────────


def test_extract_server_time_ms_prefers_explicit_key():
    assert _extract_server_time_ms({"server_time_ms": 5, "server_time": 9}) == 5


def test_extract_server_time_ms_falls_back_to_server_time():
    assert _extract_server_time_ms({"server_time": 7}) == 7


def test_lease_ttl_constants_match_worker():
    """Sanity check: client constants must equal the Worker's bounds.

    If the Worker bumps these (in src/types.ts), this test will catch a
    drift before runtime: an out-of-bounds ``ttl_ms`` is silently clamped
    on the server but the client should still use sane defaults.
    """
    assert LEASE_TTL_MIN_MS == 5_000
    assert LEASE_TTL_MAX_MS == 300_000
