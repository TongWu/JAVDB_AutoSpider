# javdb/ops/sentinel/detectors.py
"""Pure drift evaluation for the site-contract sentinel (ADR-035)."""

from __future__ import annotations

from typing import Callable, Optional

from javdb.ops.sentinel.models import DriftFinding, FieldFill, SentinelVerdict
from javdb.spider.parse_contract import fields_for

BaselineFn = Callable[[str, str], Optional[float]]


def evaluate(fills: list[FieldFill], *, min_sample: int, baseline_fn: BaselineFn) -> SentinelVerdict:
    verdict = SentinelVerdict()
    for fill in fills:
        spec = fields_for(fill.page_type).get(fill.field)
        if spec is None:
            continue
        if fill.sample_count < min_sample:
            continue  # sample-size guard
        verdict.evaluated += 1
        if spec["severity"] == "critical":
            threshold = spec["min_fill"]
            if fill.fill_rate < threshold:
                verdict.critical = True
                verdict.findings.append(DriftFinding(
                    fill.page_type, fill.field, "critical", fill.fill_rate, threshold,
                ))
        else:  # soft
            baseline = baseline_fn(fill.page_type, fill.field)
            if baseline is None:
                continue  # cannot judge relative drift without a baseline
            threshold = spec["baseline_rel"] * baseline
            if fill.fill_rate < threshold:
                verdict.findings.append(DriftFinding(
                    fill.page_type, fill.field, "soft", fill.fill_rate, threshold, baseline,
                ))
    return verdict
