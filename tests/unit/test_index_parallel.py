"""Tests for index-parallel logic: _check_stop_condition, _PriorityTaskQueue, requeue_front."""

from __future__ import annotations

import os
import queue as queue_module
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.parsing.models import IndexPageResult
from javdb.pipeline.index_family_blacklist import filter_blacklisted_families
from javdb.spider.fetch.index_parallel import _check_stop_condition
from javdb.spider.fetch import index_parallel
from javdb.spider.fetch.fetch_engine import (
    EngineTask,
    _PriorityTaskQueue,
)
from javdb.spider.fetch.login_coordinator import requeue_front
from tests.unit.index_blacklist_helpers import _entry, spy_filter, spy_select


# ---------------------------------------------------------------------------
# Lightweight EngineResult stub (avoids importing the full engine machinery)
# ---------------------------------------------------------------------------

@dataclass
class _FakeResult:
    success: bool = True
    data: Optional[dict] = None
    error: Optional[str] = None
    worker_name: str = ''
    task: Any = None


def _content_result() -> _FakeResult:
    return _FakeResult(success=True, data={'has_movie_list': True, 'html': '<html/>'})


def _valid_empty_result() -> _FakeResult:
    return _FakeResult(success=True, data={'has_movie_list': False, 'is_valid_empty': True})


def _empty_no_flag_result() -> _FakeResult:
    """Empty page without is_valid_empty (e.g. parse failure)."""
    return _FakeResult(success=True, data={'has_movie_list': False, 'is_valid_empty': False})


def _failed_result(error: str = 'timeout') -> _FakeResult:
    return _FakeResult(success=False, error=error)


class _FakeBackend:
    def __init__(self, results: Iterable[Any]) -> None:
        self._results: list[Any] = list(results)
        self.submitted: list[tuple[str, dict[str, Any], str, int]] = []
        self.marked_done: bool = False
        self.started: bool = False
        self.shutdown_called: bool = False
        self.export_called: bool = False

    def start(self) -> None:
        self.started = True

    def submit(self, url: str, meta: dict[str, Any], entry_index: str, priority: int) -> None:
        self.submitted.append((url, meta, entry_index, priority))

    def mark_done(self) -> None:
        self.marked_done = True

    def results(self) -> Iterator[Any]:
        yield from self._results

    def shutdown(self) -> None:
        self.shutdown_called = True

    def export_login_state(self) -> None:
        self.export_called = True


def _parallel_page_result() -> IndexPageResult:
    return IndexPageResult(
        has_movie_list=True,
        movies=[
            _entry("Wifey.2026.05.30", "western_studio_date"),
            _entry("ABC-123", "classic_hyphenated"),
        ],
    )


def _fake_result(page_num: int, html: str) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        data={"has_movie_list": True, "html": html, "is_valid_empty": False},
        error=None,
        worker_name=f"w{page_num}",
        task=SimpleNamespace(meta={"page_num": page_num}),
    )


# ---------------------------------------------------------------------------
# Tests for _check_stop_condition
# ---------------------------------------------------------------------------


