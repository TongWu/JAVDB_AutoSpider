"""Email notification service package."""

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult
from javdb.integrations.notify.email.service import run_email_notification

# Retained programmatic entry point: a non-CLI helper with live production
# callers (apps.api.routers.operations imports it, tests patch it at this path).
from javdb.integrations.notify.email.delivery import send_email

__all__ = [
    "EmailNotificationOptions",
    "EmailNotificationResult",
    "run_email_notification",
    "send_email",
]
