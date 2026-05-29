from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EmailNotificationOptions:
    csv_path: str | None = None
    mode: Literal["daily", "adhoc"] = "daily"
    dry_run: bool = False
    from_pipeline: bool = False
    session_id: str | None = None
    verify_jsonl: str | None = None
    health_snapshot: str | None = None
