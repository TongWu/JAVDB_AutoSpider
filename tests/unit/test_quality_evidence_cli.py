from __future__ import annotations

import importlib
from unittest.mock import patch


def _import_cli():
    return importlib.import_module("apps.cli.qb.quality_evidence")


def _summary(**overrides):
    summary = {
        "scanned": 0,
        "skipped": 0,
        "evidence_written": 0,
        "evaluations_written": 0,
        "probe_unavailable": 0,
    }
    summary.update(overrides)
    return summary


def _set_config(monkeypatch, cli, *, enabled=False, categories=""):
    values = {
        "TORRENT_QUALITY_EVIDENCE_ENABLED": enabled,
        "TORRENT_QUALITY_CATEGORIES": categories,
    }
    monkeypatch.setattr(cli, "cfg", lambda key, default=None: values.get(key, default))


def test_disabled_by_default_exits_zero_without_running_collection(monkeypatch, capsys):
    cli = _import_cli()
    _set_config(monkeypatch, cli)

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main([])

    assert rc == 0
    run.assert_not_called()
    assert "disabled" in capsys.readouterr().out


def test_enabled_runs_collection(monkeypatch):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=True)

    with patch.object(cli, "run_collection", return_value=_summary(scanned=1)) as run:
        rc = cli.main(["--days", "3", "--categories", '["Daily Ingestion"]'])

    assert rc == 0
    run.assert_called_once_with(
        days=3,
        categories=["Daily Ingestion"],
        use_proxy=None,
    )


def test_force_overrides_disabled_gate(monkeypatch):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=False)

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main(["--force"])

    assert rc == 0
    run.assert_called_once()


def test_categories_omitted_falls_back_to_config(monkeypatch):
    cli = _import_cli()
    _set_config(
        monkeypatch,
        cli,
        enabled=True,
        categories='  ["Daily Ingestion", "Ad Hoc"]  ',
    )

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main([])

    assert rc == 0
    assert run.call_args.kwargs["categories"] == ["Daily Ingestion", "Ad Hoc"]


def test_cli_categories_override_config(monkeypatch):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=True, categories='["Daily Ingestion"]')

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main(["--categories", '["Ad Hoc"]'])

    assert rc == 0
    assert run.call_args.kwargs["categories"] == ["Ad Hoc"]
