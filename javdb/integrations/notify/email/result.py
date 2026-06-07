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

    @property
    def exit_code(self) -> int:
        if not self.dry_run and not self.email_sent:
            return 2
        return 0
