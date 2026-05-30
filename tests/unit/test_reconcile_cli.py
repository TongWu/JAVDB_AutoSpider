from __future__ import annotations

import json

from javdb.ops.reconcile.models import ReconcileResult

from apps.cli.ops import reconcile as reconcile_cli


def test_default_stalled_after_days_falls_back_on_bad_config(monkeypatch):
    monkeypatch.setattr(reconcile_cli, "cfg", lambda *args, **kwargs: "not-an-int")

    assert reconcile_cli._default_stalled_after_days() == 7


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
        {
            "observed": 3,
            "outcomes_updated": 2,
            "marked_downloading": 1,
            "marked_completed": 1,
            "marked_stalled": 0,
            "marked_failed": 0,
            "errors": [],
        },
        ensure_ascii=False,
    )


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
