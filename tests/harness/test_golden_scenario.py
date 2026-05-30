from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario
from tests.harness.scenarios.golden_daily import golden_daily


def test_golden_daily_run_writes_two_movies(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    # The spider fetched the index + both detail pages from the cassette; no
    # cassette miss for the index page we authored.
    assert all("page=" not in m for m in result.http.misses)

    # Authoritative outcome: two movies materialized into history after commit.
    assert pipeline_harness.history().count() == 2

    # Two torrents were queued into the fake qB.
    assert len(result.qb.all_hashes()) == 2


def test_uploader_failure_blocks_commit(pipeline_harness):
    # qB accepts the connection/login but rejects every magnet, so the uploader
    # reports a nonzero exit_code (all adds failed).
    base = golden_daily()
    scenario = PipelineScenario(pages=base.pages, qb=FakeQBConfig(fail_adds=True))
    result = pipeline_harness.run_daily(scenario)

    assert result.uploader_result.exit_code != 0
    # Production gates "Mark sessions as committed" on uploader success
    # (DailyIngestion.yml `if: ${{ success() }}`); the harness mirrors that, so
    # commit must NOT run and pending rows stay un-drained.
    assert result.commit_result is None
    assert pipeline_harness.history().count() == 0
    assert result.qb.all_hashes() == set()
