"""Tests for :func:`runtime.state.setup_movie_claim_client` (P1-B).

Covers the same fail-open contract verified in
``test_movie_claim_client.py`` but exercised via the spider's own
factory wrapper (which reads from ``cfg`` instead of the env).  Locks
the "未配置时行为等同今天" guarantee at the integration boundary the
spider actually invokes.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import packages.python.javdb_spider.runtime.state as state  # noqa: E402
from packages.python.javdb_platform.movie_claim_client import (  # noqa: E402
    MovieClaimClient,
)


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    """Force a clean factory state for every test."""
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    yield
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)


def _patch_cfg(monkeypatch, **values):
    """Patch ``config_helper.cfg`` to return values from *values* dict."""
    from packages.python.javdb_platform import config_helper

    def fake_cfg(name, default=""):
        return values.get(name, default)

    monkeypatch.setattr(config_helper, "cfg", fake_cfg)


def test_returns_none_when_movie_claim_disabled(monkeypatch):
    """Default OFF: no MOVIE_CLAIM_ENABLED → no client, no log spam."""
    _patch_cfg(monkeypatch, PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    assert state.setup_movie_claim_client() is None
    assert state.global_movie_claim_client is None


def test_returns_none_when_movie_claim_explicitly_false(monkeypatch):
    _patch_cfg(monkeypatch, PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t", MOVIE_CLAIM_ENABLED="false")
    assert state.setup_movie_claim_client() is None


def test_returns_none_when_url_unset_even_if_enabled(monkeypatch):
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_TOKEN="t")
    assert state.setup_movie_claim_client() is None


def test_returns_none_when_token_unset_even_if_enabled(monkeypatch):
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord.test")
    assert state.setup_movie_claim_client() is None


def test_returns_none_when_health_check_fails(monkeypatch):
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=False), \
            patch.object(MovieClaimClient, "close") as close_mock:
        assert state.setup_movie_claim_client() is None
    close_mock.assert_called_once()


def test_returns_client_when_fully_configured_and_healthy(monkeypatch):
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = state.setup_movie_claim_client()
    assert client is not None
    assert isinstance(client, MovieClaimClient)
    assert state.global_movie_claim_client is client
    client.close()


def test_setup_is_idempotent(monkeypatch):
    """Repeated calls return the same client without re-running health check."""
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=True) as hc:
        first = state.setup_movie_claim_client()
        second = state.setup_movie_claim_client()
    assert first is second
    # health_check called exactly once (first invocation only).
    assert hc.call_count == 1
    first.close()


def test_setup_does_not_clobber_unrelated_env_vars(monkeypatch):
    """Setup uses its own copy of env vars, doesn't mutate ``os.environ``."""
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "EXTERNAL")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "EXTERNAL_TOKEN")
    monkeypatch.delenv("MOVIE_CLAIM_ENABLED", raising=False)
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord-from-cfg.test",
               PROXY_COORDINATOR_TOKEN="cfg-token")

    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = state.setup_movie_claim_client()
    assert client is not None
    # Original env vars must be restored after factory delegation.
    assert os.environ["PROXY_COORDINATOR_URL"] == "EXTERNAL"
    assert os.environ["PROXY_COORDINATOR_TOKEN"] == "EXTERNAL_TOKEN"
    assert "MOVIE_CLAIM_ENABLED" not in os.environ
    client.close()
