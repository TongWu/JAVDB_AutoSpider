"""Service orchestration for ADR-026 read-only diagnosis."""

from __future__ import annotations

from pathlib import Path

from javdb.ops.diagnosis.ai import Synthesizer, synthesize_with_configured_ai
from javdb.ops.diagnosis.detectors import detect_incident
from javdb.ops.diagnosis.features import build_incident_features
from javdb.ops.diagnosis.models import IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.persistence import persist_incident
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


def _persist_incident_features(bundle: IncidentBundle, record: OpsIncidentRecord, repo: object | None) -> None:
    features = build_incident_features(record, bundle=bundle)
    if repo is not None and hasattr(repo, "upsert_features"):
        repo.upsert_features(features)
        return
    with get_db(REPORTS_DB_PATH) as conn:
        OpsIncidentRepo(conn).upsert_features(features)


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
    persisted = persist_incident(record, repo=repo, jsonl_path=jsonl_path)
    if persisted.persistence_status == "d1_written":
        _persist_incident_features(bundle, persisted, repo)
    return persisted
