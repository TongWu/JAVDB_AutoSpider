"""Unit tests for shared spider detail runner helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import scripts.spider.runtime.state as state
from scripts.ingestion.models import SpiderIngestionPlan
from scripts.spider.detail.runner import (
    persist_parsed_detail_result,
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
