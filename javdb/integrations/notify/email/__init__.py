"""Email notification service package."""

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult
from javdb.integrations.notify.email.service import run_email_notification
from javdb.integrations.notify.email.delivery import send_email

__all__ = [
    "EmailNotificationOptions",
    "EmailNotificationResult",
    "run_email_notification",
    "send_email",
]
