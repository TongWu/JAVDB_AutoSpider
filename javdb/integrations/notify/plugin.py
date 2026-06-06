"""The notify-category plugin contract (ADR-039 D2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class NotifyMessage:
    subject: str
    body: str
    level: str = "info"   # info | warning | error


@dataclass
class NotifyResult:
    plugin: str
    ok: bool
    detail: Optional[str] = None


class NotifyPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def send(self, message: NotifyMessage) -> NotifyResult: ...
