from __future__ import annotations

from apps.cli.notify.email import options_from_args, parse_args
from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult


def test_email_options_defaults():
    options = EmailNotificationOptions()

    assert options.csv_path is None
    assert options.mode == "daily"
    assert options.dry_run is False
    assert options.from_pipeline is False
    assert options.session_id is None
    assert options.verify_jsonl is None
    assert options.health_snapshot is None


def test_email_cli_maps_flags_to_options():
    options = options_from_args(
        parse_args([
            "--mode",
            "adhoc",
            "--csv-path",
            "reports/AdHoc/file.csv",
            "--dry-run",
            "--from-pipeline",
            "--session-id",
            "42",
            "--verify-jsonl",
            "reports/D1/d1_drift.jsonl",
            "--health-snapshot",
            "reports/D1/pending_health_24h.json",
        ])
    )

    assert options.mode == "adhoc"
    assert options.csv_path == "reports/AdHoc/file.csv"
    assert options.dry_run is True
    assert options.from_pipeline is True
    assert options.session_id == "42"
    assert options.verify_jsonl == "reports/D1/d1_drift.jsonl"
    assert options.health_snapshot == "reports/D1/pending_health_24h.json"


def test_email_result_exit_code_for_smtp_failure():
    result = EmailNotificationResult(email_sent=False, dry_run=False, subject="subject")

    assert result.exit_code == 2


def test_email_result_exit_code_for_dry_run():
    result = EmailNotificationResult(email_sent=False, dry_run=True, subject="subject")

    assert result.exit_code == 0
