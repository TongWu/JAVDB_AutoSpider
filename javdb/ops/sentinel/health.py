# javdb/ops/sentinel/health.py
"""Read-only per-field health view for the drift surface (ADR-035 Phase 3).

Annotates the latest committed fill per contract field with severity, baseline,
threshold and a status string. Pure: no DB access (baseline is injected); reuses
the Phase-1 PARSE_CONTRACT as the single source of truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from javdb.spider.parse_contract import fields_for

BaselineFn = Callable[[str, str], Optional[float]]


@dataclass(frozen=True)
class FieldHealth:
    page_type: str
    field: str
    severity: str  # 'critical' | 'soft'
    fill_rate: float
    sample_count: int
    observed_at: Optional[str]
    baseline: Optional[float]
    threshold: Optional[float]
    status: str  # ok | critical_drift | soft_drift | no_baseline | insufficient_sample


def _status(spec: dict, fill_rate: float, sample_count: int,
            baseline: Optional[float], min_sample: int) -> tuple[str, Optional[float]]:
    if sample_count < min_sample:
        return "insufficient_sample", None
    if spec["severity"] == "critical":
        threshold = spec["min_fill"]
        return ("critical_drift" if fill_rate < threshold else "ok"), threshold
    # soft
    if baseline is None:
        return "no_baseline", None
    threshold = spec["baseline_rel"] * baseline
    return ("soft_drift" if fill_rate < threshold else "ok"), threshold


def compute_field_health(
    rows: Iterable[tuple], *, baseline_fn: BaselineFn, min_sample: int,
) -> list[FieldHealth]:
    """Annotate latest-fill rows with contract status.

    rows: iterable of (page_type, field, fill_rate, sample_count, observed_at)."""
    out: list[FieldHealth] = []
    for page_type, field_name, fill_rate, sample_count, observed_at in rows:
        spec = fields_for(page_type).get(field_name)
        if spec is None:
            continue  # field not in the contract — nothing to judge
        baseline = baseline_fn(page_type, field_name) if spec["severity"] == "soft" else None
        status, threshold = _status(spec, fill_rate, sample_count, baseline, min_sample)
        out.append(FieldHealth(
            page_type=page_type, field=field_name, severity=spec["severity"],
            fill_rate=fill_rate, sample_count=sample_count, observed_at=observed_at,
            baseline=baseline, threshold=threshold, status=status))
    return out
