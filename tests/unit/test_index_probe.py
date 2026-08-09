"""Unit tests for the index freshness probe's analysis logic.

The fetch path needs the network, but the parts that decide what the probe
*concludes* — where freshness stops, whether it is contiguous, and what a
"stop after K fresh-free pages" rule would miss — are pure functions.
"""

import pytest

from javdb.ops.index_probe import PageProbe, probe_page, simulate_stop, summarize


def _page(page, fresh, p1=0, p2=0, status='ok'):
    return PageProbe(
        page=page, url=f'https://javdb.com/?page={page}', status=status,
        entries=20, today=fresh, fresh=fresh,
        p1_selected=p1, p2_selected=p2, release_dates=['2026-08-05'],
    )


class TestSimulateStop:
    """Rule mechanics, with the floor pinned out of the way at page 1."""

    def test_stops_on_first_fresh_free_page_when_k_is_one(self):
        probes = [_page(1, 5), _page(2, 3), _page(3, 0), _page(4, 2)]
        assert simulate_stop(probes, 1, floor_page=1)[0] == 3

    def test_k_two_survives_a_single_fresh_free_page(self):
        probes = [_page(1, 5), _page(2, 0), _page(3, 2), _page(4, 0), _page(5, 0)]
        assert simulate_stop(probes, 2, floor_page=1)[0] == 5

    def test_failed_page_neither_counts_nor_resets_the_run(self):
        # Page 3 failed to fetch: pages 2 and 4 are still consecutive fresh-free.
        probes = [_page(1, 1), _page(2, 0), _page(3, 0, status='fetch_failed'),
                  _page(4, 0), _page(5, 9)]
        assert simulate_stop(probes, 2, floor_page=1)[0] == 4

    def test_returns_none_when_every_page_is_fresh(self):
        probes = [_page(1, 5), _page(2, 3), _page(3, 1)]
        assert simulate_stop(probes, 2, floor_page=1)[0] is None


class TestSimulationFloor:
    def test_the_floor_defaults_to_the_configured_page_end(self):
        from javdb.ops import index_probe

        # Production scans every page up to PAGE_END whatever its freshness, so
        # a quiet page 2 is not a stop — simulating from page 1 would report
        # everything past it as missed for a scan that never stopped there.
        probes = [_page(1, 5)] + [_page(p, 0) for p in range(2, 6)]
        assert simulate_stop(probes, 1) == (None, None)
        assert simulate_stop(probes, 1, floor_page=1)[0] == 2
        assert index_probe.PAGE_END > 5

    def test_the_stop_never_lands_before_the_floor(self):
        probes = [_page(p, 0) for p in range(1, 9)]
        stop_page, _ = simulate_stop(probes, 2, floor_page=6)
        assert stop_page == 6

    def test_summarize_reports_the_floor_it_assumed(self, monkeypatch):
        from javdb.ops import index_probe

        monkeypatch.setattr(index_probe, 'PAGE_END', 3)
        probes = [_page(1, 5), _page(2, 4), _page(3, 0), _page(4, 0), _page(5, 7)]
        sim = summarize(probes)['stop_rule_simulation']
        assert sim['floor_page'] == 3
        assert sim['k=1']['stop_page'] == 3
        assert sim['k=2']['stop_page'] == 4
        assert sim['k=2']['fresh_missed'] == 7


