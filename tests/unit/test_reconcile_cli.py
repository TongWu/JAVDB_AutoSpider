from __future__ import annotations

from dataclasses import asdict
import json
import logging

import pytest

from javdb.ops.reconcile.models import ReconcileResult

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
    caplog.set_level(logging.WARNING)

    rc = reconcile_cli.main(["--json"])

    assert rc == 0
    assert captured["options"].stalled_after_days == 7
    captured = capsys.readouterr()
    assert captured.out.strip() == json.dumps(asdict(ReconcileResult()), ensure_ascii=False)
    assert captured.err == ""
    assert [r.message for r in caplog.records] == ["Invalid RECONCILE_STALLED_DAYS; falling back to 7"]


def test_main_json_emits_payload_and_returns_zero(monkeypatch, capsys):
    expected = ReconcileResult(
        observed=3,
        outcomes_updated=2,
        marked_downloading=1,
        marked_completed=1,
        marked_stalled=0,
        marked_failed=0,
        errors=[],
    )

    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", lambda options: expected)

    rc = reconcile_cli.main(["--json"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip() == json.dumps(
        asdict(expected),
        ensure_ascii=False,
    )


def test_main_text_summary_uses_structured_logging_helpers(monkeypatch):
    result = ReconcileResult(
        observed=3,
        outcomes_updated=2,
        marked_downloading=1,
        marked_completed=1,
        marked_stalled=0,
        marked_failed=0,
        errors=["source down"],
    )
    sections = []
    summaries = []

    monkeypatch.setattr(reconcile_cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(reconcile_cli, "run", lambda options: result)
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
    assert sections == ["Acquisition Outcome Reconcile"]
    assert summaries == [("Reconcile Summary", {
        "Observed": 3,
        "Outcomes updated": 2,
        "Marked downloading": 1,
        "Marked completed": 1,
        "Marked stalled": 0,
        "Marked failed": 0,
        "Errors": 1,
    })]


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


def test_parser_rejects_nonpositive_stalled_after_days():
    parser = reconcile_cli._build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--stalled-after-days", "0"])

    assert exc.value.code == 2
