"""Unit tests for shared spider detail runner helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import scripts.spider.runtime.state as state
from scripts.ingestion.models import SpiderIngestionPlan
from scripts.spider.detail.runner import (
    DetailPersistOutcome,
    persist_parsed_detail_result,
    process_detail_entries,
    prepare_detail_entries,
)


def make_entry(
    code: str,
    *,
    href: str | None = None,
    page: int = 1,
    is_today_release: bool = False,
    is_yesterday_release: bool = False,
) -> dict:
    normalized = code.lower().replace('-', '')
    return {
        'video_code': code,
        'href': href or f'/v/{normalized}',
        'page': page,
        'is_today_release': is_today_release,
        'is_yesterday_release': is_yesterday_release,
    }


@pytest.fixture(autouse=True)
def _reset_state():
    state.parsed_links.clear()
    yield
    state.parsed_links.clear()


def test_prepare_detail_entries_preserves_recent_release_toggle(monkeypatch):
    import scripts.spider.detail.runner as dc

    entry = make_entry('ABC-123', is_today_release=True)

    monkeypatch.setattr(dc, 'has_complete_subtitles', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(dc, 'should_skip_from_rclone', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        dc,
        'should_skip_recent_today_release',
        lambda _href, _history, is_today: is_today,
    )
    monkeypatch.setattr(
        dc,
        'should_skip_recent_yesterday_release',
        lambda *_args, **_kwargs: False,
    )

    prepared, skipped = prepare_detail_entries(
        [entry],
        history_data={},
        is_adhoc_mode=False,
        include_recent_release_filters=False,
    )
    assert [candidate.entry['video_code'] for candidate in prepared] == ['ABC-123']
    assert skipped == 0

    state.parsed_links.clear()

    prepared, skipped = prepare_detail_entries(
        [entry],
        history_data={},
        is_adhoc_mode=False,
        include_recent_release_filters=True,
    )
    assert prepared == []
    assert skipped == 1


def test_prepare_detail_entries_counts_filter_skips_but_not_duplicates(monkeypatch):
    import scripts.spider.detail.runner as dc

    state.parsed_links.add('/v/already')
    entries = [
        make_entry('KEEP-1', href='/v/keep1'),
        make_entry('SKIP-HIST', href='/v/hist'),
        make_entry('SKIP-RC', href='/v/rclone'),
        make_entry('KEEP-1-DUP', href='/v/keep1'),
        make_entry('ALREADY', href='/v/already'),
    ]

    monkeypatch.setattr(dc, 'has_complete_subtitles', lambda href, _history: href == '/v/hist')
    monkeypatch.setattr(
        dc,
        'should_skip_from_rclone',
        lambda code, _inventory, _enable_dedup: code == 'SKIP-RC',
    )
    monkeypatch.setattr(
        dc,
        'should_skip_recent_today_release',
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        dc,
        'should_skip_recent_yesterday_release',
        lambda *_args, **_kwargs: False,
    )

    prepared, skipped = prepare_detail_entries(
        entries,
        history_data={},
        is_adhoc_mode=False,
        rclone_inventory={'SKIP-RC': [{}]},
        rclone_filter=True,
    )

    assert [candidate.entry['video_code'] for candidate in prepared] == ['KEEP-1']
    assert skipped == 2
    assert state.parsed_links == {'/v/already', '/v/keep1', '/v/hist', '/v/rclone'}


def test_persist_parsed_detail_result_writes_report_dedup_and_history(monkeypatch):
    import scripts.spider.detail.runner as dc

    entry = make_entry('ABC-123')
    dedup_record = SimpleNamespace(video_code='ABC-123', deletion_reason='upgrade')
    plan = SpiderIngestionPlan(
        should_skip=False,
        dedup_records=[dedup_record],
        report_row={'href': entry['href'], 'video_code': 'ABC-123'},
        has_new_torrents=True,
        should_include_in_report=True,
        new_magnet_links={'subtitle': 'magnet:?xt=urn:btih:abc'},
        new_sizes={'subtitle': '4.5GB'},
        new_file_counts={'subtitle': 1},
        new_resolutions={'subtitle': 1080},
    )

    written_rows = []
    saved_history = []
    dedup_appends = []

    monkeypatch.setattr(dc, 'build_spider_ingestion_plan', lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        dc,
        'write_csv',
        lambda rows, csv_path, fieldnames, dry_run, append_mode=False: written_rows.append(
            (rows, csv_path, tuple(fieldnames), dry_run, append_mode)
        ),
    )
    monkeypatch.setattr(
        dc,
        'save_parsed_movie_to_history',
        lambda *args, **kwargs: saved_history.append((args, kwargs)),
    )
    monkeypatch.setattr(
        dc,
        'append_dedup_record',
        lambda csv_path, record: dedup_appends.append((csv_path, record)),
    )

    outcome = persist_parsed_detail_result(
        entry=entry,
        phase=1,
        entry_index='1/1',
        history_data={},
        history_file='history.csv',
        csv_path='report.csv',
        fieldnames=['href', 'video_code'],
        dry_run=False,
        use_history_for_saving=True,
        is_adhoc_mode=False,
        rclone_inventory={'ABC-123': [{'existing': True}]},
        enable_dedup=True,
        dedup_csv_path='dedup.csv',
        enable_redownload=True,
        actor_info='Actor',
        actor_gender='F',
        actor_link='/actors/a1',
        supporting_actors='Support',
        magnet_links={'subtitle': 'magnet:?xt=urn:btih:abc'},
    )

    assert outcome.status == 'reported'
    assert outcome.row == {'href': entry['href'], 'video_code': 'ABC-123'}
    assert outcome.visited_href == entry['href']
    assert outcome.actor_update == (
        entry['href'],
        'Actor',
        'F',
        '/actors/a1',
        'Support',
    )
    assert written_rows == [
        ([{'href': entry['href'], 'video_code': 'ABC-123'}], 'report.csv', ('href', 'video_code'), False, True)
    ]
    assert len(saved_history) == 1
    assert len(dedup_appends) == 1
    assert dedup_appends[0][0] == 'dedup.csv'
    assert dedup_appends[0][1] is dedup_record


def test_persist_parsed_detail_result_keeps_visited_metadata_on_skip(monkeypatch):
    import scripts.spider.detail.runner as dc

    entry = make_entry('ZZZ-999')
    plan = SpiderIngestionPlan(
        should_skip=True,
        skip_reason='history_no_missing_types',
    )

    monkeypatch.setattr(dc, 'build_spider_ingestion_plan', lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(dc, 'write_csv', lambda *_args, **_kwargs: pytest.fail('write_csv should not be called'))
    monkeypatch.setattr(
        dc,
        'save_parsed_movie_to_history',
        lambda *_args, **_kwargs: pytest.fail('save_parsed_movie_to_history should not be called'),
    )

    outcome = persist_parsed_detail_result(
        entry=entry,
        phase=2,
        entry_index='2/2',
        history_data={},
        history_file='history.csv',
        csv_path='report.csv',
        fieldnames=['href'],
        dry_run=False,
        use_history_for_saving=True,
        is_adhoc_mode=False,
        actor_info='Actor',
        magnet_links={'subtitle': 'magnet:?xt=urn:btih:def'},
    )

    assert outcome.status == 'skipped'
    assert outcome.skipped_history == 1
    assert outcome.no_new_torrents == 0
    assert outcome.row is None
    assert outcome.visited_href == entry['href']
    assert outcome.actor_update == (
        entry['href'],
        'Actor',
        '',
        '',
        '',
    )


def test_process_detail_entries_handles_backend_results(monkeypatch):
    from scripts.spider.fetch.backend import FetchRuntimeState
    from scripts.spider.fetch.fetch_engine import EngineResult

    class FakeBackend:
        def __init__(self):
            self.tasks = []
            self.started = False
            self.done = False
            self.shutdown_called = False

        @property
        def worker_count(self):
            return 2

        def start(self):
            self.started = True

        def submit_task(self, task):
            self.tasks.append(task)

        def mark_done(self):
            self.done = True

        def runtime_state(self):
            return FetchRuntimeState(use_proxy=True, use_cf_bypass=False)

        def results(self):
            yield EngineResult(
                task=self.tasks[0],
                success=True,
                data={
                    'magnets': ['magnet-1'],
                    'actor_info': 'Actor',
                    'actor_gender': '',
                    'actor_link': '',
                    'supporting': '',
                },
            )
            yield EngineResult(
                task=self.tasks[1],
                success=False,
                error='fetch_failed',
            )

        def shutdown(self, *, timeout=10):
            self.shutdown_called = True
            return []

    backend = FakeBackend()
    entries = [
        make_entry('ABC-123', href='/v/abc123'),
        make_entry('DEF-456', href='/v/def456'),
    ]

    monkeypatch.setattr(
        'scripts.spider.detail.runner.extract_magnets',
        lambda magnets, _idx: {'subtitle': magnets[0]},
    )
    monkeypatch.setattr(
        'scripts.spider.detail.runner.persist_parsed_detail_result',
        lambda **kwargs: DetailPersistOutcome(
            status='reported',
            row={'href': kwargs['entry']['href'], 'video_code': kwargs['entry']['video_code']},
            visited_href=kwargs['entry']['href'],
        ),
    )

    result = process_detail_entries(
        backend=backend,
        entries=entries,
        phase=1,
        history_data={},
        history_file='history.csv',
        csv_path='report.csv',
        fieldnames=['href', 'video_code'],
        dry_run=True,
        use_history_for_saving=False,
        is_adhoc_mode=False,
    )

    assert backend.started is True
    assert backend.done is True
    assert backend.shutdown_called is True
    assert result['rows'] == [{'href': '/v/abc123', 'video_code': 'ABC-123'}]
    assert result['failed'] == 1
    assert result['failed_movies'] == [
        {'video_code': 'DEF-456', 'url': 'https://javdb.com/v/def456', 'phase': 1}
    ]
    assert result['use_proxy'] is True
    assert result['use_cf_bypass'] is False


def test_process_detail_entries_acknowledges_runtime_state_changes(monkeypatch):
    from scripts.spider.fetch.backend import FetchRuntimeState
    from scripts.spider.fetch.fetch_engine import EngineResult

    class FakeBackend:
        def __init__(self):
            self.tasks = []
            self._states = [
                FetchRuntimeState(use_proxy=False, use_cf_bypass=False),
                FetchRuntimeState(use_proxy=True, use_cf_bypass=True),
                FetchRuntimeState(use_proxy=True, use_cf_bypass=True),
            ]
            self._state_index = 0
            self.ack_calls = []

        @property
        def worker_count(self):
            return 1

        def start(self):
            return None

        def submit_task(self, task):
            self.tasks.append(task)

        def mark_done(self):
            return None

        def runtime_state(self):
            return self._states[self._state_index]

        def results(self):
            self._state_index = 1
            yield EngineResult(
                task=self.tasks[0],
                success=True,
                data={
                    'magnets': ['magnet-1'],
                    'actor_info': 'Actor',
                    'actor_gender': '',
                    'actor_link': '',
                    'supporting': '',
                },
                _ack_callback=lambda status, changed: self.ack_calls.append((status, changed)),
            )
            self._state_index = 2
            yield EngineResult(
                task=self.tasks[1],
                success=True,
                data={
                    'magnets': ['magnet-2'],
                    'actor_info': 'Actor',
                    'actor_gender': '',
                    'actor_link': '',
                    'supporting': '',
                },
                _ack_callback=lambda status, changed: self.ack_calls.append((status, changed)),
            )

        def shutdown(self, *, timeout=10):
            return []

    outcomes = iter(
        [
            DetailPersistOutcome(status='reported'),
            DetailPersistOutcome(status='skipped', skipped_history=1),
        ]
    )

    monkeypatch.setattr(
        'scripts.spider.detail.runner.extract_magnets',
        lambda magnets, _idx: {'subtitle': magnets[0]},
    )
    monkeypatch.setattr(
        'scripts.spider.detail.runner.persist_parsed_detail_result',
        lambda **_kwargs: next(outcomes),
    )

    backend = FakeBackend()
    process_detail_entries(
        backend=backend,
        entries=[
            make_entry('ABC-123', href='/v/abc123'),
            make_entry('DEF-456', href='/v/def456'),
        ],
        phase=1,
        history_data={},
        history_file='history.csv',
        csv_path='report.csv',
        fieldnames=['href', 'video_code'],
        dry_run=True,
        use_history_for_saving=False,
        is_adhoc_mode=False,
    )

    assert backend.ack_calls == [('reported', True), ('skipped', False)]