class TestSummarize:
    def test_detects_a_hole_in_the_fresh_range(self):
        probes = [_page(1, 5), _page(2, 0), _page(3, 4)]
        summary = summarize(probes)
        assert summary['fresh_free_pages_before_deepest_fresh'] == [2]
        assert summary['freshness_contiguous'] is False
        assert summary['deepest_page_with_fresh'] == 3

    def test_a_fetch_gap_leaves_contiguity_undetermined(self):
        # An unreadable page before the deepest fresh page may or may not have
        # been fresh; claiming contiguity would let a proxy blip manufacture
        # support for the stop rule.
        probes = [_page(1, 5), _page(2, 0, status='fetch_failed'), _page(3, 4)]
        summary = summarize(probes)
        assert summary['unreadable_pages_before_deepest_fresh'] == [2]
        assert summary['freshness_contiguous'] is None
        assert summary['fresh_free_pages_before_deepest_fresh'] == []

    def test_a_real_hole_outranks_a_fetch_gap(self):
        probes = [_page(1, 5), _page(2, 0), _page(3, 0, status='invalid'), _page(4, 4)]
        summary = summarize(probes)
        assert summary['freshness_contiguous'] is False

    def test_a_gap_after_the_fresh_block_does_not_taint_contiguity(self):
        probes = [_page(1, 5), _page(2, 4), _page(3, 0, status='fetch_failed')]
        summary = summarize(probes)
        assert summary['unreadable_pages_before_deepest_fresh'] == []
        assert summary['freshness_contiguous'] is True

    def test_us_formatted_dates_are_ordered_by_value(self):
        # Raw string comparison would call 12/28/2012 newer than 08/08/2026 and
        # report this page as descending.
        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['12/28/2012', '08/08/2026'])]
        assert summarize(probes)['release_dates_descending'] is False

        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['08/08/2026', '12/28/2012'])]
        assert summarize(probes)['release_dates_descending'] is True

    def test_contiguous_run_reports_no_holes(self):
        probes = [_page(1, 5), _page(2, 4), _page(3, 0), _page(4, 0)]
        summary = summarize(probes)
        assert summary['fresh_free_pages_before_deepest_fresh'] == []
        assert summary['freshness_contiguous'] is True
        assert summary['deepest_page_with_fresh'] == 2

    def test_reports_what_the_stop_rule_would_have_missed(self, monkeypatch):
        from javdb.ops import index_probe

        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [_page(1, 5, p1=2), _page(2, 0), _page(3, 4, p1=3)]
        sim = summarize(probes)['stop_rule_simulation']['k=1']
        assert sim['stop_page'] == 2
        assert sim['fresh_missed'] == 4
        assert sim['selected_missed'] == 3
        assert sim['pages_missed_with_fresh'] == [3]

    def test_release_date_ordering_is_reported(self):
        descending = [
            PageProbe(page=1, url='u1', status='ok', release_dates=['2026-08-05', '2026-08-04']),
            PageProbe(page=2, url='u2', status='ok', release_dates=['2026-08-03']),
        ]
        assert summarize(descending)['release_dates_descending'] is True

        shuffled = [
            PageProbe(page=1, url='u1', status='ok', release_dates=['2026-08-03', '2026-08-05']),
        ]
        assert summarize(shuffled)['release_dates_descending'] is False

    def test_failed_pages_are_excluded_from_totals(self):
        probes = [_page(1, 5), _page(2, 99, status='fetch_failed'), _page(3, 0, status='empty')]
        summary = summarize(probes)
        assert summary['pages_parsed'] == 1
        assert summary['total_fresh'] == 5
        assert summary['pages_failed'] == [2]
        assert summary['pages_empty'] == [3]


class TestProbePage:
    def test_fetch_failure_is_recorded_not_raised(self):
        class DeadGateway:
            def fetch_html(self, url):
                return None

        probe = probe_page(DeadGateway(), 7)
        assert probe.status == 'fetch_failed'
        assert probe.parsed is False
        assert probe.fresh == 0

    def test_counts_fresh_entries_and_production_selection(self, tmp_path):
        html = _read_fixture()
        if html is None:
            pytest.skip('index fixture unavailable')

        class FakeGateway:
            def fetch_html(self, url):
                return html

        # A nested path also covers the directory creation in the save branch.
        saved = tmp_path / 'probe-html' / 'index_page_1.html'
        probe = probe_page(FakeGateway(), 1, save_html_to=saved)
        assert saved.read_text(encoding='utf-8') == html
        assert probe.status == 'ok'
        assert probe.entries == 3
        # One entry carries 今日新種 + 含中字磁鏈 — fresh, and phase-1 selectable.
        assert probe.today == 1
        assert probe.fresh == 1
        assert probe.fresh_with_subtitle == 1
        assert probe.p1_selected == 1
        assert probe.fresh_positions == [0]


def _read_fixture():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'fixtures' / 'parser' / 'index_edge_cases.html'
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8')


class TestNewestReleaseDate:
    def test_reports_the_newest_release_date_seen(self):
        probes = [
            PageProbe(page=1, url='u1', status='ok', release_dates=['2026-08-04', '2012-12-01']),
            PageProbe(page=2, url='u2', status='ok', release_dates=['2021-11-09', '2026-08-01']),
        ]
        assert summarize(probes)['newest_release_date'] == '2026-08-04'

    def test_empty_when_no_dates_were_parsed(self):
        assert summarize([PageProbe(page=1, url='u1', status='ok')])['newest_release_date'] == ''

    def test_us_formatted_dates_are_not_compared_as_strings(self):
        # JavDB renders M/D/YYYY in the English locale; a plain max() would pick
        # 12/28/2012 over 08/08/2026 because it compares the leading digits.
        probes = [PageProbe(
            page=1, url='u1', status='ok',
            release_dates=['12/28/2012', '08/08/2026', '03/22/2012'],
        )]
        assert summarize(probes)['newest_release_date'] == '08/08/2026'

    def test_unparseable_dates_are_ignored(self):
        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['not a date', '2026-08-04'])]
        assert summarize(probes)['newest_release_date'] == '2026-08-04'