class TestCheckStopCondition:

    def test_contiguous_content_then_valid_empty(self):
        """Pages 1-3 content, page 4 valid_empty → stop immediately at page 4."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _content_result(),
            3: _content_result(),
            4: _valid_empty_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is True

    def test_gap_in_sequence_returns_false(self):
        """Pages 1, 3, 4 present but page 2 missing → can't evaluate, return False."""
        results: Dict[int, Any] = {
            1: _content_result(),
            3: _content_result(),
            4: _valid_empty_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is False

    def test_consecutive_empty_threshold(self):
        """Pages 1 content, 2-4 empty (no valid_empty flag) → stop at threshold=3."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _empty_no_flag_result(),
            3: _empty_no_flag_result(),
            4: _empty_no_flag_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is True

    def test_consecutive_empty_below_threshold(self):
        """Two empty pages < threshold=3 → return False."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _empty_no_flag_result(),
            3: _empty_no_flag_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is False

    def test_empty_results_returns_false(self):
        assert _check_stop_condition({}, start_page=1, max_consecutive_empty=3) is False

    def test_start_page_not_in_results(self):
        """Only higher pages present → can't start scanning, return False."""
        results: Dict[int, Any] = {
            5: _valid_empty_result(),
            6: _valid_empty_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=1) is False

    def test_valid_empty_stops_regardless_of_threshold(self):
        """A single valid_empty page right after content should stop."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _valid_empty_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=10) is True

    def test_failed_pages_count_toward_consecutive(self):
        """Failed fetches should count as empty toward the threshold."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _failed_result(),
            3: _failed_result(),
            4: _failed_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is True

    def test_content_resets_consecutive_counter(self):
        """Content pages reset the consecutive empty counter."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _empty_no_flag_result(),
            3: _content_result(),
            4: _empty_no_flag_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=2) is False

    def test_out_of_order_arrival_scenario(self):
        """Simulate the adhoc scenario: pages arrive out of order due to parallel fetch.

        Pages 1-3 have content, page 4 is valid_empty.
        Higher pages (5, 6, 8, 10, 11) arrived first but shouldn't affect
        the result — only contiguous pages from start_page matter.
        """
        results: Dict[int, Any] = {
            5: _valid_empty_result(),
            6: _valid_empty_result(),
            11: _valid_empty_result(),
            8: _valid_empty_result(),
            10: _valid_empty_result(),
            1: _content_result(),
            4: _valid_empty_result(),
            3: _content_result(),
        }
        # Page 2 not yet received → can't see through to page 4
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is False

        # Page 2 arrives → contiguous sequence 1,2,3 (content), 4 (valid_empty) → stop
        results[2] = _content_result()
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is True

    def test_start_page_is_valid_empty(self):
        """Edge case: first page is already valid_empty."""
        results: Dict[int, Any] = {
            1: _valid_empty_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is True

    def test_all_content_no_stop(self):
        """All pages have content and sequence continues → no stop."""
        results: Dict[int, Any] = {
            1: _content_result(),
            2: _content_result(),
            3: _content_result(),
        }
        assert _check_stop_condition(results, start_page=1, max_consecutive_empty=3) is False


# ---------------------------------------------------------------------------
# Tests for _PriorityTaskQueue
# ---------------------------------------------------------------------------


class TestPriorityTaskQueue:

    def test_dequeue_order_by_priority(self):
        """Tasks are dequeued in ascending priority order."""
        pq = _PriorityTaskQueue()
        pq.put(EngineTask(url='p10', priority=10))
        pq.put(EngineTask(url='p1', priority=1))
        pq.put(EngineTask(url='p5', priority=5))

        assert pq.get_nowait().url == 'p1'
        assert pq.get_nowait().url == 'p5'
        assert pq.get_nowait().url == 'p10'

    def test_fifo_within_same_priority(self):
        """Tasks with equal priority preserve insertion order."""
        pq = _PriorityTaskQueue()
        pq.put(EngineTask(url='a', priority=0))
        pq.put(EngineTask(url='b', priority=0))
        pq.put(EngineTask(url='c', priority=0))

        assert pq.get_nowait().url == 'a'
        assert pq.get_nowait().url == 'b'
        assert pq.get_nowait().url == 'c'

    def test_none_sentinel_dequeued_last(self):
        """None sentinels (shutdown signals) always come after real tasks."""
        pq = _PriorityTaskQueue()
        pq.put(None)
        pq.put(EngineTask(url='task', priority=999))

        assert pq.get_nowait().url == 'task'
        assert pq.get_nowait() is None

    def test_qsize(self):
        pq = _PriorityTaskQueue()
        assert pq.qsize() == 0
        pq.put(EngineTask(url='x', priority=1))
        assert pq.qsize() == 1
        pq.get_nowait()
        assert pq.qsize() == 0

    def test_empty_get_raises(self):
        pq = _PriorityTaskQueue()
        with pytest.raises(queue_module.Empty):
            pq.get_nowait()

    def test_is_priority_queue_marker(self):
        pq = _PriorityTaskQueue()
        assert pq._is_priority_queue is True

    def test_simulates_index_page_ordering(self):
        """Simulate: pages 1-10 submitted, then some re-queued after ban.

        Even with interleaved re-queues, pages should come out in ascending
        page order.
        """
        pq = _PriorityTaskQueue()
        for p in range(1, 11):
            pq.put(EngineTask(url=f'page-{p}', priority=p))

        t1 = pq.get_nowait()
        assert t1.url == 'page-1'
        t2 = pq.get_nowait()
        assert t2.url == 'page-2'

        # Pages 1 and 2 get re-queued (proxy ban)
        pq.put(t1)
        pq.put(t2)

        out = []
        while pq.qsize() > 0:
            out.append(pq.get_nowait())

        urls = [t.url for t in out]
        assert urls == [f'page-{p}' for p in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]


def test_parallel_multi_page_applies_blacklist_once_per_page_and_preserves_page_order(monkeypatch, tmp_path):
    backend = _FakeBackend([
        _fake_result(2, "html-2"),
        _fake_result(1, "html-1"),
    ])
    monkeypatch.setattr(index_parallel, "build_parallel_index_backend", lambda **_kwargs: backend)
    monkeypatch.setattr(index_parallel, "parse_index_page", lambda _html, _page_num: _parallel_page_result())
    monkeypatch.setattr(
        index_parallel,
        "_sentinel_field_health",
        SimpleNamespace(start_run=lambda: None, current=lambda: None),
    )
    monkeypatch.setattr(index_parallel, "_check_stop_condition", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(index_parallel, "detect_url_type", lambda *_args, **_kwargs: "actors")
    monkeypatch.setattr(index_parallel, "generate_output_csv_name_from_html", lambda *_args, **_kwargs: "resolved.csv")
    summary_calls = []
    monkeypatch.setattr(
        index_parallel,
        "log_family_blacklist_summary",
        lambda _logger, counts: summary_calls.append(
            ("INDEX FAMILY BLACKLIST SUMMARY", [("total", sum(counts.values())), *sorted(counts.items())])
        )
        if counts
        else None,
    )

    filter_calls = []
    original_filter = filter_blacklisted_families

    phase_calls = []

    monkeypatch.setattr(index_parallel, "filter_blacklisted_families", spy_filter(filter_calls, original_filter))
    monkeypatch.setattr(index_parallel, "select_index_entries", spy_select(phase_calls, include_page_num=True))
    monkeypatch.setattr(
        index_parallel,
        "load_daily_family_blacklist",
        lambda custom_url: {"western_studio_date"} if custom_url is None else set(),
    )

    result = index_parallel.fetch_all_index_pages_parallel(
        runtime=None,
        start_page=1,
        end_page=2,
        parse_all=False,
        phase_mode="all",
        custom_url=None,
        ignore_release_date=False,
        use_proxy=False,
        use_cf_bypass=False,
        max_consecutive_empty=1,
        output_csv="out.csv",
        output_dated_dir=str(tmp_path),
        csv_path=str(tmp_path / "out.csv"),
        user_specified_output=True,
        cancel_event=None,
    )

    assert backend.started is True
    assert backend.marked_done is True
    assert backend.shutdown_called is True
    assert backend.export_called is True
    assert filter_calls == [
        (["Wifey.2026.05.30", "ABC-123"], {"western_studio_date"}),
        (["Wifey.2026.05.30", "ABC-123"], {"western_studio_date"}),
    ]
    assert summary_calls == [
        ("INDEX FAMILY BLACKLIST SUMMARY", [("total", 2), ("western_studio_date", 2)])
    ]
    assert phase_calls == [
        (1, 1, ["ABC-123"]),
        (1, 2, ["ABC-123"]),
        (2, 1, ["ABC-123"]),
        (2, 2, ["ABC-123"]),
    ]
    assert [entry["href"] for entry in result["all_index_results_phase1"]] == ["/v/ABC-123", "/v/ABC-123"]
    assert [entry["href"] for entry in result["all_index_results_phase2"]] == ["/v/ABC-123", "/v/ABC-123"]


# ---------------------------------------------------------------------------
# Tests for requeue_front with priority queue
# ---------------------------------------------------------------------------


class TestRequeueFrontPriorityQueue:

    def test_requeue_preserves_priority_order(self):
        """requeue_front on a priority queue respects the task's priority."""
        pq = _PriorityTaskQueue()
        pq.put(EngineTask(url='page-5', priority=5))

        task = EngineTask(url='page-2', priority=2)
        requeue_front(pq, task)

        assert pq.get_nowait().url == 'page-2'
        assert pq.get_nowait().url == 'page-5'

    def test_requeue_on_regular_queue_still_works(self):
        """requeue_front on a regular Queue still uses appendleft."""
        q: queue_module.Queue = queue_module.Queue()
        q.put(EngineTask(url='first'))

        task = EngineTask(url='requeued')
        requeue_front(q, task)

        assert q.get_nowait().url == 'requeued'
        assert q.get_nowait().url == 'first'
