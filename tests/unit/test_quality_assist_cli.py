"""ADR-024 IMP-08 Task 6: quality_assist CLI double-gate."""

from __future__ import annotations

from unittest.mock import patch

from apps.cli.qb import quality_assist as cli


def _all_gates_on(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_assist_mode", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)


def test_evidence_disabled_skips(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    monkeypatch.setattr(cli, "_assist_mode", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_assist") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_policy_not_assist_skips(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_assist_mode", lambda: False)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_assist") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_probe_disabled_skips(monkeypatch):
    # Assist needs probe runner-up evidence — without QUALITY_PROBE_ENABLED it skips.
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_assist_mode", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: False)
    with patch.object(cli, "run_assist") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_all_gates_on_runs_with_days(monkeypatch):
    _all_gates_on(monkeypatch)
    with patch.object(cli, "run_assist", return_value={"movies": 1, "candidates": 2, "would_replace": 1}) as run:
        assert cli.main([]) == 0
    run.assert_called_once()
    assert run.call_args.kwargs["days"] == 2  # default look-back window


def test_custom_days_is_passed_through(monkeypatch):
    _all_gates_on(monkeypatch)
    with patch.object(cli, "run_assist", return_value={"movies": 0, "candidates": 0, "would_replace": 0}) as run:
        assert cli.main(["--days", "7"]) == 0
    assert run.call_args.kwargs["days"] == 7


def test_force_overrides_gates(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    monkeypatch.setattr(cli, "_assist_mode", lambda: False)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: False)
    with patch.object(cli, "run_assist", return_value={"movies": 0, "candidates": 0, "would_replace": 0}) as run:
        assert cli.main(["--force"]) == 0
    run.assert_called_once()
