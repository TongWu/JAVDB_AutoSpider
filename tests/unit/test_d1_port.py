from __future__ import annotations

import json

import pytest

from javdb.storage.d1_client import D1PermanentError, D1TransientError
from javdb.storage.d1_port import D1AccessPort, D1PortConfig


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakePoster:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _port(poster, *, max_retries=2):
    return D1AccessPort(
        url="https://example.test/query",
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        config=D1PortConfig(
            timeout=3,
            batch_limit=50,
            max_retries=max_retries,
            retry_base_sec=0,
            retry_max_sleep_sec=0,
        ),
        post_request=poster,
        sleep=lambda _seconds: None,
        jitter=lambda: 0,
    )


def test_execute_posts_single_statement_body():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 1}, "results": []}],
                }
            )
        ]
    )
    port = _port(poster)

    cursors = port.execute("SELECT 1", [])

    assert len(cursors) == 1
    assert poster.calls[0]["json"] == {"sql": "SELECT 1", "params": []}
    assert port.summary()["http_posts"] == 1


def test_transient_error_retries_then_succeeds():
    poster = FakePoster(
        [
            FakeResponse(
                status_code=500,
                payload={"success": False, "errors": [{"message": "temporary"}]},
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"n": 1}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster, max_retries=2)

    cursors = port.execute("SELECT 1", [])

    assert cursors[0].fetchone() == {"n": 1}
    assert len(poster.calls) == 2
    assert port.summary()["retries"] == 1
    assert port.summary()["retry_successes"] == 1


def test_permanent_error_does_not_retry():
    poster = FakePoster(
        [
            FakeResponse(
                status_code=400,
                payload={"success": False, "errors": [{"message": "no such table: x"}]},
            )
        ]
    )
    port = _port(poster, max_retries=3)

    with pytest.raises(D1PermanentError):
        port.execute("SELECT * FROM x", [])

    assert len(poster.calls) == 1
    assert port.summary()["permanent_errors"] == 1


def test_transient_error_exhaustion_raises_transient():
    poster = FakePoster(
        [
            FakeResponse(
                status_code=429,
                payload={"success": False, "errors": [{"message": "overloaded"}]},
            ),
            FakeResponse(
                status_code=429,
                payload={"success": False, "errors": [{"message": "overloaded"}]},
            ),
        ]
    )
    port = _port(poster, max_retries=2)

    with pytest.raises(D1TransientError):
        port.execute("SELECT 1", [])

    assert port.summary()["transient_errors"] == 2
