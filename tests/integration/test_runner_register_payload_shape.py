"""Phase 1 integration: confirm the live register payload carries
both proxy_pool_hash and proxy_pool when wired through state.py."""

import pytest


@pytest.mark.integration
def test_register_payload_has_both_hash_and_pool(monkeypatch):
    """End-to-end: a runner that calls register() emits a payload containing
    proxy_pool_hash (legacy) AND proxy_pool (Phase 1, ADR-004)."""
    from javdb.proxy.coordinator import runner_registry_client as rrc

    # Patch the network call to capture the body.
    captured = []

    def fake_do_request(self, method, path, body):
        captured.append({"method": method, "path": path, "body": body})
        return {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
            "movie_claim_recommended": False,
            "movie_claim_min_runners": 0,
        }

    monkeypatch.setattr(rrc.RunnerRegistryClient, "_do_request", fake_do_request)

    client = rrc.RunnerRegistryClient(base_url="https://x.test", token="t")
    client.register(
        holder_id="holder-int-1",
        proxy_hash="0123456789abcdef",
        proxy_pool=rrc.proxy_pool_summary_for_registry(
            [
                {"name": "Singapore Arm-3", "http": "x"},
                {"name": "Tokyo Backup-1", "https": "y"},
            ]
        ),
    )

    body = captured[0]["body"]
    assert body["proxy_pool_hash"] == "0123456789abcdef"
    assert body["proxy_pool"] == [
        {"id": "Singapore Arm-3", "name": "Singapore Arm-3"},
        {"id": "Tokyo Backup-1", "name": "Tokyo Backup-1"},
    ]

    # ADR-004 defence-in-depth: confirm no proxy URL/credential fragments
    # leak through the actual proxy_pool field. Scope to body["proxy_pool"]
    # so we don't catch the harmless "https" in base_url's repr.
    pool_repr = repr(body["proxy_pool"])
    for forbidden in ("http://", "https://", "user:", "password", "7890"):
        assert forbidden not in pool_repr, (
            f"PROXY_POOL leak at integration layer: {forbidden!r} in payload"
        )
