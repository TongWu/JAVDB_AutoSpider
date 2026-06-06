import javdb.integrations.notify.email.plugin as email_plugin
from javdb.integrations.notify.plugin import NotifyMessage


def test_send_delegates_to_send_email(monkeypatch):
    calls = {}

    def _ok(subject, body, **kw):
        calls.update(subject=subject, body=body)
        return True   # send_email returns True on success

    monkeypatch.setattr(email_plugin, "send_email", _ok)
    plugin = email_plugin.EmailNotifyPlugin()
    result = plugin.send(NotifyMessage(subject="Run failed", body="details"))
    assert calls == {"subject": "Run failed", "body": "details"}
    assert result.plugin == "email"
    assert result.ok is True


def test_send_reports_failure_when_send_email_returns_false(monkeypatch):
    # send_email swallows SMTP errors and returns False (delivery.py) — the
    # plugin must surface that as ok=False, not treat "no exception" as success.
    monkeypatch.setattr(email_plugin, "send_email", lambda *a, **k: False)
    result = email_plugin.EmailNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False


def test_send_reports_failure_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(email_plugin, "send_email", _boom)
    result = email_plugin.EmailNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False
    assert "smtp down" in (result.detail or "")
