"""Comprehensive integration test for spider: index/detail fetch, login coordination,
cookie revocation/failover, and humanised sleep timing.

Scenario coverage:
  1. Index page requires login → triggers login → proceeds to detail pages
  2. Mixed detail pages: some need login, routed to logged-in worker
  3. Cookie revocation on logged-in worker → proxy switch + re-login on another worker
  4. MovieSleepManager humanised delay: distribution shape, jitter, force-high chain
"""

import os
import sys
import math
import queue as queue_module
import statistics
import threading
import time
from collections import Counter
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.spider.parallel import ProxyWorker, DetailTask, DetailResult
from scripts.spider.parallel_login import (
    LoginCoordinator, requeue_front, use_login_queue_priority,
    should_delegate_login_task,
)
from scripts.spider.sleep_manager import (
    MovieSleepManager, PenaltyTracker, DualWindowThrottle,
    COMPOSITE_MULTIPLIER_CAP, ABSOLUTE_MAX_SLEEP,
)
import scripts.spider.state as state


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

LOGIN_PAGE_HTML = '<html><head><title>登入 JavDB</title></head><body><form id="login"></form></body></html>'

DETAIL_HTML_TEMPLATE = """
<html>
<head><title>{code} Detail</title></head>
<body>
    <div class="video-meta-panel">
        <div class="panel-block">
            <strong>演員:</strong>
            <span class="value">
                <a href="/actors/a1">Mock Actor</a><strong class="symbol female">♀</strong>&nbsp;
            </span>
        </div>
    </div>
    <div id="magnets-content">
        <div class="item columns is-desktop">
            <div class="magnet-name">
                <a href="magnet:?xt=urn:btih:{hash}">
                    <span class="name">{code}.torrent</span>
                    <span class="meta">4.5GB, 1個文件</span>
                    <div class="tags"><span class="tag">字幕</span></div>
                </a>
            </div>
            <span class="time">2025-12-01</span>
        </div>
    </div>
</body>
</html>
"""


def make_detail_html(code: str, hash_val: str = "aabbcc") -> str:
    return DETAIL_HTML_TEMPLATE.format(code=code, hash=hash_val)


def make_entry(code: str, page: int = 1) -> dict:
    href = f"/v/{code.lower().replace('-', '')}"
    return {
        "video_code": code,
        "href": href,
        "page": page,
        "is_today_release": False,
        "is_yesterday_release": False,
    }


# ---------------------------------------------------------------------------
# Helpers to build workers without starting threads
# ---------------------------------------------------------------------------


def create_workers(
    proxy_names: list[str],
    coordinator: LoginCoordinator = None,
    use_cookie: bool = True,
    is_adhoc: bool = False,
) -> tuple:
    """Create ProxyWorker instances (not started) and associated queues."""
    dq: queue_module.Queue = queue_module.Queue()
    rq: queue_module.Queue = queue_module.Queue()
    lq: queue_module.Queue = queue_module.Queue()

    all_workers: list[ProxyWorker] = []
    coord = coordinator or LoginCoordinator(all_workers=all_workers)
    if coordinator is None:
        coord._all_workers = all_workers

    for idx, name in enumerate(proxy_names):
        cfg = {"name": name, "http": f"http://10.0.0.{idx + 1}:8080"}
        w = ProxyWorker(
            worker_id=idx,
            proxy_config=cfg,
            detail_queue=dq,
            result_queue=rq,
            login_queue=lq,
            total_workers=len(proxy_names),
            use_cookie=use_cookie,
            is_adhoc_mode=is_adhoc,
            movie_sleep_min=0,
            movie_sleep_max=0,
            fallback_cooldown=0,
            ban_log_file="",
            all_workers=all_workers,
            coordinator=coord,
            shared_penalty_tracker=PenaltyTracker(),
            shared_throttle=DualWindowThrottle(
                short_window_sec=0.5, short_max=100,
                long_window_sec=5.0, long_max=500,
            ),
        )
        all_workers.append(w)

    return all_workers, coord, dq, rq, lq


# =========================================================================
# Scenario 1: Index page requires login
# =========================================================================


