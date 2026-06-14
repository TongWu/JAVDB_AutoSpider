from __future__ import annotations

from dataclasses import asdict
import json
import logging

import pytest

from javdb.ops.reconcile.models import ConsumptionResult, OwnershipResult, ReconcileResult

from apps.cli.ops import reconcile as reconcile_cli


def test_default_stalled_after_days_falls_back_on_non_integer_config(monkeypatch):
    monkeypatch.setattr(reconcile_cli, "cfg", lambda *args, **kwargs: "not-an-int")

    assert reconcile_cli._default_stalled_after_days() == 7


def test_main_falls_back_on_nonpositive_config_default(monkeypatch, capsys, caplog):
    monkeypatch.setattr(reconcile_cli, "cfg", lambda *args, **kwargs: "0")
    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    captured = {}

    def _run(options):
        captured["options"] = options
        return ReconcileResult()

    monkeypatch.setattr(reconcile_cli, "run", _run)
    monkeypatch.setattr(reconcile_cli, "run_ownership", lambda options: OwnershipResult())
    monkeypatch.setattr(reconcile_cli, "run_consumption", lambda options: ConsumptionResult())
    monkeypatch.setattr(reconcile_cli, "parse_media_servers", lambda _raw: [])
    caplog.set_level(logging.WARNING)

    rc = reconcile_cli.main(["--json"])

    assert rc == 0
    assert captured["options"].stalled_after_days == 7
    captured = capsys.readouterr()
    expected_output = json.dumps(
        {
            "acquisition": asdict(ReconcileResult()),
            "ownership": asdict(OwnershipResult()),
            "consumption": asdict(ConsumptionResult()),
        },
        ensure_ascii=False,
    )
    assert captured.out.strip() == expected_output
    assert captured.err == ""
    assert [r.message for r in caplog.records] == ["Invalid RECONCILE_STALLED_DAYS; falling back to 7"]


def test_main_uses_configured_categories_by_default(monkeypatch, capsys):
    captured = {}

    def _cfg(name, default):
        values = {
            "TORRENT_CATEGORY": "Movies",
            "TORRENT_CATEGORY_ADHOC": "One Off",
        }
        return values.get(name, default)

    def _run(options):
        captured["options"] = options
        return ReconcileResult()

    monkeypatch.setattr(reconcile_cli, "cfg", _cfg)
    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", _run)

    rc = reconcile_cli.main(["--json"])

    assert rc == 0
    assert captured["options"].categories == ("Movies", "One Off")
    assert captured["options"].infer_absent is True
    capsys.readouterr()


def test_main_category_override_disables_absent_inference(monkeypatch, capsys):
    captured = {}

    def _run(options):
        captured["options"] = options
        return ReconcileResult()

    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", _run)

    rc = reconcile_cli.main(["--category", "Movies", "--json"])

    assert rc == 0
    assert captured["options"].categories == ("Movies",)
    assert captured["options"].infer_absent is False
    capsys.readouterr()


def test_main_json_emits_payload_and_returns_zero(monkeypatch, capsys):
    expected_acquisition = ReconcileResult(
        observed=3,
        outcomes_updated=2,
        marked_downloading=1,
        marked_completed=1,
        marked_stalled=0,
        marked_failed=0,
        errors=[],
    )
    expected_ownership = OwnershipResult()
    expected_consumption = ConsumptionResult()

    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", lambda options: expected_acquisition)
    monkeypatch.setattr(reconcile_cli, "run_ownership", lambda options: expected_ownership)
    monkeypatch.setattr(reconcile_cli, "run_consumption", lambda options: expected_consumption)
    monkeypatch.setattr(reconcile_cli, "parse_media_servers", lambda _raw: [])

    rc = reconcile_cli.main(["--json"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip() == json.dumps(
        {
            "acquisition": asdict(expected_acquisition),
            "ownership": asdict(expected_ownership),
            "consumption": asdict(expected_consumption),
        },
        ensure_ascii=False,
    )


def test_main_text_summary_uses_structured_logging_helpers(monkeypatch):
    acquisition_result = ReconcileResult(
        observed=3,
        outcomes_updated=2,
        marked_downloading=1,
        marked_completed=1,
        marked_stalled=0,
        marked_failed=0,
        errors=["source down"],
    )
    ownership_result = OwnershipResult()
    consumption_result = ConsumptionResult()
    sections = []
    summaries = []

    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", lambda options: acquisition_result)
    monkeypatch.setattr(reconcile_cli, "run_ownership", lambda options: ownership_result)
    monkeypatch.setattr(reconcile_cli, "run_consumption", lambda options: consumption_result)
    monkeypatch.setattr(reconcile_cli, "parse_media_servers", lambda _raw: [])
    monkeypatch.setattr(
        reconcile_cli,
        "log_section",
        lambda _logger, title: sections.append(title),
    )
    monkeypatch.setattr(
        reconcile_cli,
        "log_summary_block",
        lambda _logger, title, pairs: summaries.append((title, pairs)),
    )

    rc = reconcile_cli.main([])

    assert rc == 2
    assert sections == [
        "Acquisition Outcome Reconcile",
        "Ownership Ledger Reconcile",
        "Consumption Signal Reconcile",
    ]
    assert summaries == [
        ("Reconcile Summary", {
            "Observed": 3,
            "Outcomes updated": 2,
            "Marked downloading": 1,
            "Marked completed": 1,
            "Marked stalled": 0,
            "Marked failed": 0,
            "Missing files deleted from qB": 0,
            "Errors": 1,
        }),
        ("Ownership Summary", {
            "Observed": 0,
            "Upserted": 0,
            "Swept absent": 0,
            "Marked in-library": 0,
            "Errors": 0,
        }),
        ("Consumption Summary", {
            "Instances observed": 0,
            "Items observed": 0,
            "Signals updated": 0,
            "Resolved high/medium/low": "0/0/0",
            "Marked unresolved": 0,
            "Errors": 0,
        }),
    ]


def test_main_returns_nonzero_when_run_raises(monkeypatch, capsys):
    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)

    def _boom(_options):
        raise RuntimeError("boom")

    monkeypatch.setattr(reconcile_cli, "run", _boom)

    rc = reconcile_cli.main(["--json"])

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: boom" in captured.err
    assert "Traceback" not in captured.err


def test_parser_rejects_unknown_source():
    parser = reconcile_cli._build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--source", "qbb"])

    assert exc.value.code == 2


def test_main_consumption_config_error_still_emits_other_results(monkeypatch, capsys):
    """With malformed MEDIA_SERVERS, --pass all emits acquisition+ownership JSON and exits 1."""
    expected_acquisition = ReconcileResult()
    expected_ownership = OwnershipResult()

    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", lambda options: expected_acquisition)
    monkeypatch.setattr(reconcile_cli, "run_ownership", lambda options: expected_ownership)
    monkeypatch.setattr(
        reconcile_cli,
        "parse_media_servers",
        lambda _raw: (_ for _ in ()).throw(ValueError("bad config")),
    )

    rc = reconcile_cli.main(["--pass", "all", "--json"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Error: invalid MEDIA_SERVERS config: bad config" in captured.err
    parsed = json.loads(captured.out.strip())
    assert "acquisition" in parsed
    assert "ownership" in parsed
    assert "consumption" not in parsed


def test_parser_rejects_nonpositive_stalled_after_days():
    parser = reconcile_cli._build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--stalled-after-days", "0"])

    assert exc.value.code == 2
