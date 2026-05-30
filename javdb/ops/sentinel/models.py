"""Typed contracts for the site-contract drift sentinel (ADR-035 Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

Severity = Literal["critical", "soft"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FieldFill:
    page_type: str
    field: str
    fill_rate: float
    sample_count: int


@dataclass(frozen=True)
class DriftFinding:
    page_type: str
    field: str
    severity: Severity
    fill_rate: float
    threshold: float
    baseline: Optional[float] = None


@dataclass
class SentinelVerdict:
    critical: bool = False
    findings: list[DriftFinding] = field(default_factory=list)
    evaluated: int = 0


@dataclass
class SentinelOptions:
    min_sample: int = 30
    baseline_window: int = 14
