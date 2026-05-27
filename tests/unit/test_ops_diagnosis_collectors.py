from __future__ import annotations

from javdb.ops.diagnosis.collectors import collect_incident_bundle


def test_collect_bundle_reads_capped_log_snippets(tmp_path):
    log = tmp_path / "pipeline.log"
    log.write_text("ok\nERROR failed spider\nWARNING retrying\n", encoding="utf-8")

    bundle = collect_incident_bundle(
        trigger_source="manual_cli",
        run_id="123",
        run_attempt=1,
        session_id="sid",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        log_paths=[log],
    )

    assert bundle.trigger_source == "manual_cli"
    assert bundle.workflow_result == "failure"
    assert bundle.log_snippets == [
        "pipeline.log: ERROR failed spider",
        "pipeline.log: WARNING retrying",
    ]
    assert bundle.runbook_refs