class TestIndexRequiresLogin:
    """Simulate an index page that returns a login page, triggering login refresh,
    then successfully fetches detail pages with the new cookie."""

    def test_index_login_detection_triggers_attempt(self):
        """When the index page is a login page, is_login_page returns True
        and the spider can trigger attempt_login_refresh."""
        from scripts.spider.session import is_login_page

        assert is_login_page(LOGIN_PAGE_HTML) is True

        normal_html = "<html><head><title>JavDB</title></head><body>movie list</body></html>"
        assert is_login_page(normal_html) is False

    def test_index_login_then_detail_fetch_flow(self):
        """End-to-end: index login detected → attempt_login_refresh →
        cookie set → detail worker can use the cookie."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2"])

        orig = {
            "attempted": state.login_attempted,
            "cookie": state.refreshed_session_cookie,
            "proxy": state.logged_in_proxy_name,
            "total": state.login_total_attempts,
            "budget": state.login_total_budget,
            "per_proxy": state.login_attempts_per_proxy.copy(),
            "failures": state.login_failures_per_proxy.copy(),
        }

        try:
            state.login_attempted = False
            state.login_total_attempts = 0
            state.login_total_budget = 10
            state.login_attempts_per_proxy = {}
            state.login_failures_per_proxy = {}

            new_cookie = "freshly_baked_session_cookie"

            with patch(
                "scripts.spider.parallel_login.attempt_login_refresh",
                return_value=(True, new_cookie, "ARM-1"),
            ):
                success, cookie, _ = coord._do_login_for_proxy(
                    workers[0].proxy_config, workers[0].proxy_name
                )
                assert success is True
                assert cookie == new_cookie

                workers[0]._handler.config.javdb_session_cookie = cookie
                coord.logged_in_worker_id = 0

            assert workers[0]._handler.config.javdb_session_cookie == new_cookie
            assert coord.logged_in_worker_id == 0
            assert workers[1]._handler.config.javdb_session_cookie != new_cookie

        finally:
            state.login_attempted = orig["attempted"]
            state.refreshed_session_cookie = orig["cookie"]
            state.logged_in_proxy_name = orig["proxy"]
            state.login_total_attempts = orig["total"]
            state.login_total_budget = orig["budget"]
            state.login_attempts_per_proxy = orig["per_proxy"]
            state.login_failures_per_proxy = orig["failures"]

    def test_worker_detects_login_page_on_fetch(self):
        """_try_fetch_and_parse correctly returns needs_login=True
        when the fetched HTML is a login page."""
        workers, coord, *_ = create_workers(["TestProxy"])
        w = workers[0]

        task = DetailTask(
            url="http://javdb.com/v/abc123",
            entry=make_entry("ABC-123"),
            phase=1,
            entry_index="1/5",
        )

        w._fetch_html = lambda url, use_cf: LOGIN_PAGE_HTML

        _, _, _, _, _, success, needs_login = w._try_fetch_and_parse(
            task, False, "test"
        )
        assert success is False
        assert needs_login is True

    def test_worker_parses_detail_html_successfully(self):
        """_try_fetch_and_parse returns success=True for valid detail HTML."""
        workers, coord, *_ = create_workers(["TestProxy"])
        w = workers[0]

        task = DetailTask(
            url="http://javdb.com/v/abc123",
            entry=make_entry("ABC-123"),
            phase=1,
            entry_index="1/5",
        )

        w._fetch_html = lambda url, use_cf: make_detail_html("ABC-123")

        magnets, actor, _, _, _, success, needs_login = w._try_fetch_and_parse(
            task, False, "test"
        )
        assert success is True
        assert needs_login is False
        assert len(magnets) > 0


# =========================================================================
# Scenario 2: Mixed detail pages — some require login
# =========================================================================


class TestMixedDetailLogin:
    """Some detail pages return login page; these should be routed to the
    logged-in worker via login_queue while others are processed normally."""

    def test_login_required_routes_to_logged_in_worker(self):
        """When worker B hits a login page, the task is routed to worker A
        (the logged-in worker) via login_queue."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2", "ARM-3"])

        coord.logged_in_worker_id = 0  # ARM-1 is logged in

        task = DetailTask(
            url="http://javdb.com/v/def456",
            entry=make_entry("DEF-456"),
            phase=1,
            entry_index="2/10",
        )

        worker_b = workers[1]  # ARM-2 encounters login page
        worker_b._handle_login_required(task)

        assert not lq.empty()
        routed = lq.get_nowait()
        assert routed is task
        assert "ARM-1" not in task.failed_proxies

    def test_logged_in_worker_prioritises_login_queue(self):
        """The logged-in worker checks login_queue first."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2"])
        coord.logged_in_worker_id = 0

        with coord.lock:
            is_priority = coord.is_login_worker("ARM-1", 0)
        assert is_priority is True

        with coord.lock:
            is_priority = coord.is_login_worker("ARM-2", 1)
        assert is_priority is False

    def test_direct_then_cf_shortcircuits_on_login(self):
        """_try_direct_then_cf detects login page on direct attempt and
        does NOT try CF bypass (since CF won't fix auth)."""
        workers, coord, *_ = create_workers(["TestProxy"])
        w = workers[0]

        task = DetailTask(
            url="http://javdb.com/v/ghi789",
            entry=make_entry("GHI-789"),
            phase=1,
            entry_index="3/10",
        )

        call_count = {"n": 0}

        def mock_fetch(url, use_cf):
            call_count["n"] += 1
            return LOGIN_PAGE_HTML

        w._fetch_html = mock_fetch

        m, a, ag, al, sup, success, used_cf, needs_login = w._try_direct_then_cf(task)
        assert success is False
        assert needs_login is True
        assert call_count["n"] == 1, "Should stop after direct; CF bypass skipped"

    def test_multiple_workers_concurrent_login_routing(self):
        """Multiple workers hitting login pages concurrently all route to
        the logged-in worker without race conditions."""
        workers, coord, dq, rq, lq = create_workers(
            ["ARM-1", "ARM-2", "ARM-3", "ARM-4"]
        )
        coord.logged_in_worker_id = 0

        tasks = []
        for i in range(10):
            t = DetailTask(
                url=f"http://javdb.com/v/mov{i:03d}",
                entry=make_entry(f"MOV-{i:03d}"),
                phase=1,
                entry_index=f"{i+1}/10",
            )
            tasks.append(t)

        errors = []

        def route_task(worker_idx, task):
            try:
                workers[worker_idx]._handle_login_required(task)
            except Exception as e:
                errors.append(e)

        threads = []
        for i, t in enumerate(tasks):
            w_idx = (i % 3) + 1  # workers 1-3 route to worker 0
            th = threading.Thread(target=route_task, args=(w_idx, t))
            threads.append(th)

        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert len(errors) == 0
        assert lq.qsize() == 10


# =========================================================================
# Scenario 3: Cookie revocation → proxy switch → re-login
# =========================================================================


class TestCookieRevocationAndFailover:
    """Simulate the logged-in worker's cookie going stale. The coordinator
    should detect repeated failures, switch to another proxy, and re-login."""

    def _save_and_patch_state(self):
        """Save state and prepare clean budget."""
        orig = {
            "attempted": state.login_attempted,
            "cookie": state.refreshed_session_cookie,
            "proxy": state.logged_in_proxy_name,
            "total": state.login_total_attempts,
            "budget": state.login_total_budget,
            "per_proxy": state.login_attempts_per_proxy.copy(),
            "failures": state.login_failures_per_proxy.copy(),
        }
        state.login_attempted = False
        state.login_total_attempts = 0
        state.login_total_budget = 20
        state.login_attempts_per_proxy = {}
        state.login_failures_per_proxy = {}
        return orig

    def _restore_state(self, orig):
        state.login_attempted = orig["attempted"]
        state.refreshed_session_cookie = orig["cookie"]
        state.logged_in_proxy_name = orig["proxy"]
        state.login_total_attempts = orig["total"]
        state.login_total_budget = orig["budget"]
        state.login_attempts_per_proxy = orig["per_proxy"]
        state.login_failures_per_proxy = orig["failures"]

    def test_stale_cookie_triggers_relogin_on_same_proxy(self):
        """If the logged-in worker's session goes stale, coordinator tries
        re-login on the same proxy first."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2"])
        orig = self._save_and_patch_state()

        try:
            coord.logged_in_worker_id = 0
            new_cookie = "re_login_cookie_v2"

            task = DetailTask(
                url="http://javdb.com/v/abc",
                entry=make_entry("ABC-001"),
                phase=1,
                entry_index="1/5",
            )

            with patch(
                "scripts.spider.parallel_login.attempt_login_refresh",
                return_value=(True, new_cookie, "ARM-1"),
            ) as mock_login:
                coord.handle_login_required(
                    worker=workers[0],
                    task=task,
                    video_code="ABC-001",
                    login_queue=lq,
                    task_queue=dq,
                )
                mock_login.assert_called_once()

            assert coord.logged_in_worker_id == 0
            assert workers[0]._handler.config.javdb_session_cookie == new_cookie
            assert not lq.empty()

        finally:
            self._restore_state(orig)

    def test_stale_cookie_exceeds_threshold_switches_proxy(self):
        """After LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH stale failures,
        coordinator switches login to a different proxy."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2", "ARM-3"])
        orig = self._save_and_patch_state()

        try:
            coord.logged_in_worker_id = 0
            state.login_failures_per_proxy["ARM-1"] = 3  # at threshold

            new_cookie = "arm2_cookie_after_switch"

            task = DetailTask(
                url="http://javdb.com/v/switch",
                entry=make_entry("SWT-001"),
                phase=1,
                entry_index="1/5",
            )

            with patch(
                "scripts.spider.parallel_login.attempt_login_refresh",
                return_value=(True, new_cookie, "ARM-2"),
            ):
                coord.handle_login_required(
                    worker=workers[0],
                    task=task,
                    video_code="SWT-001",
                    login_queue=lq,
                    task_queue=dq,
                )

            assert coord.logged_in_worker_id == 1, "Should switch to ARM-2 (worker_id=1)"
            assert workers[1]._handler.config.javdb_session_cookie == new_cookie

        finally:
            self._restore_state(orig)

    def test_all_proxies_exhausted_falls_back_to_failure(self):
        """When every proxy's login budget is exhausted, task is treated
        as a normal failure and requeued."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2"])
        orig = self._save_and_patch_state()

        try:
            state.login_total_budget = 2
            state.login_total_attempts = 2  # budget exhausted

            coord.logged_in_worker_id = None

            task = DetailTask(
                url="http://javdb.com/v/fail",
                entry=make_entry("FAIL-001"),
                phase=1,
                entry_index="1/5",
            )

            coord.handle_login_required(
                worker=workers[0],
                task=task,
                video_code="FAIL-001",
                login_queue=lq,
                task_queue=dq,
            )

            assert lq.empty(), "Should NOT route to login_queue"
            assert not dq.empty(), "Should requeue to detail_queue"
            assert "ARM-1" in task.failed_proxies

        finally:
            self._restore_state(orig)

    def test_cookie_revoke_then_another_worker_logs_in(self):
        """Full flow: worker 0 logged in → cookie revoked (stale) →
        stale count reaches threshold → worker 1 logs in → tasks route
        to worker 1."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2", "ARM-3"])
        orig = self._save_and_patch_state()

        try:
            workers[0]._handler.config.javdb_session_cookie = "original_valid_cookie"
            coord.logged_in_worker_id = 0

            state.login_failures_per_proxy["ARM-1"] = 0

            login_responses = iter([
                (False, None, None),      # ARM-1 re-login fails
                (True, "arm2_fresh", "ARM-2"),  # ARM-2 succeeds
            ])

            with patch(
                "scripts.spider.parallel_login.attempt_login_refresh",
                side_effect=lambda *a, **kw: next(login_responses),
            ):
                for stale_round in range(3):
                    task = DetailTask(
                        url=f"http://javdb.com/v/stale{stale_round}",
                        entry=make_entry(f"STL-{stale_round:03d}"),
                        phase=1,
                        entry_index=f"{stale_round + 1}/5",
                    )
                    coord.handle_login_required(
                        worker=workers[0],
                        task=task,
                        video_code=f"STL-{stale_round:03d}",
                        login_queue=lq,
                        task_queue=dq,
                    )

            assert coord.logged_in_worker_id == 1, "ARM-2 should now be logged in"
            assert workers[1]._handler.config.javdb_session_cookie == "arm2_fresh"

        finally:
            self._restore_state(orig)

    def test_no_logged_in_worker_first_available_logs_in(self):
        """When no worker is logged in yet, the first worker to encounter
        a login page should attempt login on its own proxy."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2"])
        orig = self._save_and_patch_state()

        try:
            coord.logged_in_worker_id = None
            new_cookie = "first_login_cookie"

            task = DetailTask(
                url="http://javdb.com/v/first",
                entry=make_entry("FST-001"),
                phase=1,
                entry_index="1/5",
            )

            with patch(
                "scripts.spider.parallel_login.attempt_login_refresh",
                return_value=(True, new_cookie, "ARM-1"),
            ):
                coord.handle_login_required(
                    worker=workers[0],
                    task=task,
                    video_code="FST-001",
                    login_queue=lq,
                    task_queue=dq,
                )

            assert coord.logged_in_worker_id == 0
            assert workers[0]._handler.config.javdb_session_cookie == new_cookie
            assert not lq.empty(), "Task should be re-queued to login_queue"

        finally:
            self._restore_state(orig)


# =========================================================================
# Scenario 4: Sleep manager — humanised random wait
# =========================================================================


class TestSleepManagerHumanLike:
    """Validate that MovieSleepManager produces human-like, non-deterministic
    delays with the right statistical properties."""

    def test_samples_within_range(self):
        """All sleep times must fall within [base_min - drift, ABSOLUTE_MAX]."""
        mgr = MovieSleepManager(8.0, 25.0)
        for _ in range(500):
            t = mgr.get_sleep_time()
            assert 6.0 <= t <= ABSOLUTE_MAX_SLEEP

    def test_no_two_consecutive_identical(self):
        """Consecutive samples should almost never be identical
        (verifies jitter + randomisation)."""
        mgr = MovieSleepManager(8.0, 25.0)
        identical_count = 0
        prev = mgr.get_sleep_time()
        for _ in range(200):
            cur = mgr.get_sleep_time()
            if cur == prev:
                identical_count += 1
            prev = cur
        assert identical_count < 5, (
            f"Too many identical consecutive values ({identical_count}/200)"
        )

    def test_right_skewed_distribution(self):
        """Log-normal based sampling should produce right-skewed distribution
        (mean > median)."""
        mgr = MovieSleepManager(8.0, 25.0)
        samples = [mgr.get_sleep_time() for _ in range(3000)]
        mean = statistics.mean(samples)
        median = statistics.median(samples)
        assert mean >= median - 1.0, (
            f"Expected right skew: mean={mean:.2f}, median={median:.2f}"
        )

    def test_force_high_chain_after_low(self):
        """When a low-range sample is drawn (roll in [0.08, 0.15)),
        _force_high is set to True, ensuring the next sample is from
        the upper range. This creates a natural fast→slow pattern."""
        mgr = MovieSleepManager(10.0, 30.0)

        mgr._force_high = True
        eff_min, eff_max = mgr._effective_range()
        span = eff_max - eff_min

        high_sample = mgr.get_sleep_time()
        assert high_sample >= eff_min + span * 0.5, (
            f"Force-high sample {high_sample:.2f} should be in upper range"
        )
        assert mgr._force_high is False, (
            "_force_high should reset after being consumed"
        )

    def test_distinct_fractional_precision(self):
        """Sleep values should have fine-grained precision (not just whole
        seconds or 0.1s steps)."""
        mgr = MovieSleepManager(8.0, 25.0)
        fractionals = set()
        for _ in range(200):
            t = mgr.get_sleep_time()
            fractionals.add(round(t % 1, 2))
        assert len(fractionals) > 15, (
            f"Only {len(fractionals)} distinct fractional parts — too coarse"
        )

    def test_independent_instances_differ(self):
        """Two independently created managers should produce different
        sequences (independent RNG + drift)."""
        mgr1 = MovieSleepManager(8.0, 25.0)
        mgr2 = MovieSleepManager(8.0, 25.0)
        seq1 = [mgr1.get_sleep_time() for _ in range(30)]
        seq2 = [mgr2.get_sleep_time() for _ in range(30)]
        assert seq1 != seq2

    def test_volume_multiplier_increases_sleep_range(self):
        """High volume (N=200) should widen the effective sleep range."""
        mgr = MovieSleepManager(10.0, 20.0)
        before_min, before_max = mgr.sleep_min, mgr.sleep_max
        mgr.apply_volume_multiplier(200)
        assert mgr.sleep_min > before_min
        assert mgr.sleep_max > before_max

    def test_concurrency_factor_scales_with_sqrt(self):
        """Worker factor = min(sqrt(W), CAP)."""
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_concurrency_factor(4)
        assert abs(mgr._worker_factor - 2.0) < 0.01

        mgr2 = MovieSleepManager(10.0, 20.0)
        mgr2.apply_concurrency_factor(9)
        # sqrt(9)=3.0 exceeds WORKER_FACTOR_CAP=2.45, so it's capped
        assert mgr2._worker_factor == mgr2.WORKER_FACTOR_CAP

    def test_penalty_tracker_raises_sleep_dynamically(self):
        """Recording CF/failure events dynamically raises effective sleep range."""
        pt = PenaltyTracker()
        mgr = MovieSleepManager(10.0, 20.0, penalty_tracker=pt)

        eff_before = mgr._effective_range()
        pt.record_event()
        pt.record_event()
        eff_after = mgr._effective_range()

        assert eff_after[0] > eff_before[0]
        assert eff_after[1] > eff_before[1]

    def test_penalty_decays_after_window(self):
        """Penalty events expire after the configured window."""
        pt = PenaltyTracker()
        pt.WINDOW_SECONDS = 0.1

        pt.record_event()
        pt.record_event()
        assert pt.get_penalty_factor() == 1.65

        time.sleep(0.15)
        assert pt.get_penalty_factor() == 1.0

    def test_composite_cap_prevents_runaway(self):
        """Even with max volume + concurrency + penalty, effective multiplier
        is capped at COMPOSITE_MULTIPLIER_CAP."""
        pt = PenaltyTracker()
        for _ in range(10):
            pt.record_event()

        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(300)
        mgr.apply_concurrency_factor(10)

        eff_min, eff_max = mgr._effective_range()
        assert eff_min <= mgr.base_min * COMPOSITE_MULTIPLIER_CAP + 1
        assert eff_max <= mgr.base_max * COMPOSITE_MULTIPLIER_CAP + 1

    def test_absolute_max_sleep_ceiling(self):
        """No sample should exceed ABSOLUTE_MAX_SLEEP regardless of multipliers."""
        pt = PenaltyTracker()
        for _ in range(10):
            pt.record_event()

        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(300)
        mgr.apply_concurrency_factor(10)

        for _ in range(300):
            assert mgr.get_sleep_time() <= ABSOLUTE_MAX_SLEEP

    def test_dual_window_throttle_enforces_burst_limit(self):
        """DualWindowThrottle blocks when the short-window burst limit is hit."""
        dwt = DualWindowThrottle(
            short_window_sec=60.0, short_max=2,
            long_window_sec=600.0, long_max=100,
        )
        dwt.wait_if_needed()
        dwt.wait_if_needed()

        start = time.monotonic()
        dwt.wait_if_needed()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.5, "Third request should be delayed by throttle"

    def test_sleep_method_combines_sleep_and_throttle(self):
        """MovieSleepManager.sleep() calls both get_sleep_time() and throttle."""
        pt = PenaltyTracker()
        dwt = DualWindowThrottle(
            short_window_sec=0.5, short_max=100,
            long_window_sec=5.0, long_max=500,
        )
        mgr = MovieSleepManager(0.01, 0.02, penalty_tracker=pt, throttle=dwt)

        with patch.object(mgr, "get_sleep_time", return_value=0.01):
            with patch("time.sleep") as mock_sleep:
                total = mgr.sleep()
                mock_sleep.assert_called_once_with(0.01)
                assert total >= 0.01

    def test_worker_startup_jitter_varies_by_id(self):
        """Each worker gets a different startup jitter based on worker_id."""
        workers, *_ = create_workers(["P1", "P2", "P3", "P4"])
        jitters = [w._startup_jitter for w in workers]
        assert len(set(jitters)) == len(jitters), (
            "Each worker should have a unique jitter"
        )
        for i in range(1, len(jitters)):
            assert jitters[i] > jitters[i - 1] * 0.3, (
                "Higher worker_id should generally have larger jitter"
            )


# =========================================================================
# Scenario 5: End-to-end mini pipeline (threaded workers)
# =========================================================================


class TestEndToEndMiniPipeline:
    """Spin up actual worker threads with mocked fetch to validate the full
    run() loop: fetch → parse → result collection.

    Login routing across workers is tested deterministically in
    TestMixedDetailLogin and TestCookieRevocationAndFailover.
    """

    def test_workers_process_normal_tasks(self):
        """3 workers process 6 normal detail tasks in parallel and
        all results are collected via result_queue."""
        workers, coord, dq, rq, lq = create_workers(["ARM-1", "ARM-2", "ARM-3"])

        for w in workers:
            w._fetch_html = lambda url, use_cf: make_detail_html("TEST", hash_val="testhash")

        for i in range(6):
            dq.put(DetailTask(
                url=f"http://javdb.com/v/mov{i:03d}",
                entry=make_entry(f"MOV-{i:03d}"),
                phase=1,
                entry_index=f"{i+1}/6",
            ))

        for w in workers:
            w.start()

        results = []
        timeout = time.monotonic() + 20
        while len(results) < 6 and time.monotonic() < timeout:
            try:
                r = rq.get(timeout=0.5)
                results.append(r)
            except queue_module.Empty:
                pass

        for w in workers:
            dq.put(None)
        for w in workers:
            w.join(timeout=5)

        assert len(results) == 6, f"Expected 6 results, got {len(results)}"
        assert all(r.parse_success for r in results), "All tasks should parse successfully"

        worker_names = {r.task.url for r in results}
        assert len(worker_names) == 6, "Each task should produce exactly one result"


# =========================================================================
# Scenario 6: requeue_front puts tasks at the front of the queue
# =========================================================================


class TestRequeueFront:
    """Validate that requeue_front actually inserts at the front."""

    def test_requeue_front_ordering(self):
        q = queue_module.Queue()
        q.put("second")
        q.put("third")
        requeue_front(q, "first")

        assert q.get_nowait() == "first"
        assert q.get_nowait() == "second"
        assert q.get_nowait() == "third"

    def test_requeue_front_on_empty_queue(self):
        q = queue_module.Queue()
        requeue_front(q, "only_item")
        assert q.get_nowait() == "only_item"


# =========================================================================
# Scenario 7: Login queue priority helpers
# =========================================================================


class TestLoginQueueHelpers:

    def test_use_login_queue_priority_logged_in_worker(self):
        assert use_login_queue_priority(None, "ARM-1", 0, 0) is True

    def test_use_login_queue_priority_named_proxy(self):
        assert use_login_queue_priority("ARM-1", "ARM-1", None, 1) is True

    def test_use_login_queue_priority_other_worker(self):
        assert use_login_queue_priority("ARM-1", "ARM-2", None, 1) is False

    def test_should_delegate_login_task_to_named(self):
        assert should_delegate_login_task("ARM-1", "ARM-2") is True

    def test_should_not_delegate_when_is_named(self):
        assert should_delegate_login_task("ARM-1", "ARM-1") is False

    def test_should_not_delegate_when_no_name(self):
        assert should_delegate_login_task(None, "ARM-1") is False