class TestDateKeyEdgeCases:
    def test_unpadded_months_and_days_compare_numerically(self):
        # '8' sorts above '12' as a string; as a date it does not.
        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['8/9/2026', '12/10/2026'])]
        summary = summarize(probes)
        assert summary['newest_release_date'] == '12/10/2026'
        assert summary['release_dates_descending'] is False

        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['8/10/2026', '8/9/2026'])]
        assert summarize(probes)['release_dates_descending'] is True


class TestUnorderableDates:
    def test_ordering_is_unknown_without_two_comparable_dates(self):
        # all() over an empty range is True; reporting "descending" from a page
        # whose date field stopped parsing would read as evidence the listing is
        # date-sorted, which is the opposite of what the probe measured.
        no_dates = [PageProbe(page=1, url='u1', status='ok', release_dates=[])]
        assert summarize(no_dates)['release_dates_descending'] is None

        unparseable = [PageProbe(page=1, url='u1', status='ok',
                                 release_dates=['—', 'unknown'])]
        assert summarize(unparseable)['release_dates_descending'] is None

        single = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['2026-08-04'])]
        assert summarize(single)['release_dates_descending'] is None

    def test_a_partial_date_sample_cannot_confirm_descending(self):
        # The unparseable entry sits exactly where a violation would hide, so
        # dropping it and reporting the survivors as ordered would let field
        # drift vouch for the ordering.
        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['2026-08-08', 'unknown', '2026-08-07'])]
        summary = summarize(probes)
        assert summary['release_dates_descending'] is None
        assert (summary['release_dates_parsed'], summary['release_dates_seen']) == (2, 3)

    def test_an_unreadable_page_cannot_confirm_descending(self):
        probes = [
            PageProbe(page=1, url='u1', status='ok',
                      release_dates=['2026-08-08', '2026-08-07']),
            PageProbe(page=2, url='u2', status='fetch_failed'),
        ]
        assert summarize(probes)['release_dates_descending'] is None

    def test_a_counterexample_holds_despite_an_incomplete_sample(self):
        # False needs no coverage argument: two entries in ascending order are
        # in ascending order whatever sits between them.
        probes = [PageProbe(page=1, url='u1', status='ok',
                            release_dates=['2026-08-07', 'unknown', '2026-08-08'])]
        assert summarize(probes)['release_dates_descending'] is False


class TestNoFreshPages:
    def test_contiguity_is_unknown_when_nothing_was_fresh(self):
        # A probe run before the day's torrents land tests nothing; reporting
        # True would read as evidence for the stop rule.
        probes = [_page(1, 0), _page(2, 0), _page(3, 0)]
        summary = summarize(probes)
        assert summary['deepest_page_with_fresh'] is None
        assert summary['freshness_contiguous'] is None


class TestUnreadableTail:
    def test_unreadable_pages_past_the_stop_are_not_counted_as_zero(self, monkeypatch):
        from javdb.ops import index_probe

        # Page 4 was never read, so "the rule lost nothing" is not something
        # this run can establish — fresh_missed only covers pages we could see.
        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [_page(1, 5), _page(2, 0), _page(3, 0),
                  _page(4, 0, status='fetch_failed')]
        sim = summarize(probes)['stop_rule_simulation']['k=2']
        assert sim['stop_page'] == 3
        assert sim['fresh_missed'] == 0
        assert sim['pages_missed_unreadable'] == [4]
        assert sim['missed_counts_complete'] is False

    def test_a_fully_read_tail_reports_complete_counts(self, monkeypatch):
        from javdb.ops import index_probe

        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [_page(1, 5), _page(2, 0), _page(3, 0), _page(4, 6)]
        sim = summarize(probes)['stop_rule_simulation']['k=2']
        assert sim['fresh_missed'] == 6
        assert sim['pages_missed_unreadable'] == []
        assert sim['missed_counts_complete'] is True


