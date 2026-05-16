"""Unit tests for W6.B — RecommendProxyClient HTTP surface.

Targets the JSON decode + happy-path / error paths against a mocked
``requests.Session`` (mirrors the pattern in
``test_runner_registry_client.py``).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.recommend_proxy_client import (  # noqa: E402
    ProxyRecommendation,
    RecommendProxyClient,
    RecommendProxyUnavailable,
    create_recommend_proxy_client_from_env,
)


def _make_client() -> RecommendProxyClient:
    return RecommendProxyClient(base_url="https://w.test", token="tok")


def _mock_response(status: int, body):
    class _R:
        status_code = status

        def json(self_inner):
            return body

        @property
        def text(self_inner):
            return "" if isinstance(body, dict) else str(body)

    return _R()


# ---------------------------------------------------------------------------
# recommend()
# ---------------------------------------------------------------------------


def test_recommend_returns_typed_recommendations():
    c = _make_client()
    body = {
        "recommendations": [
            {
                "proxy_id": "P-1", "score": 0.9, "latency_ema_ms": 120.0,
                "success_count": 200, "failure_count": 5,
                "banned": False, "requires_cf_bypass": False, "available": True,
            },
            {
                "proxy_id": "P-2", "score": 0.4, "latency_ema_ms": 300.0,
                "success_count": 50, "failure_count": 40,
                "banned": False, "requires_cf_bypass": True, "available": True,
            },
        ],
        "queried_proxy_ids": ["P-1", "P-2"],
        "server_time": 1234,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.recommend(["P-1", "P-2"])
        assert len(r.recommendations) == 2
        assert all(isinstance(rec, ProxyRecommendation) for rec in r.recommendations)
        assert r.recommendations[0].proxy_id == "P-1"
        assert r.recommendations[0].score == pytest.approx(0.9)
        assert r.recommendations[1].requires_cf_bypass is True
        assert r.queried_proxy_ids == ["P-1", "P-2"]
        assert r.server_time_ms == 1234
    finally:
        c.close()


def test_recommend_empty_proxy_ids_short_circuits():
    c = _make_client()
    try:
        # No request is fired; the mock asserts via call count.
        with patch.object(c._session, "get") as get_mock:
            r = c.recommend([])
        get_mock.assert_not_called()
        assert r.recommendations == []
        assert r.queried_proxy_ids == []
    finally:
        c.close()


def test_recommend_blank_proxy_ids_short_circuits():
    c = _make_client()
    try:
        with patch.object(c._session, "get") as get_mock:
            r = c.recommend(["", "  ", None])  # type: ignore[list-item]
        get_mock.assert_not_called()
        assert r.recommendations == []
    finally:
        c.close()


def test_recommend_caps_request_at_32_proxy_ids():
    c = _make_client()
    proxy_ids = [f"P-{i}" for i in range(50)]
    body = {"recommendations": [], "queried_proxy_ids": [], "server_time": 0}
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)) as get_mock:
            c.recommend(proxy_ids)
        # First positional arg is the URL; check it carries ≤ 32 ids.
        url = get_mock.call_args[0][0]
        # Comma-separated ids in the query string.
        ids_in_query = url.split("proxy_ids=")[-1].split("&")[0].split(",")
        assert len(ids_in_query) == 32
    finally:
        c.close()


def test_recommend_top_n_and_include_unhealthy_in_query():
    c = _make_client()
    body = {"recommendations": [], "queried_proxy_ids": [], "server_time": 0}
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)) as get_mock:
            c.recommend(["P-1"], top_n=3, include_unhealthy=True)
        url = get_mock.call_args[0][0]
        assert "top_n=3" in url
        assert "include_unhealthy=1" in url
    finally:
        c.close()


def test_recommend_drops_malformed_rows():
    c = _make_client()
    body = {
        "recommendations": [
            {"proxy_id": "good", "score": 0.5, "latency_ema_ms": 0,
             "success_count": 0, "failure_count": 0,
             "banned": False, "requires_cf_bypass": False, "available": True},
            "not-a-dict",
            {"proxy_id": "", "score": 0.1},  # empty id rejected
        ],
        "queried_proxy_ids": ["good"],
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.recommend(["P-1"])
        assert len(r.recommendations) == 1
        assert r.recommendations[0].proxy_id == "good"
    finally:
        c.close()


def test_recommend_raises_when_recommendations_is_not_a_list():
    c = _make_client()
    body = {"recommendations": "garbage", "queried_proxy_ids": [], "server_time": 0}
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            with pytest.raises(RecommendProxyUnavailable, match="must be a list"):
                c.recommend(["P-1"])
    finally:
        c.close()


def test_recommend_raises_on_http_error():
    c = _make_client()
    try:
        with patch.object(c._session, "get", return_value=_mock_response(503, "down")):
            with pytest.raises(RecommendProxyUnavailable):
                c.recommend(["P-1"])
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _cfg_mapping(**kwargs):
    def _side_effect(key, default=None):
        return kwargs.get(key, default)
    return _side_effect


def test_factory_returns_none_when_flag_off(monkeypatch):
    monkeypatch.delenv("RECOMMEND_PROXY_ENABLED", raising=False)
    assert create_recommend_proxy_client_from_env() is None


def test_factory_returns_none_when_flag_off_explicit(monkeypatch):
    monkeypatch.setenv("RECOMMEND_PROXY_ENABLED", "false")
    assert create_recommend_proxy_client_from_env() is None


def test_factory_returns_none_when_url_missing(monkeypatch):
    monkeypatch.setenv("RECOMMEND_PROXY_ENABLED", "true")
    with patch(
        "packages.python.javdb_platform.config_helper.cfg",
        side_effect=_cfg_mapping(PROXY_COORDINATOR_URL="", PROXY_COORDINATOR_TOKEN=""),
    ):
        assert create_recommend_proxy_client_from_env() is None


def test_factory_returns_client_when_health_passes(monkeypatch):
    monkeypatch.setenv("RECOMMEND_PROXY_ENABLED", "true")
    with patch(
        "packages.python.javdb_platform.config_helper.cfg",
        side_effect=_cfg_mapping(
            PROXY_COORDINATOR_URL="https://w.test",
            PROXY_COORDINATOR_TOKEN="tok",
        ),
    ), patch.object(RecommendProxyClient, "health_check", return_value=True):
        client = create_recommend_proxy_client_from_env()
    assert isinstance(client, RecommendProxyClient)
    assert client.base_url == "https://w.test"
    client.close()


def test_factory_returns_none_when_health_fails(monkeypatch):
    monkeypatch.setenv("RECOMMEND_PROXY_ENABLED", "true")
    with patch(
        "packages.python.javdb_platform.config_helper.cfg",
        side_effect=_cfg_mapping(
            PROXY_COORDINATOR_URL="https://w.test",
            PROXY_COORDINATOR_TOKEN="tok",
        ),
    ), patch.object(RecommendProxyClient, "health_check", return_value=False):
        assert create_recommend_proxy_client_from_env() is None
