"""Unit tests for the dynamic page-scan policy (ADR-057)."""

from javdb.spider.fetch.page_scan import PageScanPolicy


def _policy(floor=3, max_page=10, stop_after=2, enabled=True):
    return PageScanPolicy(
        floor_page=floor, max_page=max_page,
        stop_after=stop_after, enabled=enabled,
    )


class TestDisabledPolicy:
    def test_stops_at_the_floor_exactly_like_a_fixed_range(self):
        policy = _policy(enabled=False)
        for page in (1, 2):
            policy.observe(page, fresh=40)
            assert policy.should_continue_after(page) is True
        policy.observe(3, fresh=40)
        assert policy.should_continue_after(3) is False
        assert policy.stop_reason == 'floor'


class TestFloor:
    def test_a_fresh_free_page_below_the_floor_never_stops_the_scan(self):
        # D3: the configured range is a floor, so an empty page 2 of 1..3 is
        # still followed by page 3.
        policy = _policy()
        policy.observe(1, fresh=0)
        assert policy.should_continue_after(1) is True
        policy.observe(2, fresh=0)
        assert policy.should_continue_after(2) is True

    def test_fresh_floor_page_extends_past_the_configured_end(self):
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        assert policy.should_continue_after(3) is True
        assert policy.stop_reason is None


class TestExhaustion:
    def test_stops_after_k_consecutive_fresh_free_pages(self):
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=0)
        assert policy.should_continue_after(4) is True   # K=2 needs one more
        policy.observe(5, fresh=0)
        assert policy.should_continue_after(5) is False
        assert policy.stop_reason == 'exhausted'

    def test_a_fresh_page_resets_the_run(self):
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=0)
        policy.observe(5, fresh=17)
        policy.observe(6, fresh=0)
        assert policy.should_continue_after(6) is True
        policy.observe(7, fresh=0)
        assert policy.should_continue_after(7) is False

    def test_partial_freshness_still_counts_as_fresh(self):
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=1)
        assert policy.should_continue_after(4) is True


class TestUnknownPages:
    def test_a_failed_page_neither_advances_nor_resets_the_run(self):
        # D5: one transient proxy ban must not truncate the day.
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=0)
        policy.observe(5, fresh=None)          # fetch failed
        assert policy.should_continue_after(5) is True
        policy.observe(6, fresh=0)
        assert policy.should_continue_after(6) is False
        assert policy.stop_reason == 'exhausted'

    def test_a_single_failed_page_does_not_stop_the_scan(self):
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=None)
        assert policy.should_continue_after(4) is True

    def test_k_unreadable_pages_end_the_extension(self):
        # A page we could not read is no evidence the fresh block continues, so
        # a proxy outage must not march the scan to the cap fetching nothing.
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=None)
        policy.observe(5, fresh=None)
        assert policy.should_continue_after(5) is False
        assert policy.stop_reason == 'unreadable'
        assert policy.hit_cap is False

    def test_a_readable_page_resets_the_unknown_run(self):
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=None)
        policy.observe(5, fresh=12)
        policy.observe(6, fresh=None)
        assert policy.should_continue_after(6) is True


class TestCap:
    def test_stops_at_the_hard_cap_even_while_fresh(self):
        policy = _policy(max_page=5)
        for page in (1, 2, 3, 4, 5):
            policy.observe(page, fresh=40)
        assert policy.should_continue_after(5) is False
        assert policy.stop_reason == 'cap'
        assert policy.hit_cap is True

    def test_a_spent_fresh_free_budget_outranks_the_ceiling(self):
        # Pages 4 and 5 are fresh-free and 5 is the cap. The scan ended on its
        # own terms, so calling it `cap` would warn about a truncation that did
        # not happen — and D6 treats a cap hit as exactly that signal.
        policy = _policy(max_page=5)
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=0)
        policy.observe(5, fresh=0)
        assert policy.should_continue_after(5) is False
        assert policy.stop_reason == 'exhausted'
        assert policy.hit_cap is False

    def test_a_spent_unreadable_budget_outranks_the_ceiling(self):
        policy = _policy(max_page=5)
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=None)
        policy.observe(5, fresh=None)
        assert policy.should_continue_after(5) is False
        assert policy.stop_reason == 'unreadable'
        assert policy.hit_cap is False

    def test_cap_below_the_floor_does_not_cut_the_configured_range(self):
        # A misconfigured cap must never scan less than PAGE_END.
        policy = _policy(floor=10, max_page=4)
        for page in range(1, 10):
            policy.observe(page, fresh=0)
            assert policy.should_continue_after(page) is True
        policy.observe(10, fresh=0)
        assert policy.should_continue_after(10) is False
        assert policy.stop_reason == 'floor'