class TestTruncatedScan:
    def test_a_time_budget_cut_is_not_reported_as_a_shorter_request(self):
        # --pages 30 that gave up at page 12 must not look like a clean 12-page
        # scan: the probe exits zero either way.
        probes = [_page(p, 0) for p in range(1, 13)]
        summary = summarize(probes, pages_requested=30)
        assert summary['pages_requested'] == 30
        assert summary['pages_attempted'] == 12
        assert summary['scan_truncated'] is True

    def test_a_complete_scan_is_not_flagged(self):
        probes = [_page(p, 0) for p in range(1, 13)]
        summary = summarize(probes, pages_requested=12)
        assert summary['scan_truncated'] is False
        assert summary['pages_attempted'] == 12

    def test_a_truncated_scan_cannot_claim_complete_missed_counts(self, monkeypatch):
        from javdb.ops import index_probe

        # Pages 13-30 were never attempted, so they are unknown for the same
        # reason an unreadable page is — the simulation must not imply the rule
        # cost nothing over a tail nobody fetched.
        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [_page(1, 5)] + [_page(p, 0) for p in range(2, 13)]
        sim = summarize(probes, pages_requested=30)['stop_rule_simulation']['k=2']
        assert sim['stop_page'] == 3
        assert sim['pages_missed_unreadable'] == []
        assert sim['missed_counts_complete'] is False


class TestSimulationCap:
    def test_the_cap_stops_the_simulation_where_production_stops(self, monkeypatch):
        from javdb.ops import index_probe

        # Pages 31-35 are still fresh, but production would have been cut off
        # at PAGE_SCAN_MAX. Simulating without the cap reports them as reached
        # and the rule as lossless.
        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        monkeypatch.setattr(index_probe, 'PAGE_SCAN_MAX', 30)
        probes = [_page(p, 5) for p in range(1, 36)]
        sim = summarize(probes)['stop_rule_simulation']
        assert sim['max_page'] == 30
        assert sim['k=2']['stop_page'] == 30
        assert sim['k=2']['stop_reason'] == 'cap'
        assert sim['k=2']['fresh_missed'] == 25          # pages 31-35, 5 each
        assert sim['k=2']['pages_missed_with_fresh'] == [31, 32, 33, 34, 35]

    def test_an_explicit_cap_overrides_the_configured_one(self):
        probes = [_page(p, 5) for p in range(1, 10)]
        assert simulate_stop(probes, 2, floor_page=1, max_page=5) == (5, 'cap')


class TestSimulationMatchesProduction:
    def test_unreadable_pages_stop_the_simulated_scan_too(self, monkeypatch):
        from javdb.ops import index_probe

        # Production stops after k consecutive unreadable pages, so a probe that
        # scanned straight through them would report fresh_missed=0 for a run
        # the real rule would have truncated.
        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [
            _page(1, 5),
            _page(2, 0, status='fetch_failed'),
            _page(3, 0, status='invalid'),
            _page(4, 7),
        ]
        stop_page, stop_reason = simulate_stop(probes, 2, floor_page=1)
        assert (stop_page, stop_reason) == (3, 'unreadable')

        sim = summarize(probes)['stop_rule_simulation']['k=2']
        assert sim['stop_page'] == 3
        assert sim['stop_reason'] == 'unreadable'
        # Page 4's freshness is what the rule would have cost us.
        assert sim['fresh_missed'] == 7

    def test_end_of_content_is_reported_as_such(self):
        # End-of-content outranks the floor: there is nothing left to scan.
        probes = [_page(1, 5), _page(2, 0, status='empty')]
        assert simulate_stop(probes, 3) == (2, 'end-of-content')


class TestProxyDefault:
    """The probe reproduces the daily spider's index fetch, proxying included.

    Forcing the pool on would measure a path production may not take, and the
    project rule is that modules consult PROXY_MODULES rather than hard-coding.
    """

    def _use_proxy_for(self, argv, monkeypatch, modules):
        import sys
        from javdb.ops import index_probe

        seen = {}

        def fake_gateway(**kwargs):
            seen.update(kwargs)
            raise SystemExit(0)

        monkeypatch.setattr(index_probe, 'create_gateway', fake_gateway)
        monkeypatch.setattr(
            index_probe, 'should_use_proxy_for_module',
            lambda module, override: (
                override if override is not None else module in modules
            ),
        )
        monkeypatch.setattr(sys, 'argv', ['index_probe', *argv])
        with pytest.raises(SystemExit):
            index_probe.main()
        return seen['use_proxy']

    def test_defaults_to_the_module_configuration(self, monkeypatch):
        assert self._use_proxy_for([], monkeypatch, modules={'spider'}) is True
        assert self._use_proxy_for([], monkeypatch, modules=set()) is False

    def test_flags_override_the_module_configuration(self, monkeypatch):
        assert self._use_proxy_for(['--no-proxy'], monkeypatch, modules={'spider'}) is False
        assert self._use_proxy_for(['--use-proxy'], monkeypatch, modules=set()) is True

    def test_contradictory_flags_are_rejected(self, monkeypatch):
        import sys
        from javdb.ops import index_probe

        monkeypatch.setattr(sys, 'argv', ['index_probe', '--no-proxy', '--use-proxy'])
        with pytest.raises(SystemExit) as exc:
            index_probe.main()
        assert exc.value.code == 2


