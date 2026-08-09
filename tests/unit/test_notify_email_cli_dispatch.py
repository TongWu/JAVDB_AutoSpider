"""ADR-039 D4: the pipeline notification CLI fans out to secondary backends.

These pin the wiring in ``apps.cli.notify.email.main`` without touching SMTP or
the real plugins: ``run_email_notification`` and the dispatcher are stubbed at
the CLI module boundary so we assert *how* main() routes, not what each backend
does (that is covered by the plugin / dispatch unit tests).
"""

from __future__ import annotations

import apps.cli.notify.email as cli
from javdb.integrations.notify.email.result import EmailNotificationResult
from javdb.integrations.notify.plugin import NotifyResult


def _stub_run(monkeypatch, *, subject="✓ SUCCESS - JavDB Daily Report", summary="Spider: 5 processed",
              has_critical_errors=False, email_sent=True, dry_run=False):
    """Stub run_email_notification; capture the deliver kwarg it was called with."""
    captured = {}

    def _fake(opts, *, deliver=True):
        captured["deliver"] = deliver
        return EmailNotificationResult(
            email_sent=email_sent,
            dry_run=dry_run,
            subject=subject,
            has_critical_errors=has_critical_errors,
            summary=summary,
        )

    monkeypatch.setattr(cli, "run_email_notification", _fake)
    return captured


def _stub_dispatch(monkeypatch, active, send_results=None):
    """Stub dispatch.active_names / dispatch.send; capture send() invocation."""
    sent = {}

    def _fake_send(message, exclude=None):
        sent["message"] = message
        sent["exclude"] = exclude
        return send_results if send_results is not None else [
            NotifyResult(plugin="telegram", ok=True)
        ]

    monkeypatch.setattr(cli.dispatch, "active_names", lambda: list(active))
    monkeypatch.setattr(cli.dispatch, "send", _fake_send)
    return sent


def test_default_email_only_does_not_fan_out(monkeypatch):
    run = _stub_run(monkeypatch)
    sent = _stub_dispatch(monkeypatch, active=["email"])

    code = cli.main(["--mode", "daily"])

    assert code == 0
    assert run["deliver"] is True            # email delivered as today
    assert sent == {}                        # no secondary fan-out


def test_email_plus_telegram_fans_out_with_exclude(monkeypatch):
    run = _stub_run(monkeypatch, subject="✓ SUCCESS - JavDB Daily Report",
                    summary="Spider: 5 processed")
    sent = _stub_dispatch(monkeypatch, active=["email", "telegram"])

    code = cli.main(["--mode", "daily"])

    assert code == 0
    assert run["deliver"] is True                                  # email still rich
    assert sent["exclude"] == {"email"}                            # email not double-notified
    assert sent["message"].subject == "✓ SUCCESS - JavDB Daily Report"
    assert sent["message"].body == "Spider: 5 processed"
    assert sent["message"].level == "info"


def test_telegram_only_suppresses_email_delivery(monkeypatch):
    run = _stub_run(monkeypatch)
    sent = _stub_dispatch(monkeypatch, active=["telegram"])

    code = cli.main(["--mode", "daily"])

    assert code == 0                          # secondary-only: exit not gated by SMTP
    assert run["deliver"] is False            # email computed but NOT sent
    assert sent["exclude"] == {"email"}
    assert sent["message"].subject.startswith("✓ SUCCESS")


def test_failure_level_and_subject_fallback_body(monkeypatch):
    # No summary → body falls back to the subject; critical errors → level error.
    _stub_run(monkeypatch, subject="✗ FAILED - JavDB Daily Report",
              summary="", has_critical_errors=True)
    sent = _stub_dispatch(monkeypatch, active=["email", "telegram"])

    cli.main(["--mode", "daily"])

    assert sent["message"].level == "error"
    assert sent["message"].body == "✗ FAILED - JavDB Daily Report"


def test_email_smtp_failure_exit_code_propagates_with_secondary(monkeypatch):
    # Email is active and its SMTP send failed (email_sent=False) → exit 2 even
    # though the telegram fan-out succeeded.
    _stub_run(monkeypatch, email_sent=False)
    _stub_dispatch(monkeypatch, active=["email", "telegram"])

    assert cli.main(["--mode", "daily"]) == 2
