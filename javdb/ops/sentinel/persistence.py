# javdb/ops/sentinel/persistence.py
"""D1-canonical persistence wiring for the drift sentinel (ADR-035)."""

from __future__ import annotations

import contextlib
import json

from javdb.ops.diagnosis.models import OpsIncidentRecord, build_incident_id
from javdb.ops.sentinel.models import SentinelVerdict, utc_now_iso
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo


@contextlib.contextmanager
def open_fill_repo():
    with get_db(REPORTS_DB_PATH) as conn:
        yield ParseRunFieldFillRepo(conn)


@contextlib.contextmanager
def open_incident_repo():
    with get_db(REPORTS_DB_PATH) as conn:
        yield OpsIncidentRepo(conn)


def build_drift_incident(
    verdict: SentinelVerdict, *, session_id: str | None,
    run_id: str | None, run_attempt: int | None,
) -> OpsIncidentRecord:
    now = utc_now_iso()
    findings = [
        {"page_type": f.page_type, "field": f.field, "severity": f.severity,
         "fill_rate": f.fill_rate, "threshold": f.threshold, "baseline": f.baseline}
        for f in verdict.findings
    ]
    confidence = "high" if verdict.critical else "medium"
    actions = (["Inspect the parser/selectors; the commit was gated."]
               if verdict.critical else ["Inspect the soft-field selector; run committed."])
    return OpsIncidentRecord(
        incident_id=build_incident_id(
            trigger_source="sentinel", run_id=run_id, run_attempt=run_attempt,
            session_id=session_id, incident_type="site_drift",
        ),
        trigger_source="sentinel",
        run_id=run_id,
        run_attempt=run_attempt,
        session_id=session_id,
        incident_type="site_drift",
        status="open",
        # Built pre-persist; the service flips this to "d1_written" only after a
        # successful upsert (mirrors OpsIncidentRecord.from_bundle_and_result).
        persistence_status="not_written",
        model_version="n/a",
        detector_version="sentinel-v1",
        bundle_schema_version="n/a",
        confidence=confidence,
        confirmed_findings_json=json.dumps(findings, ensure_ascii=False),
        likely_causes_json="[]",
        unknowns_json="[]",
        recommended_next_actions_json=json.dumps(actions, ensure_ascii=False),
        unsafe_actions_json="[]",
        evidence_refs_json="[]",
        created_at=now,
        updated_at=now,
        resolved_at=None,
    )