class TestConfiguredStopAfter:
    def test_the_configured_budget_is_simulated_and_named(self, monkeypatch):
        from javdb.ops import index_probe

        # Production running k=4 must appear in the report; a sweep of 1..3 says
        # nothing about the rule actually in force.
        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        monkeypatch.setattr(index_probe, 'PAGE_SCAN_STOP_AFTER', 4)
        probes = [_page(1, 5)] + [_page(p, 0) for p in range(2, 7)] + [_page(7, 9)]
        sim = summarize(probes)['stop_rule_simulation']

        assert sim['configured_stop_after'] == 4
        assert 'k=4' in sim
        assert sim['k=4']['stop_page'] == 5
        assert sim['k=4']['fresh_missed'] == 9
        # The calibration sweep is still there alongside it.
        assert sim['k=1']['stop_page'] == 2


class TestEmptyPageIsKnownNotUnknown:
    """An `empty` page is the tail of pagination, not a page we failed to read.

    It carries no entries, so it hides nothing — treating it as unknown would
    make an ordinary complete scan report undetermined results.
    """

    def test_an_empty_tail_page_does_not_void_the_date_sample(self, monkeypatch):
        from javdb.ops import index_probe

        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [
            PageProbe(page=1, url='u1', status='ok',
                      release_dates=['2026-08-08', '2026-08-07']),
            PageProbe(page=2, url='u2', status='empty'),
        ]
        assert summarize(probes)['release_dates_descending'] is True

    def test_an_unreadable_page_still_voids_it(self):
        probes = [
            PageProbe(page=1, url='u1', status='ok',
                      release_dates=['2026-08-08', '2026-08-07']),
            PageProbe(page=2, url='u2', status='fetch_failed'),
        ]
        assert summarize(probes)['release_dates_descending'] is None

    def test_an_empty_page_before_fresh_content_is_a_real_hole(self):
        # Known to hold nothing, so freshness demonstrably is not contiguous —
        # this is False, not the None an unreadable page would produce.
        probes = [_page(1, 5), _page(2, 0, status='empty'), _page(3, 4)]
        summary = summarize(probes)
        assert summary['fresh_free_pages_before_deepest_fresh'] == [2]
        assert summary['freshness_contiguous'] is False

    def test_an_empty_tail_after_the_stop_is_not_an_unread_page(self, monkeypatch):
        from javdb.ops import index_probe

        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        probes = [_page(1, 5), _page(2, 0), _page(3, 0),
                  PageProbe(page=4, url='u4', status='empty')]
        sim = summarize(probes)['stop_rule_simulation']['k=2']
        assert sim['pages_missed_unreadable'] == []
        assert sim['missed_counts_complete'] is True


class TestShallowRequest:
    def test_a_scan_too_shallow_for_the_rule_is_not_complete(self, monkeypatch):
        from javdb.ops import index_probe

        # --pages 10 completed exactly as asked, but page 10 is still fresh and
        # the cap is 30 — production would have read page 11. Nothing failed and
        # nothing was truncated, so only the missing stop point marks this out.
        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        monkeypatch.setattr(index_probe, 'PAGE_SCAN_MAX', 30)
        probes = [_page(p, 5) for p in range(1, 11)]
        sim = summarize(probes, pages_requested=10)['stop_rule_simulation']['k=2']

        assert sim['stop_page'] is None
        assert sim['fresh_missed'] == 0
        assert sim['pages_missed_unreadable'] == []
        assert sim['missed_counts_complete'] is False

    def test_reaching_the_cap_is_a_complete_answer(self, monkeypatch):
        from javdb.ops import index_probe

        monkeypatch.setattr(index_probe, 'PAGE_END', 1)
        monkeypatch.setattr(index_probe, 'PAGE_SCAN_MAX', 5)
        probes = [_page(p, 5) for p in range(1, 6)]
        sim = summarize(probes, pages_requested=5)['stop_rule_simulation']['k=2']

        assert (sim['stop_page'], sim['stop_reason']) == (5, 'cap')
        assert sim['missed_counts_complete'] is True


