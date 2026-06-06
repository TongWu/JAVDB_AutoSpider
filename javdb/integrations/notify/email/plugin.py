"""Email notify plugin — wraps the existing send_email primitive (ADR-039 D3).

The rich run_email_notification report path is NOT touched; this is the generic
notification adapter."""

from __future__ import annotations

from javdb.integrations.notify.email.delivery import send_email
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY


class EmailNotifyPlugin:
    name = "email"

    def is_configured(self) -> bool:
        from javdb.integrations.notify.email import _config
        user = getattr(_config, "SMTP_USER", "")
        return bool(user) and "your_email" not in user

    def send(self, message: NotifyMessage) -> NotifyResult:
        # send_email returns True/False — it swallows SMTP errors and returns
        # False (see delivery.py), raising only on unexpected errors. Honor both
        # so a failed delivery is reported as ok=False, not masked as success.
        try:
            sent = send_email(message.subject, message.body)
        except Exception as exc:
            return NotifyResult(plugin=self.name, ok=False, detail=str(exc))
        if not sent:
            return NotifyResult(plugin=self.name, ok=False, detail="send_email returned False")
        return NotifyResult(plugin=self.name, ok=True)


REGISTRY.register("notify", EmailNotifyPlugin())
