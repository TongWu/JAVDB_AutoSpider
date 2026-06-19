"""ADR-024 IMP-10: quality_probe CLI double-gate."""

from __future__ import annotations

from unittest.mock import patch

from apps.cli.qb import quality_probe as cli


def test_evidence_disabled_skips(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_probe") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_probe_disabled_skips(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: False)
    with patch.object(cli, "run_probe") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_both_gates_on_runs(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_probe", return_value={"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}) as run:
        assert cli.main([]) == 0
    run.assert_called_once()


def test_force_overrides_gates(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: False)
    with patch.object(cli, "run_probe", return_value={"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}) as run:
        assert cli.main(["--force"]) == 0
    run.assert_called_once()


def test_default_passes_auto_proxy_mode(monkeypatch):
    """No --use-proxy/--no-proxy → run_probe must receive use_proxy=None (auto),
    NOT False — collapsing auto to False would force the proxy off."""
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_probe", return_value={"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}) as run:
        assert cli.main([]) == 0
    assert run.call_args.kwargs["use_proxy"] is None