class TestProductionAlignment:
    def test_the_start_page_defaults_to_the_production_start(self, monkeypatch):
        import sys
        from javdb.ops import index_probe

        # Production starts at PAGE_START. Probing from page 1 regardless would
        # charge pages production never fetches against the consecutive
        # fresh-free and unreadable budgets.
        scanned = []
        monkeypatch.setattr(index_probe, 'PAGE_START', 4)
        monkeypatch.setattr(index_probe, 'create_gateway', lambda **kw: object())
        monkeypatch.setattr(
            index_probe, 'probe_page',
            lambda gw, page, save_html_to=None: (
                scanned.append(page) or _page(page, 0)
            ),
        )
        monkeypatch.setattr(sys, 'argv', ['index_probe', '--pages', '2', '--delay', '0'])
        index_probe.main()

        assert scanned == [4, 5]

    def test_an_explicit_start_page_still_wins(self, monkeypatch):
        import sys
        from javdb.ops import index_probe

        scanned = []
        monkeypatch.setattr(index_probe, 'PAGE_START', 4)
        monkeypatch.setattr(index_probe, 'create_gateway', lambda **kw: object())
        monkeypatch.setattr(
            index_probe, 'probe_page',
            lambda gw, page, save_html_to=None: (
                scanned.append(page) or _page(page, 0)
            ),
        )
        monkeypatch.setattr(
            sys, 'argv',
            ['index_probe', '--start-page', '1', '--pages', '2', '--delay', '0'],
        )
        index_probe.main()

        assert scanned == [1, 2]

    def test_the_report_says_whether_production_runs_the_rule(self, monkeypatch):
        from javdb.ops import index_probe

        probes = [_page(1, 5), _page(2, 0), _page(3, 0)]

        monkeypatch.setattr(index_probe, '_daily_scan_would_extend', lambda: False)
        assert summarize(probes)['stop_rule_simulation']['dynamic_scan_enabled'] is False

        monkeypatch.setattr(index_probe, '_daily_scan_would_extend', lambda: True)
        assert summarize(probes)['stop_rule_simulation']['dynamic_scan_enabled'] is True

    def test_the_predicate_follows_the_production_builder(self, monkeypatch):
        from javdb.ops import index_probe
        from javdb.spider.fetch import index as index_mod

        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', False, raising=False)
        assert index_probe._daily_scan_would_extend() is False

        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        monkeypatch.setattr(index_mod, 'IGNORE_RELEASE_DATE_FILTER', True, raising=False)
        assert index_probe._daily_scan_would_extend() is False

        monkeypatch.setattr(index_mod, 'IGNORE_RELEASE_DATE_FILTER', False, raising=False)
        assert index_probe._daily_scan_would_extend() is True


class TestBlacklistOrdering:
    """Freshness before the blacklists, selection after — production's order.

    ADR-057 D2 counts a blacklisted entry as proof the page is still inside the
    fresh block, but production drops it before selecting, so counting it as
    selected would inflate `selected_missed` with entries that never ingest.
    """

    def _probe_with_family_blacklist(self, monkeypatch, blacklist):
        from javdb.ops import index_probe

        html = _read_fixture()
        if html is None:
            pytest.skip('index fixture unavailable')

        class FakeGateway:
            def fetch_html(self, url):
                return html

        monkeypatch.setattr(
            index_probe, 'load_daily_family_blacklist', lambda _url: blacklist,
        )
        return index_probe.probe_page(FakeGateway(), 1)

    def test_a_blacklisted_entry_is_fresh_but_not_selected(self, monkeypatch):
        # ABC-123 is the fixture's only fresh, phase-1-selectable entry, and its
        # video_code_family is `classic_hyphenated`.
        probe = self._probe_with_family_blacklist(monkeypatch, {'classic_hyphenated'})
        assert probe.fresh == 1
        assert probe.p1_selected == 0

    def test_without_the_blacklist_it_is_still_selected(self, monkeypatch):
        probe = self._probe_with_family_blacklist(monkeypatch, set())
        assert probe.fresh == 1
        assert probe.p1_selected == 1
