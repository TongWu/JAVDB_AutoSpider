from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


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


def test_disabled_with_malformed_config_categories_exits_zero_without_running_collection(
    monkeypatch,
):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=False, categories="not-json")

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main([])

    assert rc == 0
    run.assert_not_called()


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


def test_blank_config_categories_resolves_to_none_when_enabled(monkeypatch):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=True, categories="   ")

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main([])

    assert rc == 0
    assert run.call_args.kwargs["categories"] is None


def test_cli_categories_override_config(monkeypatch):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=True, categories='["Daily Ingestion"]')

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main(["--categories", '["Ad Hoc"]'])

    assert rc == 0
    assert run.call_args.kwargs["categories"] == ["Ad Hoc"]


def test_invalid_config_categories_when_enabled_raises_system_exit(monkeypatch):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=True, categories="not-json")

    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("--use-proxy", True),
        ("--no-proxy", False),
    ],
)
def test_proxy_override_forwarding(monkeypatch, flag, expected):
    cli = _import_cli()
    _set_config(monkeypatch, cli, enabled=True)

    with patch.object(cli, "run_collection", return_value=_summary()) as run:
        rc = cli.main([flag])

    assert rc == 0
    assert run.call_args.kwargs["use_proxy"] is expected
