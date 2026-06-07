# tests/harness/fake_smtp.py
"""Capture emails instead of sending them (ADR-037 Phase 2).

Drop-in for ``javdb.integrations.notify.email.service.send_email``: same
``(subject, body, attachments=None, dry_run=False) -> bool`` shape. Records
every call so a scenario can assert what the pipeline would email — no SMTP."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SentEmail:
    subject: str
    body: str
    attachments: tuple = ()
    dry_run: bool = False


class FakeSMTP:
    def __init__(self, *, succeed: bool = True) -> None:
        self._succeed = succeed
        self.sent: list[SentEmail] = []

    def send_email(self, subject, body, attachments=None, dry_run=False, **kwargs) -> bool:
        self.sent.append(SentEmail(subject, body, tuple(attachments or ()), bool(dry_run)))
        return self._succeed