class TestEndOfContent:
    def test_end_of_content_stops_regardless_of_freshness(self):
        policy = _policy()
        policy.observe(1, fresh=40)
        policy.observe(2, fresh=0, end_of_content=True)
        assert policy.should_continue_after(2) is False
        assert policy.stop_reason == 'end-of-content'
        assert policy.hit_cap is False


class TestForceStop:
    def test_records_a_reason_the_page_rule_cannot_express(self):
        policy = _policy()
        policy.observe(1, fresh=40)
        policy.force_stop('proxies-exhausted')
        assert policy.stop_reason == 'proxies-exhausted'
        assert policy.hit_cap is False

    def test_does_not_overwrite_why_the_scan_already_stopped(self):
        # Whatever ended the scan first is what ended it; a late infrastructure
        # failure must not relabel a clean `exhausted` run as truncated.
        policy = _policy()
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        policy.observe(4, fresh=0)
        policy.observe(5, fresh=0)
        assert policy.should_continue_after(5) is False
        policy.force_stop('proxies-exhausted')
        assert policy.stop_reason == 'exhausted'


class TestConfigDefaults:
    def test_shipped_defaults_match_the_adr(self):
        from javdb.spider.runtime import config

        assert config.PAGE_SCAN_DYNAMIC is True
        assert config.PAGE_SCAN_MAX == 30
        assert config.PAGE_SCAN_STOP_AFTER == 2

    def test_the_floor_default_matches_what_the_generator_writes(self, monkeypatch):
        # ADR-057 makes PAGE_END the scan floor, so a runtime default that
        # differs from the one config_generator writes for GitHub Actions gives
        # local runs a different base scan depth than production.
        import importlib

        from javdb.infra import config_generator
        from javdb.spider.runtime import config

        monkeypatch.delenv('END_PAGE', raising=False)
        monkeypatch.delenv('PAGE_END', raising=False)
        importlib.reload(config_generator)

        generated = dict(
            (row[0], row[3]) for row in config_generator.get_config_map()
        )
        assert generated['PAGE_END'] == config.PAGE_END


class TestStopAfterFloor:
    def test_a_zero_budget_does_not_stop_on_a_fresh_page(self):
        # Both counters start at 0, so honouring K=0 would satisfy the exhausted
        # check the moment the scan reaches the floor — silently truncating on a
        # page still full of new torrents, which is what ADR-057 exists to stop.
        policy = _policy(floor=3, stop_after=0)
        for page in (1, 2, 3):
            policy.observe(page, fresh=40)
        assert policy.should_continue_after(3) is True
        assert policy.stop_after == 1
        assert policy.stop_after_raw == 0

    def test_a_negative_budget_is_floored_too(self):
        policy = _policy(floor=1, stop_after=-5)
        policy.observe(1, fresh=40)
        assert policy.should_continue_after(1) is True
        assert policy.stop_after == 1

    def test_a_floored_budget_still_stops_after_one_quiet_page(self):
        policy = _policy(floor=1, stop_after=0)
        policy.observe(1, fresh=40)
        policy.observe(2, fresh=0)
        assert policy.should_continue_after(2) is False
        assert policy.stop_reason == 'exhausted'

    def test_a_usable_budget_is_left_alone(self):
        policy = _policy(stop_after=2)
        assert (policy.stop_after, policy.stop_after_raw) == (2, 2)
