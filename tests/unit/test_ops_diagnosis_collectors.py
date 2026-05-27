from __future__ import annotations

import logging

from javdb.ops.diagnosis import collectors
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


def test_collect_bundle_falls_back_when_log_snippet_limit_config_is_invalid(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(collectors, "cfg", lambda _name, _default: "not-an-int")
    log = tmp_path / "pipeline.log"
    log.write_text("ERROR failed spider\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        bundle = collect_incident_bundle(
            trigger_source="manual_cli",
            log_paths=[log],
        )

    assert bundle.log_snippets == ["pipeline.log: ERROR failed spider"]
    assert any("OPS_DIAGNOSIS_MAX_LOG_SNIPPETS" in record.message for record in caplog.records)
