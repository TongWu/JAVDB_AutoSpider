"""Unit tests for spider detail execution backends."""

from __future__ import annotations

import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from scripts.spider.fetch.backend import FetchRuntimeState
from scripts.spider.fetch.fetch_engine import EngineTask
from scripts.spider.fetch.sequential_backend import SequentialFetchBackend


def make_task(index: str) -> EngineTask:
    return EngineTask(
        url=f'https://javdb.com/v/{index.replace("/", "-")}',
        entry_index=index,
        meta={},
    )


def test_sequential_backend_success_updates_runtime_state_and_payload(monkeypatch):
    import scripts.spider.fetch.sequential_backend as sb

    monkeypatch.setattr(
        sb,
        'fetch_detail_page_with_fallback',
        lambda *_args, **_kwargs: (
            ['magnet-1'],
            'Actor',
            'F',
            '/actors/a1',
            'Support',
            True,
            True,
            True,
        ),
    )

    backend = SequentialFetchBackend(
        object(),
        use_proxy=False,
        use_cf_bypass=False,
        use_cookie=True,
        is_adhoc_mode=False,
    )
    backend.submit_task(make_task('1/1'))
    backend.mark_done()

    result = next(backend.results())

    assert result.success is True
    assert result.used_cf is True
    assert result.data == {
        'magnets': ['magnet-1'],
        'actor_info': 'Actor',
        'actor_gender': 'F',
        'actor_link': '/actors/a1',
        'supporting': 'Support',
    }
    assert backend.runtime_state() == FetchRuntimeState(
        use_proxy=True,
        use_cf_bypass=True,
    )


def test_sequential_backend_failure_triggers_next_movie_sleep(monkeypatch):
    import scripts.spider.fetch.sequential_backend as sb

    responses = iter(
        [
            ([], '', '', '', '', False, False, False),
            (['magnet-2'], 'Actor', '', '', '', True, False, False),
        ]
    )
    sleep_calls: list[str] = []

    monkeypatch.setattr(
        sb,
        'fetch_detail_page_with_fallback',
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        sb.movie_sleep_mgr,
        'sleep',
        lambda: sleep_calls.append('movie'),
    )

    backend = SequentialFetchBackend(
        object(),
        use_proxy=False,
        use_cf_bypass=False,
        use_cookie=False,
        is_adhoc_mode=False,
    )
    backend.submit_task(make_task('1/2'))
    backend.submit_task(make_task('2/2'))
    backend.mark_done()

    results = backend.results()
    first = next(results)
    assert first.success is False
    first.acknowledge('failed')

    second = next(results)
    assert second.success is True
    assert sleep_calls == ['movie']
    second.acknowledge('reported')


def test_sequential_backend_skip_and_cf_fallback_keep_original_pacing(monkeypatch):
    import scripts.spider.fetch.sequential_backend as sb

    responses = iter(
        [
            (['magnet-1'], 'Actor', '', '', '', True, True, True),
            (['magnet-2'], 'Actor', '', '', '', True, True, True),
            (['magnet-3'], 'Actor', '', '', '', True, True, True),
        ]
    )
    movie_sleep_calls: list[str] = []

    monkeypatch.setattr(
        sb,
        'fetch_detail_page_with_fallback',
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(
        sb.movie_sleep_mgr,
        'sleep',
        lambda: movie_sleep_calls.append('movie'),
    )

    backend = SequentialFetchBackend(
        object(),
        use_proxy=False,
        use_cf_bypass=False,
        use_cookie=False,
        is_adhoc_mode=False,
    )
    backend.submit_task(make_task('1/3'))
    backend.submit_task(make_task('2/3'))
    backend.submit_task(make_task('3/3'))
    backend.mark_done()

    results = backend.results()
    first = next(results)
    first.acknowledge('reported', runtime_state_changed=True)

    second = next(results)
    second.acknowledge('skipped')

    third = next(results)
    third.acknowledge('no_row')

    # runtime_state_changed now uses movie_sleep_mgr.sleep() (same as inter-movie pacing),
    # then skipped also triggers movie sleep before next task.
    assert movie_sleep_calls == ['movie', 'movie']
