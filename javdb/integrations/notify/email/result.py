from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EmailNotificationResult:
    email_sent: bool
    dry_run: bool
    subject: str
    has_critical_errors: bool = False
    attachments: Sequence[str] = field(default_factory=tuple)
    cleanup_errors: Sequence[str] = field(default_factory=tuple)
    # ADR-039 D4: a compact plaintext digest of the run for *secondary* notify
    # backends (e.g. Telegram). The rich HTML report stays email-only; this is
    # what the dispatcher fans out as the NotifyMessage body.
    summary: str = ""
    # ADR-039 D4: whether SMTP delivery was attempted. When 'email' is not an
    # active NOTIFY_BACKENDS entry the caller passes deliver=False, so a skipped
    # send (email_sent=False) is expected — NOT a failure — and must not map to
    # exit code 2.
    deliver: bool = True

    @property
    def exit_code(self) -> int:
        if self.deliver and not self.dry_run and not self.email_sent:
            return 2
        return 0
