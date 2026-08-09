"""Capability-probe honesty tests for the ADR-054 ``magnet_aggregation`` flag."""

from apps.api.routers import capabilities as _caps
import javdb.integrations.indexer.dispatch as _dispatch


def test_probe_true_when_active_sources_present(monkeypatch):
    monkeypatch.setattr(_dispatch, "active_sources", lambda: ["javbus"])
    assert _caps._magnet_aggregation_enabled() is True


def test_probe_false_when_active_sources_empty(monkeypatch):
    monkeypatch.setattr(_dispatch, "active_sources", lambda: [])
    assert _caps._magnet_aggregation_enabled() is False


def test_probe_false_when_dispatcher_errors(monkeypatch):
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(_dispatch, "active_sources", _raise)
    assert _caps._magnet_aggregation_enabled() is False


def test_served_capability_reflects_active_sources(monkeypatch):
    monkeypatch.setattr(_dispatch, "active_sources", lambda: ["sukebei"])
    assert _caps.build_capabilities().features.magnet_aggregation is True

    monkeypatch.setattr(_dispatch, "active_sources", lambda: [])
    assert _caps.build_capabilities().features.magnet_aggregation is False
