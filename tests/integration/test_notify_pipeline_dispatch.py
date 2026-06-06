"""ADR-039 D4 integration: a configured second backend is invoked on a run.

Exercises the *real* notify dispatcher and the *real* ``TelegramNotifyPlugin``
(only the HTTP transport is mocked) through the actual pipeline notification
entrypoint — ``apps.cli.notify.email.main`` — which is the step the pipeline
service subprocess and the GitHub Actions workflows run. The rich email path
also runs (SMTP + heavy file/DB collaborators stubbed) so we prove the email
report is not regressed when a second backend is added.
"""

from __future__ import annotations

import pytest

import apps.cli.notify.email as cli
from javdb.integrations.notify import dispatch
from javdb.integrations.notify.telegram import plugin as tg


def _stub_email_collaborators(monkeypatch):
    """Stub the heavy collaborators run_email_notification touches (mirror P0).

    Returns a dict that records the subject ``send_email`` was called with, so a
    test can assert the rich email path ran.
    """
    from javdb.integrations.notify.email import service as en

    email_sent = {}

    def _capture_send_email(subject, body, attachments=None, dry_run=False, session_id=None):
        email_sent["subject"] = subject
        return True

    monkeypatch.setattr(en, "send_email", _capture_send_email)
    monkeypatch.setattr(en, "_resolve_default_verify_jsonl", lambda x: None)
    monkeypatch.setattr(en, "_resolve_default_health_snapshot", lambda x: None)
    monkeypatch.setattr(en, "find_proxy_ban_html_files", lambda d=None: [])
    monkeypatch.setattr(en, "extract_proxy_ban_summary", lambda *a, **k: {})
    monkeypatch.setattr(en, "analyze_spider_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_uploader_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pikpak_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pipeline_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "check_workflow_job_status", lambda: (False, []))
    monkeypatch.setattr(en, "extract_spider_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_uploader_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_pikpak_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_dedup_statistics", lambda *a, **k: None)
    monkeypatch.setattr(en, "find_latest_adhoc_csv", lambda d: None)
    monkeypatch.setattr(en, "find_latest_daily_csv", lambda d: None)
    monkeypatch.setattr(en, "_load_pending_verify_records", lambda *a, **k: [])
    return email_sent


def _capture_telegram(monkeypatch):
    """Configure the real telegram plugin + capture its Bot API POST."""
    monkeypatch.setattr(
        tg, "cfg",
        lambda name, default: {"TELEGRAM_BOT_TOKEN": "TKN", "TELEGRAM_CHAT_ID": "CHAT"}.get(name, default),
    )
    posted = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        posted["url"] = url
        posted["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(tg.requests, "post", _post)
    return posted


@pytest.mark.integration
def test_second_backend_invoked_alongside_email(monkeypatch):
    """NOTIFY_BACKENDS=['email','telegram']: email rich report + telegram ping."""
    monkeypatch.setenv("JAVDB_FORBID_DB_WRITES", "1")  # hermetic: no DB writes
    email_sent = _stub_email_collaborators(monkeypatch)
    posted = _capture_telegram(monkeypatch)
    monkeypatch.setattr(dispatch, "active_names", lambda: ["email", "telegram"])

    code = cli.main(["--mode", "daily"])

    assert code == 0
    # Rich email path still ran (no regression) ...
    assert email_sent, "rich email path did not run"
    # ... and the second backend was actually invoked on this run.
    assert posted, "telegram backend was not invoked"
    assert "botTKN/sendMessage" in posted["url"]
    assert posted["json"]["chat_id"] == "CHAT"
    assert "JavDB" in posted["json"]["text"]  # the run verdict subject rode through


@pytest.mark.integration
def test_telegram_only_invokes_telegram_and_suppresses_email(monkeypatch):
    """NOTIFY_BACKENDS=['telegram']: telegram pinged, email NOT delivered."""
    monkeypatch.setenv("JAVDB_FORBID_DB_WRITES", "1")
    email_sent = _stub_email_collaborators(monkeypatch)
    posted = _capture_telegram(monkeypatch)
    monkeypatch.setattr(dispatch, "active_names", lambda: ["telegram"])

    code = cli.main(["--mode", "daily"])

    assert code == 0
    assert posted, "telegram backend was not invoked"
    assert not email_sent, "email must NOT be delivered when 'email' is not an active backend"
