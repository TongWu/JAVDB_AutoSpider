from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QbUploaderOptions:
    mode: Literal["adhoc", "daily"] = "daily"
    input_file: str | None = None
    proxy_override: bool | None = None
    from_pipeline: bool = False
    category: str | None = None
    session_id: str | None = None
