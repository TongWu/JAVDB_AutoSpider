from __future__ import annotations

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult


def run_email_notification(options: EmailNotificationOptions) -> EmailNotificationResult:
    from javdb.integrations.notify.email import _legacy

    return _legacy.run_email_notification_from_options(options)
