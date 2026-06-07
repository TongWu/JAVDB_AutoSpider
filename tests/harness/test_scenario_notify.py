# tests/harness/test_scenario_notify.py
"""ADR-037 Phase 2: the daily pipeline's email is produced through a faked SMTP."""

from tests.harness.scenarios.golden_daily import golden_daily


def test_daily_notify_email_is_captured(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    notify_result = pipeline_harness.run_notify(
        result.spider_result.csv_path, result.spider_result.session_id,
    )

    # Exactly one email went through the faked SMTP seam, with a real subject.
    assert pipeline_harness.smtp is not None
    assert len(pipeline_harness.smtp.sent) == 1
    assert pipeline_harness.smtp.sent[0].subject
    assert notify_result.email_sent is True
