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
    """Force a clean factory state for every test.

    The auto-toggle adds three module-level singletons that shadow the
    old ``global_movie_claim_client``-only singleton; reset them all so
    a previous test's ``force_on`` doesn't leak ``mode='force_on'``
    into a later test that asserts ``mode='auto'`` etc.
    """
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_client_pending", None, raising=False)
    monkeypatch.setattr(
        state, "_movie_claim_mode",
        state.MOVIE_CLAIM_MODE_OFF, raising=False,
    )
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False, raising=False)
    yield
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_client_pending", None, raising=False)
    monkeypatch.setattr(
        state, "_movie_claim_mode",
        state.MOVIE_CLAIM_MODE_OFF, raising=False,
    )
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False, raising=False)


def _patch_cfg(monkeypatch, **values):
    """Patch ``config_helper.cfg`` to return values from *values* dict."""
    from packages.python.javdb_platform import config_helper

    def fake_cfg(name, default=""):
        return values.get(name, default)

    monkeypatch.setattr(config_helper, "cfg", fake_cfg)


def test_returns_none_when_movie_claim_disabled(monkeypatch):
    """Explicit OFF: MOVIE_CLAIM_ENABLED=false → no client, no log spam."""
    _patch_cfg(monkeypatch, PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t", MOVIE_CLAIM_ENABLED="false")
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


def test_setup_reuses_pending_client_after_auto_unmount(monkeypatch):
    """Auto mode can unmount global while keeping the pending client alive."""
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="auto",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=True) as hc:
        client = state.setup_movie_claim_client()
        state._apply_movie_claim_recommendation(False)
        again = state.setup_movie_claim_client()

    assert again is client
    assert state._movie_claim_client_pending is client
    assert state.global_movie_claim_client is None
    assert hc.call_count == 1
    client.close()


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


# ── tri-state / auto-toggle behaviour ──────────────────────────────────────


def test_setup_auto_mode_optimistically_mounts_global_and_pending(monkeypatch):
    """Auto mode + healthy /health → mode=auto, pending=client, global=client.

    Optimistic mounting is the contract: the runner's first detail page
    must coordinate with peers immediately, even though the registry
    signal hasn't landed yet.  ``setup_runner_registry_client`` then
    reconciles by feeding the first ``register`` response into
    ``_apply_movie_claim_recommendation``."""
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="auto",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = state.setup_movie_claim_client()
    assert client is not None
    assert state.global_movie_claim_client is client
    assert state._movie_claim_client_pending is client
    assert state._movie_claim_mode == state.MOVIE_CLAIM_MODE_AUTO
    client.close()


def test_setup_default_unset_resolves_to_auto_mode(monkeypatch):
    """When ``MOVIE_CLAIM_ENABLED`` isn't set in config at all, the new
    default is ``auto`` (not ``off`` like before)."""
    _patch_cfg(monkeypatch, PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")  # MOVIE_CLAIM_ENABLED omitted
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = state.setup_movie_claim_client()
    assert client is not None
    assert state._movie_claim_mode == state.MOVIE_CLAIM_MODE_AUTO


def test_setup_force_on_mode_keeps_legacy_behaviour(monkeypatch):
    """``MOVIE_CLAIM_ENABLED=true`` reproduces the legacy P1-B contract:
    client is mounted on global immediately and the registry signal is
    ignored thereafter (verified separately in test_movie_claim_auto_toggle.py)."""
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = state.setup_movie_claim_client()
    assert client is not None
    assert state.global_movie_claim_client is client
    assert state._movie_claim_client_pending is client
    assert state._movie_claim_mode == state.MOVIE_CLAIM_MODE_FORCE_ON
    client.close()


def test_setup_off_mode_via_explicit_false(monkeypatch):
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="false",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    assert state.setup_movie_claim_client() is None
    assert state.global_movie_claim_client is None
    assert state._movie_claim_client_pending is None
    assert state._movie_claim_mode == state.MOVIE_CLAIM_MODE_OFF


def test_setup_off_mode_via_empty_string(monkeypatch):
    """Empty string preserves the operator intuition that ``MOVIE_CLAIM_ENABLED=``
    silences the feature, distinct from "var unset → auto"."""
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    assert state.setup_movie_claim_client() is None
    assert state._movie_claim_mode == state.MOVIE_CLAIM_MODE_OFF


def test_setup_auto_mode_health_failure_falls_back_to_off(monkeypatch):
    """Auto mode + /health 5xx → mode collapses to off + pending stays
    None; later registry signals become no-ops, identical to today."""
    _patch_cfg(monkeypatch, MOVIE_CLAIM_ENABLED="auto",
               PROXY_COORDINATOR_URL="https://coord.test",
               PROXY_COORDINATOR_TOKEN="t")
    with patch.object(MovieClaimClient, "health_check", return_value=False):
        result = state.setup_movie_claim_client()
    assert result is None
    assert state.global_movie_claim_client is None
    assert state._movie_claim_client_pending is None
    assert state._movie_claim_mode == state.MOVIE_CLAIM_MODE_OFF
