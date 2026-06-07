"""ADR-037 Phase 2: critical site-contract drift gates the commit (ADR-035)."""

from javdb.ops.sentinel.models import FieldFill
from javdb.ops.sentinel.service import persist_run
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.sessions.commit import SiteContractDriftError

from tests.harness.scenarios.golden_daily import golden_daily


def _seed_critical_drift(session_id: str) -> None:
    # index.video_code is critical with min_fill=0.99; 0.10 fill over 50 samples
    # (>= the 30-row min_sample gate) is unambiguous critical drift.
    persist_run([FieldFill("index", "video_code", 0.10, 50)], session_id=session_id)


def test_drift_gate_refuses_commit(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily(), before_commit=_seed_critical_drift)

    # The ADR-035 commit gate raised, so nothing was promoted.
    assert isinstance(result.commit_error, SiteContractDriftError)
    assert result.commit_result is None
    assert pipeline_harness.history().count() == 0

    # The sentinel recorded a site_drift incident as a side effect of the gate.
    with get_db(_db.REPORTS_DB_PATH) as conn:
        incidents = conn.execute(
            "SELECT COUNT(*) FROM OpsIncidents WHERE incident_type = 'site_drift'"
        ).fetchone()[0]
    assert incidents >= 1
