"""End-to-end tests for dynamic index pagination (ADR-057).

Both fetch paths are driven with fake page content so the freshness-driven
extension can be asserted without touching the network.
"""

import pytest

import javdb.spider.fetch.index as index_mod


def _html(fresh: int, entries: int = 5) -> str:
    """An index page with *fresh* of *entries* movies carrying a today badge."""
    items = []
    for i in range(entries):
        tags = '<span class="tag">今日新種</span>' if i < fresh else ''
        items.append(
            '<div class="item"><a href="/v/x%d" class="box">'
            '<div class="video-title"><strong>ABC-%d</strong> Title</div>'
            '<div class="meta">2026-08-08</div>'
            '<div class="tags has-addons"><span class="tag">含磁鏈</span>%s</div>'
            '</a></div>' % (i, i, tags)
        )
    return (
        '<html><body><div class="movie-list h cols-4 vcols-8">'
        + ''.join(items)
        + '</div></body></html>'
    )


@pytest.fixture
def fake_fetch(monkeypatch):
    """Serve scripted pages and record which page numbers were requested."""
    requested = []

    class _NoSleep:
        def sleep(self):
            pass

        def apply_volume_multiplier(self, *args, **kwargs):
            pass

    monkeypatch.setattr(index_mod, '_sleep_manager', lambda runtime=None: _NoSleep())

    def _install(fresh_by_page: dict, default_fresh: int = 0):
        def fake_fetch_index_page_with_fallback(page_url, session, **kwargs):
            page_num = kwargs['page_num']
            requested.append(page_num)
            fresh = fresh_by_page.get(page_num, default_fresh)
            return (_html(fresh), True, False, kwargs['use_proxy'],
                    kwargs['use_cf_bypass'], False)

        monkeypatch.setattr(
            index_mod, 'fetch_index_page_with_fallback',
            fake_fetch_index_page_with_fallback,
        )
        return requested

    return _install


def _run_sequential(end_page=2, start_page=1, **overrides):
    kwargs = dict(
        runtime=None, session=None, start_page=start_page, end_page=end_page,
        parse_all=False, phase_mode='1', custom_url=None,
        ignore_release_date=False, use_proxy=False, use_cf_bypass=False,
        max_consecutive_empty=3, output_csv='out.csv', output_dated_dir='/tmp',
        csv_path='/tmp/out.csv', user_specified_output=True,
        parsed_movies_history_phase1={}, parsed_movies_history_phase2={},
        use_parallel=False,
    )
    kwargs.update(overrides)
    return index_mod.fetch_all_index_pages(**kwargs)


