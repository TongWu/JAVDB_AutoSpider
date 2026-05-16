"""Unit tests for W6.C — WorkDistributorClient HTTP surface.

Mirrors the pattern in test_runner_registry_client.py: mock the
``requests.Session`` POST/GET and assert the decoded dataclass shapes,
factory disable paths, and error handling.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.work_distributor_client import (  # noqa: E402
    CompleteResult,
    EnqueueResult,
    PullResult,
    ReleaseResult,
    StatsResult,
    WorkDistributorClient,
    WorkDistributorUnavailable,
    WorkItem,
    create_work_distributor_client_from_env,
)


def _make_client() -> WorkDistributorClient:
    return WorkDistributorClient(base_url="https://w.test", token="tok")


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
# enqueue()
# ---------------------------------------------------------------------------


def test_enqueue_accepts_list_of_strings():
    c = _make_client()
    body = {
        "enqueued": ["A", "B"], "duplicates": [],
        "queue_size": 2, "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)) as post:
            r = c.enqueue(["A", "B"])
        assert isinstance(r, EnqueueResult)
        assert r.enqueued == ["A", "B"]
        # Body shape: items list of {key: ...} dicts.
        sent = post.call_args[1]["json"]
        assert sent == {
            "items": [{"key": "A"}, {"key": "B"}],
            "replace_existing": False,
        }
    finally:
        c.close()


def test_enqueue_accepts_dicts_with_payloads():
    c = _make_client()
    body = {"enqueued": ["A"], "duplicates": [], "queue_size": 1, "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)) as post:
            c.enqueue([{"key": "A", "payload": {"hint": "x"}}])
        sent = post.call_args[1]["json"]
        assert sent["items"] == [{"key": "A", "payload": {"hint": "x"}}]
    finally:
        c.close()


def test_enqueue_filters_blank_keys_client_side():
    c = _make_client()
    body = {"enqueued": [], "duplicates": [], "queue_size": 0, "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)) as post:
            c.enqueue(["", "   ", "real", {"key": "  "}])
        sent = post.call_args[1]["json"]
        assert sent["items"] == [{"key": "real"}]
    finally:
        c.close()


def test_enqueue_empty_list_skips_network():
    c = _make_client()
    try:
        with patch.object(c._session, "post") as post:
            r = c.enqueue([])
        post.assert_not_called()
        assert r.enqueued == []
    finally:
        c.close()


def test_enqueue_replace_existing_flag_propagates():
    c = _make_client()
    body = {"enqueued": [], "duplicates": ["A"], "queue_size": 1, "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)) as post:
            c.enqueue(["A"], replace_existing=True)
        sent = post.call_args[1]["json"]
        assert sent["replace_existing"] is True
    finally:
        c.close()


# ---------------------------------------------------------------------------
# pull()
# ---------------------------------------------------------------------------


def test_pull_decodes_typed_items():
    c = _make_client()
    body = {
        "items": [
            {"key": "A", "payload": {"k": 1}, "enqueued_at_ms": 100, "attempt_count": 2},
            {"key": "B", "payload": None, "enqueued_at_ms": 200, "attempt_count": 1},
        ],
        "queue_size": 5,
        "server_time": 999,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.pull("holder-1", max_items=10)
        assert isinstance(r, PullResult)
        assert len(r.items) == 2
        assert all(isinstance(i, WorkItem) for i in r.items)
        assert r.items[0].key == "A"
        assert r.items[0].payload == {"k": 1}
        assert r.items[0].attempt_count == 2
        assert r.queue_size == 5
    finally:
        c.close()


def test_pull_drops_malformed_items():
    c = _make_client()
    body = {
        "items": [
            {"key": "ok", "payload": None, "enqueued_at_ms": 1, "attempt_count": 1},
            "not-a-dict",
            {"key": "", "payload": "x"},  # empty key rejected
        ],
        "queue_size": 1,
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.pull("h", max_items=5)
        assert len(r.items) == 1
        assert r.items[0].key == "ok"
    finally:
        c.close()


def test_pull_requires_holder_id():
    c = _make_client()
    try:
        with pytest.raises(WorkDistributorUnavailable, match="holder_id"):
            c.pull("", max_items=5)
    finally:
        c.close()


def test_pull_sends_visibility_timeout():
    c = _make_client()
    body = {"items": [], "queue_size": 0, "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)) as post:
            c.pull("h", max_items=3, visibility_timeout_ms=12345)
        sent = post.call_args[1]["json"]
        assert sent == {
            "holder_id": "h",
            "max_items": 3,
            "visibility_timeout_ms": 12345,
        }
    finally:
        c.close()


# ---------------------------------------------------------------------------
# complete() / release()
# ---------------------------------------------------------------------------


def test_complete_with_no_keys_skips_network():
    c = _make_client()
    try:
        with patch.object(c._session, "post") as post:
            r = c.complete("h", [])
        post.assert_not_called()
        assert isinstance(r, CompleteResult)
        assert r.completed == []
    finally:
        c.close()


def test_complete_filters_blank_keys():
    c = _make_client()
    body = {"completed": ["A"], "skipped": [], "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)) as post:
            c.complete("h", ["", "  ", "A"])
        sent = post.call_args[1]["json"]
        assert sent["keys"] == ["A"]
    finally:
        c.close()


def test_release_decodes_result():
    c = _make_client()
    body = {"released": ["A"], "skipped": ["B"], "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.release("h", ["A", "B"])
        assert isinstance(r, ReleaseResult)
        assert r.released == ["A"]
        assert r.skipped == ["B"]
    finally:
        c.close()


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


def test_stats_decodes_typed_result():
    c = _make_client()
    body = {
        "queue_size": 3, "visible": 2, "leased": 1,
        "oldest_enqueued_at_ms": 123456, "server_time": 999,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.stats()
        assert isinstance(r, StatsResult)
        assert r.queue_size == 3
        assert r.visible == 2
        assert r.leased == 1
        assert r.oldest_enqueued_at_ms == 123456
    finally:
        c.close()


def test_stats_handles_null_oldest():
    c = _make_client()
    body = {"queue_size": 0, "visible": 0, "leased": 0,
            "oldest_enqueued_at_ms": None, "server_time": 1}
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.stats()
        assert r.oldest_enqueued_at_ms is None
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_http_error_raises_unavailable():
    c = _make_client()
    try:
        with patch.object(c._session, "post", return_value=_mock_response(503, "down")):
            with pytest.raises(WorkDistributorUnavailable):
                c.enqueue(["A"])
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _cfg_mapping(**kwargs):
    def _side_effect(key, default=None):
        return kwargs.get(key, default)
    return _side_effect


def test_factory_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WORK_DISTRIBUTOR_ENABLED", raising=False)
    assert create_work_distributor_client_from_env() is None


def test_factory_disabled_when_flag_false(monkeypatch):
    monkeypatch.setenv("WORK_DISTRIBUTOR_ENABLED", "false")
    assert create_work_distributor_client_from_env() is None


def test_factory_returns_client_when_healthy(monkeypatch):
    monkeypatch.setenv("WORK_DISTRIBUTOR_ENABLED", "true")
    with patch(
        "packages.python.javdb_platform.config_helper.cfg",
        side_effect=_cfg_mapping(
            PROXY_COORDINATOR_URL="https://w.test",
            PROXY_COORDINATOR_TOKEN="tok",
        ),
    ), patch.object(WorkDistributorClient, "health_check", return_value=True):
        c = create_work_distributor_client_from_env()
    assert isinstance(c, WorkDistributorClient)
    c.close()


def test_factory_returns_none_when_health_fails(monkeypatch):
    monkeypatch.setenv("WORK_DISTRIBUTOR_ENABLED", "true")
    with patch(
        "packages.python.javdb_platform.config_helper.cfg",
        side_effect=_cfg_mapping(
            PROXY_COORDINATOR_URL="https://w.test",
            PROXY_COORDINATOR_TOKEN="tok",
        ),
    ), patch.object(WorkDistributorClient, "health_check", return_value=False):
        assert create_work_distributor_client_from_env() is None
