"""Phase 1: RunnerRegistryClient.register() carries proxy_pool field (ADR-004)."""

import pytest

from packages.python.javdb_platform.runner_registry_client import (
    RunnerRegistryClient,
)


def _make_client(captured_body: list):
    """Build a client whose ``_do_request`` is stubbed to capture the outgoing body.

    Patches at the instance level (not class level) so test isolation is precise.
    Returns the minimal valid register response shape so register() doesn't
    raise during the parse path.
    """
    client = RunnerRegistryClient(base_url="https://example.test", token="t")

    def fake_do_request(method, path, body):
        captured_body.append({"method": method, "path": path, "body": body})
        return {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
            "movie_claim_recommended": False,
            "movie_claim_min_runners": 0,
        }

    client._do_request = fake_do_request  # type: ignore[assignment]
    return client


def test_register_includes_proxy_pool_when_provided():
    captured: list = []
    client = _make_client(captured)
    client.register(
        holder_id="holder-1",
        proxy_pool=[{"id": "P-1", "name": "P-1"}, {"id": "P-2", "name": "P-2"}],
    )
    assert len(captured) == 1
    assert captured[0]["body"]["proxy_pool"] == [
        {"id": "P-1", "name": "P-1"},
        {"id": "P-2", "name": "P-2"},
    ]


def test_register_omits_proxy_pool_field_when_not_provided():
    """Backward compat: callers that don't pass proxy_pool produce
    payloads identical to the pre-Phase-1 contract."""
    captured: list = []
    client = _make_client(captured)
    client.register(holder_id="holder-1")
    body = captured[0]["body"]
    assert "proxy_pool" not in body
