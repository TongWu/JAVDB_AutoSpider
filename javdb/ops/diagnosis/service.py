"""Service orchestration for ADR-026 read-only diagnosis."""

from __future__ import annotations

from pathlib import Path

from javdb.ops.diagnosis.ai import Synthesizer, synthesize_with_configured_ai
from javdb.ops.diagnosis.detectors import detect_incident
from javdb.ops.diagnosis.models import IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.persistence import persist_incident


def diagnose_incident(
    bundle: IncidentBundle,
    *,
    synthesizer: Synthesizer | None = None,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
) -> OpsIncidentRecord:
    detector_result = detect_incident(bundle)
    result = (synthesizer or synthesize_with_configured_ai)(bundle, detector_result)
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result)
    return persist_incident(record, repo=repo, jsonl_path=jsonl_path)