class TestSequentialExtension:
    def test_extends_past_the_configured_end_while_pages_are_fresh(self, fake_fetch, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        requested = fake_fetch({1: 5, 2: 5, 3: 5, 4: 0, 5: 0, 6: 5})

        result = _run_sequential(end_page=2)

        # Floor 1-2, extended while fresh, stopped after two fresh-free pages.
        assert requested == [1, 2, 3, 4, 5]
        assert result['effective_end_page'] == 5
        assert result['page_scan_stop_reason'] == 'exhausted'

    def test_fixed_range_is_unchanged_when_dynamic_is_off(self, fake_fetch, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', False, raising=False)
        requested = fake_fetch({1: 5, 2: 5, 3: 5})

        result = _run_sequential(end_page=2)

        assert requested == [1, 2]
        # None means "no dynamic scan ran" — callers keep their configured range.
        assert result['effective_end_page'] is None
        assert result['page_scan_stop_reason'] == 'floor'

    def test_quiet_day_still_scans_the_whole_floor(self, fake_fetch, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        requested = fake_fetch({}, default_fresh=0)

        result = _run_sequential(end_page=3)

        assert requested == [1, 2, 3]
        assert result['effective_end_page'] == 3

    def test_cap_stops_a_runaway_scan_and_is_reported(self, fake_fetch, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_MAX', 4, raising=False)
        requested = fake_fetch({}, default_fresh=5)   # every page looks fresh

        result = _run_sequential(end_page=2)

        assert requested == [1, 2, 3, 4]
        assert result['page_scan_stop_reason'] == 'cap'

    def test_adhoc_url_keeps_the_fixed_range(self, fake_fetch, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        requested = fake_fetch({}, default_fresh=5)

        _run_sequential(end_page=2, custom_url='https://javdb.com/actors/EvkJ',
                        user_specified_output=True)

        assert requested == [1, 2]

    def test_ignore_release_date_keeps_the_fixed_range(self, fake_fetch, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        requested = fake_fetch({}, default_fresh=5)

        _run_sequential(end_page=2, ignore_release_date=True)

        assert requested == [1, 2]


# ---------------------------------------------------------------------------
# Parallel path
# ---------------------------------------------------------------------------

import javdb.spider.fetch.index_parallel as parallel_mod  # noqa: E402
from collections import deque  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class _DynamicBackend:
    """Backend that answers whatever the collection loop submits.

    The stock fake in ``test_index_parallel`` replays a fixed result list, which
    cannot exercise an extension that submits pages while results arrive.
    """

    def __init__(self, fresh_by_page, default_fresh=0):
        self._fresh_by_page = fresh_by_page
        self._default_fresh = default_fresh
        self._pending = deque()
        self.submitted_pages = []
        self.marked_done = False
        self.has_login_worker = False

    def start(self):
        pass

    def submit(self, url, meta, entry_index, priority, login_only=False):
        self.submitted_pages.append(meta['page_num'])
        self._pending.append(meta['page_num'])

    def mark_done(self):
        self.marked_done = True

    def results(self):
        while self._pending:
            page_num = self._pending.popleft()
            fresh = self._fresh_by_page.get(page_num, self._default_fresh)
            task = SimpleNamespace(meta={'page_num': page_num})
            data = parallel_mod._index_parse_fn(_html(fresh), task)
            yield SimpleNamespace(
                success=True, data=data, task=task,
                worker_name='w1', error=None,
            )

    def shutdown(self):
        pass

    def export_login_state(self):
        pass


def _run_parallel(backend, monkeypatch, end_page=3, **overrides):
    monkeypatch.setattr(
        parallel_mod, 'build_parallel_index_backend', lambda **_kwargs: backend,
    )
    monkeypatch.setattr(
        parallel_mod, '_sentinel_field_health',
        SimpleNamespace(start_run=lambda: None, current=lambda: None),
    )
    kwargs = dict(
        runtime=None, start_page=1, end_page=end_page, parse_all=False,
        phase_mode='1', custom_url=None, ignore_release_date=False,
        use_proxy=False, use_cf_bypass=False, max_consecutive_empty=3,
        output_csv='out.csv', output_dated_dir='/tmp',
        csv_path='/tmp/out.csv', user_specified_output=True, cancel_event=None,
    )
    kwargs.update(overrides)
    return parallel_mod.fetch_all_index_pages_parallel(**kwargs)


class TestParallelExtension:
    def test_extends_the_fixed_range_while_pages_are_fresh(self, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        backend = _DynamicBackend({1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 5, 7: 0, 8: 0, 9: 5})

        result = _run_parallel(backend, monkeypatch, end_page=3)

        assert backend.submitted_pages == [1, 2, 3, 4, 5, 6, 7, 8]
        assert backend.marked_done is True
        assert result['effective_end_page'] == 8
        assert result['page_scan_stop_reason'] == 'exhausted'

    def test_fixed_range_is_unchanged_when_dynamic_is_off(self, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', False, raising=False)
        backend = _DynamicBackend({}, default_fresh=5)

        result = _run_parallel(backend, monkeypatch, end_page=3)

        assert backend.submitted_pages == [1, 2, 3]
        assert backend.marked_done is True
        assert result['effective_end_page'] is None

    def test_cap_bounds_a_runaway_extension(self, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_MAX', 5, raising=False)
        backend = _DynamicBackend({}, default_fresh=5)

        result = _run_parallel(backend, monkeypatch, end_page=2)

        assert backend.submitted_pages == [1, 2, 3, 4, 5]
        assert result['page_scan_stop_reason'] == 'cap'

    def test_each_page_is_parsed_exactly_once(self, monkeypatch):
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        parsed_pages = []
        real_parse = parallel_mod.parse_index_page

        def counting_parse(html, page_num):
            parsed_pages.append(page_num)
            return real_parse(html, page_num)

        monkeypatch.setattr(parallel_mod, 'parse_index_page', counting_parse)
        backend = _DynamicBackend({1: 5, 2: 0, 3: 0})

        _run_parallel(backend, monkeypatch, end_page=3)

        assert sorted(parsed_pages) == [1, 2, 3]


class TestSummaryReporting:
    def test_reports_the_effective_page_range_and_stop_reason(self, capsys):
        from javdb.spider.runtime import report as report_mod

        report_mod.generate_summary_report(
            runtime=None, phase_mode='1', parse_all=False,
            start_page=1, end_page=10,
            effective_end_page=14, page_scan_stop_reason='cap',
            max_consecutive_empty=3,
            phase1_rows=[], phase2_rows=[], rows=[],
            use_history_for_loading=False, ignore_history=True,
            skipped_history_count=0, failed_count=0, no_new_torrents_count=0,
            csv_path='/tmp/out.csv', dry_run=False, use_history_for_saving=False,
            use_proxy=False, any_proxy_banned=False, any_proxy_banned_phase2=False,
        )

        out = capsys.readouterr().out
        assert 'SPIDER_STAT_PAGES=1-14' in out
        # D9: the reason rides its own line so the page range stays parseable.
        assert 'SPIDER_STAT_PAGE_SCAN_STOP_REASON=cap' in out

    def test_falls_back_to_the_configured_range(self, capsys):
        from javdb.spider.runtime import report as report_mod

        report_mod.generate_summary_report(
            runtime=None, phase_mode='1', parse_all=False,
            start_page=1, end_page=10, max_consecutive_empty=3,
            phase1_rows=[], phase2_rows=[], rows=[],
            use_history_for_loading=False, ignore_history=True,
            skipped_history_count=0, failed_count=0, no_new_torrents_count=0,
            csv_path='/tmp/out.csv', dry_run=False, use_history_for_saving=False,
            use_proxy=False, any_proxy_banned=False, any_proxy_banned_phase2=False,
        )

        out = capsys.readouterr().out
        assert 'SPIDER_STAT_PAGES=1-10' in out
        # No dynamic scan ran, so there is no stop reason to report.
        assert 'SPIDER_STAT_PAGE_SCAN_STOP_REASON' not in out


class TestEmptyRange:
    def test_reversed_page_range_closes_the_backend_instead_of_hanging(self, monkeypatch):
        # results() only returns once mark_done() is in; with nothing submitted
        # the collection loop never runs, so the queue must be closed up front.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        backend = _DynamicBackend({}, default_fresh=5)

        result = _run_parallel(backend, monkeypatch, start_page=5, end_page=2)

        assert backend.submitted_pages == []
        assert backend.marked_done is True
        assert result['all_index_results_phase1'] == []


class TestResultPageRange:
    """The workflows read stats.pages out of the result JSON, not stdout."""

    def _page_range_after(self, idx_result):
        from javdb.spider.app.run_service import resolve_page_range

        return resolve_page_range(
            1, 10, False,
            effective_end_page=idx_result.get('effective_end_page'),
            page_scan_stop_reason=idx_result.get('page_scan_stop_reason'),
        )

    def test_extended_scan_reports_the_effective_range(self):
        assert self._page_range_after({
            'effective_end_page': 14, 'page_scan_stop_reason': 'exhausted',
        }) == '1-14 (exhausted)'

    def test_capped_scan_surfaces_the_cap_in_the_range(self):
        assert self._page_range_after({
            'effective_end_page': 30, 'page_scan_stop_reason': 'cap',
        }) == '1-30 (cap)'

    def test_fixed_range_stays_plain(self):
        assert self._page_range_after({
            'effective_end_page': 10, 'page_scan_stop_reason': 'floor',
        }) == '1-10'

    def test_no_dynamic_scan_keeps_the_configured_range(self):
        assert self._page_range_after({
            'effective_end_page': None, 'page_scan_stop_reason': 'floor',
        }) == '1-10'

    def test_parse_all_is_untouched(self):
        from javdb.spider.app.run_service import resolve_page_range

        assert resolve_page_range(
            1, 10, True, effective_end_page=14, page_scan_stop_reason='exhausted',
        ) == '1-*'

    def test_parse_all_still_reports_an_infrastructure_truncation(self):
        from javdb.spider.app.run_service import resolve_page_range

        # D7 keeps the rule out of --all, so a rule-derived reason is noise
        # there. A dead proxy pool is not a rule outcome — it cut the sweep
        # short, and `1-*` alone reads as having reached the last page.
        assert resolve_page_range(
            1, 10, True, effective_end_page=None,
            page_scan_stop_reason='proxies-exhausted',
        ) == '1-* (proxies-exhausted)'

    def test_a_truncation_with_no_usable_end_page_still_says_so(self):
        # A proxy pool that dies on page 1 leaves no range to report, but the
        # run was cut short — dropping the reason would make it read as a clean
        # pass over the configured 1-10.
        assert self._page_range_after({
            'effective_end_page': 0, 'page_scan_stop_reason': 'proxies-exhausted',
        }) == '1-10 (proxies-exhausted)'

    def test_only_none_means_no_dynamic_scan_ran(self):
        assert self._page_range_after({
            'effective_end_page': None, 'page_scan_stop_reason': None,
        }) == '1-10'


class TestReversedRange:
    def test_sequential_reversed_range_scans_nothing(self, fake_fetch, monkeypatch):
        # Every page of a reversed range sits past the floor, so a fresh listing
        # would otherwise extend the scan all the way to the cap.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        requested = fake_fetch({}, default_fresh=5)

        result = _run_sequential(end_page=2, start_page=5)

        assert requested == []
        assert result['all_index_results_phase1'] == []
        assert result['effective_end_page'] is None


class TestParallelEndOfContent:
    def test_reports_the_page_that_ended_the_scan_not_the_last_to_land(self, monkeypatch):
        # Floor pages go out together, so pages 2-3 still return after page 1
        # reports end-of-content. The scan ended at 1, and that is what the
        # result JSON and ReportSessions.EndPage must say.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)

        class _EmptyFirstPageBackend(_DynamicBackend):
            def results(self):
                for page_num in list(self._pending):
                    self._pending.remove(page_num)
                    task = SimpleNamespace(meta={'page_num': page_num})
                    if page_num == 1:
                        data = {
                            'html': '', 'has_movie_list': False,
                            'is_valid_empty': True, 'page_result': None, 'fresh': 0,
                        }
                    else:
                        data = parallel_mod._index_parse_fn(_html(5), task)
                    yield SimpleNamespace(
                        success=True, data=data, task=task,
                        worker_name='w1', error=None,
                    )

        backend = _EmptyFirstPageBackend({}, default_fresh=5)
        result = _run_parallel(backend, monkeypatch, end_page=3)

        assert result['page_scan_stop_reason'] == 'end-of-content'
        assert result['effective_end_page'] == 1


class TestUnreadableBudget:
    def test_a_larger_stop_after_is_not_preempted_by_the_empty_tolerance(self, monkeypatch):
        # max_consecutive_empty is 3; with PAGE_SCAN_STOP_AFTER=5 the scan must
        # spend its configured budget and report `unreadable`, not stop mute.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_STOP_AFTER', 5, raising=False)

        class _NoSleep:
            def sleep(self):
                pass

            def apply_volume_multiplier(self, *args, **kwargs):
                pass

        monkeypatch.setattr(index_mod, '_sleep_manager', lambda runtime=None: _NoSleep())
        requested = []

        def fetch(page_url, session, **kwargs):
            page_num = kwargs['page_num']
            requested.append(page_num)
            if page_num == 1:
                return (_html(5), True, False, kwargs['use_proxy'], kwargs['use_cf_bypass'], False)
            return (None, False, False, kwargs['use_proxy'], kwargs['use_cf_bypass'], False)

        monkeypatch.setattr(index_mod, 'fetch_index_page_with_fallback', fetch)

        result = _run_sequential(end_page=1)

        assert requested == [1, 2, 3, 4, 5, 6]
        assert result['page_scan_stop_reason'] == 'unreadable'


class _BanningBackend(_DynamicBackend):
    """Backend whose proxy pool dies on the first page.

    Models the real engine's contract after ``all_proxies_banned``: the queue as
    it stood is drained, the workers exit, and anything submitted afterwards has
    nobody to fetch it. ``results()`` here simply stops, so a page submitted
    post-ban shows up as a submission the run could never have completed.
    """

    def results(self):
        while self._pending:
            page_num = self._pending.popleft()
            task = SimpleNamespace(meta={'page_num': page_num})
            yield SimpleNamespace(
                success=False, data=None, task=task,
                worker_name='w1', error='all_proxies_banned',
            )


class TestProxyPoolExhausted:
    def test_a_total_ban_closes_the_extension_instead_of_submitting_into_it(
        self, monkeypatch,
    ):
        # With a one-page floor and stop_after=2, neither the fresh-free nor the
        # unreadable budget is spent after page 1, so the policy would happily
        # extend — into an engine with no workers left, which blocks forever
        # because the stalled-task flush only runs once the backend is done.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_STOP_AFTER', 2, raising=False)
        backend = _BanningBackend({})

        result = _run_parallel(backend, monkeypatch, end_page=1)

        assert backend.submitted_pages == [1]
        assert backend.marked_done is True
        # D9: a run cut short by infrastructure must say so, or it reads as a
        # clean scan that simply found nothing past page 1.
        assert result['page_scan_stop_reason'] == 'proxies-exhausted'
        # The ban landed before any observation; without recording the page here
        # the run would report an end page of 0.
        assert result['effective_end_page'] == 1

    def test_a_ban_abandons_a_deferred_fixed_range_too(self, monkeypatch):
        # An ad-hoc URL probes page 1 first and defers the rest in
        # pending_pages, and D7 disables the policy for it. Gating the ban
        # handler on policy.enabled would let those deferred pages go to an
        # engine with no workers left — the same hang, reached another way.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        backend = _BanningBackend({})

        _run_parallel(
            backend, monkeypatch, end_page=5,
            custom_url='https://javdb.com/actors/EvkJ',
        )

        assert backend.submitted_pages == [1]
        assert backend.marked_done is True


class _StrictBanningBackend(_BanningBackend):
    """Rejects a submit after mark_done(), exactly as the real backend does."""

    def mark_done(self):
        self.marked_done = True

    def submit(self, url, meta, entry_index, priority, login_only=False):
        if self.marked_done:
            raise RuntimeError('Cannot submit after mark_done()')
        super().submit(url, meta, entry_index, priority, login_only=login_only)


class TestProxyPoolExhaustedUnderParseAll:
    def test_the_sliding_window_stops_feeding_a_closed_backend(self, monkeypatch):
        # --all keeps its own submit path. The ban handler marks the backend
        # done, so the window refilling itself afterwards raises rather than
        # ending the run in a controlled way.
        monkeypatch.setattr(index_mod, 'PAGE_SCAN_DYNAMIC', True, raising=False)
        backend = _StrictBanningBackend({})

        _run_parallel(backend, monkeypatch, end_page=3, parse_all=True)

        assert backend.marked_done is True


class TestParseAllSummaryReporting:
    def _pages_pair(self, capsys, **kwargs):
        from javdb.spider.runtime import report as report_mod

        report_mod.generate_summary_report(
            runtime=None, phase_mode='1', parse_all=True,
            start_page=1, end_page=10, max_consecutive_empty=3,
            phase1_rows=[], phase2_rows=[], rows=[],
            use_history_for_loading=False, ignore_history=True,
            skipped_history_count=0, failed_count=0, no_new_torrents_count=0,
            csv_path='/tmp/out.csv', dry_run=False, use_history_for_saving=False,
            use_proxy=False, any_proxy_banned=False, any_proxy_banned_phase2=False,
            **kwargs,
        )
        return capsys.readouterr().out

    def test_an_infrastructure_stop_reaches_the_summary(self, capsys):
        out = self._pages_pair(capsys, page_scan_stop_reason='proxies-exhausted')
        assert 'proxies-exhausted' in out
        assert 'SPIDER_STAT_PAGE_SCAN_STOP_REASON=proxies-exhausted' in out

    def test_a_rule_derived_stop_does_not(self, capsys):
        # The rule never ran under --all, so `exhausted` would describe a
        # decision nobody took.
        out = self._pages_pair(capsys, page_scan_stop_reason='exhausted')
        assert 'to last page with results (exhausted)' not in out
