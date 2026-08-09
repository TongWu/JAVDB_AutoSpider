"""Lightweight ADR-026 incident analytics."""

from __future__ import annotations

from collections import Counter

from javdb.ops.diagnosis.models import OpsIncidentRecord


def _counter_dict(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))


def summarize_incidents(records: list[OpsIncidentRecord]) -> dict:
    return {
        "total": len(records),
        "by_type": _counter_dict([record.incident_type for record in records]),
        "by_status": _counter_dict([record.status for record in records]),
        "by_confidence": _counter_dict([record.confidence for record in records]),
        "open_high_confidence": sum(
            1 for record in records
            if record.status == "open" and record.confidence == "high"
        ),
    }
