from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RcloneManagerResult:
    exit_code: int
    mode: str = ""
    error_reason: str | None = None
