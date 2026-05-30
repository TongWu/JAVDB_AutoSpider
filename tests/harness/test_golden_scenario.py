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
