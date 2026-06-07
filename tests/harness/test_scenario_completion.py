"""ADR-037 Phase 2: completion closes the ADR-033 acquisition loop."""

from tests.harness.scenarios.golden_daily import golden_daily


def test_completion_closes_the_loop(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    # The uploader's record_queued hook staged two queued outcomes.
    queued = {o["qb_hash"]: o["state"] for o in pipeline_harness.acquisition_outcomes()}
    assert len(queued) == 2
    assert set(queued.values()) == {"queued"}

    # Simulate both downloads finishing in qB (progress=1.0, state=uploading).
    for qb_hash in result.qb.all_hashes():
        result.qb.complete(qb_hash)

    # The real reconciler closes the loop: queued -> completed.
    rec = pipeline_harness.reconcile()
    assert rec.marked_completed == 2
    assert rec.errors == []

    after = {o["qb_hash"]: o["state"] for o in pipeline_harness.acquisition_outcomes()}
    assert set(after.values()) == {"completed"}
