import csv
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.migrations.tools.align_inventory_with_moviehistory import (
    _RESULT_FIELDNAMES,
    _write_csv,
    _write_consolidated_result_csv,
    compute_missing_codes,
    _best_parsed_category,
    _to_purge_plan_rows,
    parse_args,
    run_alignment,
)


def test_compute_missing_codes_inventory_minus_history():
    inventory = {
        'JAC-228': [{'VideoCode': 'JAC-228'}],
        'VDD-201': [{'VideoCode': 'VDD-201'}],
    }
    history = {
        '/v/abc': {'VideoCode': 'VDD-201'},
    }
    missing = compute_missing_codes(inventory, history)
    assert missing == ['JAC-228']


def test_best_parsed_category_prefers_hacked_subtitle():
    magnet_links = {
        'hacked_subtitle': 'magnet:?xt=urn:btih:1',
        'hacked_no_subtitle': 'magnet:?xt=urn:btih:2',
        'subtitle': 'magnet:?xt=urn:btih:3',
        'no_subtitle': 'magnet:?xt=urn:btih:4',
    }
    assert _best_parsed_category(magnet_links) == 'hacked_subtitle'


def test_purge_plan_rows_only_lower_rank_entries():
    entries = [
        {
            'FolderPath': 'drive:/root/2026/A/JAC-228 [有码-无字]',
            'SensorCategory': '有码',
            'SubtitleCategory': '无字',
        },
        {
            'FolderPath': 'drive:/root/2026/A/JAC-228 [有码-中字]',
            'SensorCategory': '有码',
            'SubtitleCategory': '中字',
        },
        {
            'FolderPath': 'drive:/root/2026/A/JAC-228 [无码-中字]',
            'SensorCategory': '无码',
            'SubtitleCategory': '中字',
        },
    ]

    rows = _to_purge_plan_rows(
        video_code='JAC-228',
        inventory_entries=entries,
        parsed_best_rank=20,  # subtitle
        new_torrent_category='subtitle',
    )

    assert len(rows) == 1
    assert rows[0]['source_path'].endswith('[有码-无字]')
    assert 'destination_path' not in rows[0]


def test_purge_plan_rows_only_touch_same_family_entries():
    entries = [
        {
            'FolderPath': 'drive:/root/2026/A/JAC-228 [有码-中字]',
            'SensorCategory': '有码',
            'SubtitleCategory': '中字',
        },
        {
            'FolderPath': 'drive:/root/2026/A/JAC-228 [无码破解-无字]',
            'SensorCategory': '无码破解',
            'SubtitleCategory': '无字',
        },
    ]

    rows = _to_purge_plan_rows(
        video_code='JAC-228',
        inventory_entries=entries,
        parsed_best_rank=40,
        new_torrent_category='hacked_subtitle',
    )

    assert len(rows) == 1
    assert rows[0]['source_path'].endswith('[无码破解-无字]')


def test_parse_args_alignment_defaults_to_proxy(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['align_inventory_with_moviehistory.py'])
    args = parse_args()
    assert args.no_proxy is False
    assert args.use_proxy is True


def test_parse_args_alignment_no_proxy(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['align_inventory_with_moviehistory.py', '--no-proxy'])
    args = parse_args()
    assert args.no_proxy is True
    assert args.use_proxy is False


def test_parse_args_alignment_limit_per_worker(monkeypatch):
    monkeypatch.setattr(
        sys,
        'argv',
        ['align_inventory_with_moviehistory.py', '--limit-per-worker', '5'],
    )
    args = parse_args()
    assert args.limit_per_worker == 5


def test_parse_args_alignment_rejects_conflicting_proxy_flags(monkeypatch):
    monkeypatch.setattr(sys, 'argv', [
        'align_inventory_with_moviehistory.py',
        '--no-proxy',
        '--use-proxy',
    ])
    with pytest.raises(SystemExit):
        parse_args()


def test_write_csv_removes_header_only_report(temp_dir):
    csv_path = os.path.join(temp_dir, 'InventoryHistoryAlign_QBUpgrade_test.csv')
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        f.write('href,video_code\n')

    written_path = _write_csv(csv_path, ['href', 'video_code'], [])

    assert written_path == ''
    assert not os.path.exists(csv_path)


def test_write_consolidated_result_csv_merges_legacy_files_and_dedupes(temp_dir):
    legacy_dir = Path(temp_dir) / '2026' / '03'
    legacy_dir.mkdir(parents=True, exist_ok=True)
    older = legacy_dir / 'InventoryHistoryAlign_Result_20260324_010101.csv'
    newer = legacy_dir / 'InventoryHistoryAlign_Result_20260325_010101.csv'

    with older.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=_RESULT_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            'video_code': 'ABC-123',
            'status': 'search_miss',
            'href': '',
            'detail_href': '',
            'actor_name': '',
            'chosen_upgrade_category': '',
            'message': 'old-result',
        })

    with newer.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=_RESULT_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            'video_code': 'ABC-123',
            'status': 'ok',
            'href': '/v/abc',
            'detail_href': '/v/abc',
            'actor_name': 'Alice',
            'chosen_upgrade_category': 'subtitle',
            'message': '',
        })
        writer.writerow({
            'video_code': 'XYZ-999',
            'status': 'search_miss',
            'href': '',
            'detail_href': '',
            'actor_name': '',
            'chosen_upgrade_category': '',
            'message': 'legacy-only',
        })

    written_path = _write_consolidated_result_csv(temp_dir, [
        {
            'video_code': 'XYZ-999',
            'status': 'ok',
            'href': '/v/xyz',
            'detail_href': '/v/xyz',
            'actor_name': 'Bob',
            'chosen_upgrade_category': 'no_subtitle',
            'message': '',
        },
        {
            'video_code': 'DEF-456',
            'status': 'detail_parse_failed',
            'href': '/v/def',
            'detail_href': '/v/def',
            'actor_name': '',
            'chosen_upgrade_category': '',
            'message': 'parse failed',
        },
    ])

    assert written_path == os.path.join(temp_dir, 'InventoryHistoryAlign_Result.csv')
    assert os.path.exists(written_path)
    assert not older.exists()
    assert not newer.exists()

    with open(written_path, 'r', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))

    assert [row['video_code'] for row in rows] == ['ABC-123', 'DEF-456', 'XYZ-999']
    assert rows[0]['status'] == 'ok'
    assert rows[0]['actor_name'] == 'Alice'
    assert rows[2]['status'] == 'ok'
    assert rows[2]['href'] == '/v/xyz'


def test_run_alignment_skips_empty_auxiliary_reports(monkeypatch, temp_dir):
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class FakeDetail:
        parse_success = True
        magnets = []

        def get_first_actor_name(self):
            return ''

        def get_first_actor_gender(self):
            return ''

        def get_first_actor_href(self):
            return ''

        def get_supporting_actors_json(self):
            return '[]'

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass
        def load_history(self, **_kw):
            return {}

    class _FakeOperationsRepo:
        def __init__(self, **_kw):
            pass
        def load_rclone_inventory(self):
            return {'ABC-123': [{'VideoCode': 'ABC-123'}]}
        def load_align_no_exact_match_codes(self):
            return set()
        def upsert_align_no_exact_match(self, *args, **kwargs):
            pass
        def delete_align_no_exact_match(self, *args, **kwargs):
            pass

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'OperationsRepo', _FakeOperationsRepo)
    monkeypatch.setattr(mod, 'init_db', lambda *args, **kwargs: None)
    monkeypatch.setattr(mod.spider_state, 'setup_proxy_pool', lambda **kwargs: None)
    monkeypatch.setattr(mod.spider_state, 'initialize_request_handler', lambda: None)
    monkeypatch.setattr(mod, 'cfg', lambda key, default=None: temp_dir if key == 'REPORTS_DIR' else default)
    monkeypatch.setattr(mod, 'get_page_url', lambda page_num, custom_url=None: custom_url or 'https://javdb.com/search')
    monkeypatch.setattr(mod, '_fetch_html', lambda session, url, use_proxy: '<html></html>')
    monkeypatch.setattr(
        mod,
        'parse_index_page',
        lambda html, page_num=1: SimpleNamespace(
            has_movie_list=True,
            movies=[SimpleNamespace(href='/v/abc')],
        ),
    )
    monkeypatch.setattr(mod, 'find_exact_video_code_match', lambda movies, code: movies[0])
    monkeypatch.setattr(mod, 'find_exact_entry_first_search_page', lambda movies, code: movies[0] if movies else None)
    monkeypatch.setattr(mod, 'parse_detail_page', lambda html: FakeDetail())
    monkeypatch.setattr(mod, 'extract_magnets', lambda payload, index='': {})
    monkeypatch.setattr(
        mod,
        'build_alignment_upgrade_plan',
        lambda detail_href, video_code, magnet_links, inventory_entries: SimpleNamespace(
            qb_rows=[],
            purge_plan_rows=[],
            chosen_upgrade_category='',
        ),
    )

    args = SimpleNamespace(
        dry_run=True,
        session_id=None,
        limit=0,
        codes='',
        output_dir=temp_dir,
        enqueue_qb=False,
        qb_category='',
        execute_delete=False,
        no_proxy=True,
        use_proxy=False,
        no_login=False,
        shuffle=False,
    )

    rc = run_alignment(args)

    assert rc == 0

    summary_files = list(Path(temp_dir).rglob('InventoryHistoryAlign_Summary_*.json'))
    assert len(summary_files) == 1

    summary = json.loads(summary_files[0].read_text(encoding='utf-8'))
    result_csv = summary['files']['result_csv']

    assert result_csv == os.path.join(temp_dir, 'InventoryHistoryAlign_Result.csv')
    assert os.path.exists(result_csv)
    assert not list(Path(temp_dir).rglob('InventoryHistoryAlign_Result_*.csv'))
    assert summary['files']['qb_upgrade_csv'] == ''
    assert summary['files']['purge_plan_csv'] == ''
    assert not list(Path(temp_dir).rglob('InventoryHistoryAlign_QBUpgrade_*.csv'))
    assert not list(Path(temp_dir).rglob('InventoryHistoryAlign_PurgePlan_*.csv'))


# ── compute_missing_codes: skip_codes ────────────────────────────────────

def test_compute_missing_codes_skips_no_exact_match_codes():
    inventory = {
        'ABC-123': [{'VideoCode': 'ABC-123'}],
        'DEF-456': [{'VideoCode': 'DEF-456'}],
        'GHI-789': [{'VideoCode': 'GHI-789'}],
    }
    history = {}
    missing = compute_missing_codes(
        inventory, history, skip_codes={'DEF-456'},
    )
    assert missing == ['ABC-123', 'GHI-789']


def test_compute_missing_codes_skip_codes_normalises_case():
    inventory = {'ABC-123': [{}], 'DEF-456': [{}]}
    missing = compute_missing_codes(
        inventory, {}, skip_codes={'abc-123'},
    )
    assert missing == ['DEF-456']


def test_compute_missing_codes_skip_and_only_codes_combined():
    inventory = {
        'A-001': [{}], 'B-002': [{}], 'C-003': [{}],
    }
    missing = compute_missing_codes(
        inventory, {},
        only_codes=['A-001', 'B-002'],
        skip_codes={'B-002'},
    )
    assert missing == ['A-001']


# ── DB helpers: InventoryAlignNoExactMatch ───────────────────────────────

def test_db_align_no_exact_match_roundtrip(temp_dir):
    import sqlite3
    from javdb.storage.db._db_migrations import _OPERATIONS_DDL
    from javdb.storage.db._db_operations import (
        db_upsert_align_no_exact_match,
        db_load_align_no_exact_match_codes,
        db_delete_align_no_exact_match,
    )

    db_path = os.path.join(temp_dir, 'ops_test.db')
    conn = sqlite3.connect(db_path)
    conn.executescript(_OPERATIONS_DDL)
    conn.commit()
    conn.close()

    assert db_load_align_no_exact_match_codes(db_path=db_path) == set()

    db_upsert_align_no_exact_match('abc-123', db_path=db_path, session_id=None)
    db_upsert_align_no_exact_match(
        'DEF-456', reason='custom', db_path=db_path, session_id=None)

    codes = db_load_align_no_exact_match_codes(db_path=db_path)
    assert codes == {'ABC-123', 'DEF-456'}

    db_upsert_align_no_exact_match(
        'abc-123', reason='updated', db_path=db_path, session_id=None)
    codes = db_load_align_no_exact_match_codes(db_path=db_path)
    assert codes == {'ABC-123', 'DEF-456'}

    db_delete_align_no_exact_match('ABC-123', db_path=db_path)
    assert db_load_align_no_exact_match_codes(db_path=db_path) == {'DEF-456'}

    db_delete_align_no_exact_match('nonexistent', db_path=db_path)
    assert db_load_align_no_exact_match_codes(db_path=db_path) == {'DEF-456'}


# ── parse_args: --no-login / --shuffle ───────────────────────────────────

def test_parse_args_no_login_defaults_false(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['align_inventory_with_moviehistory.py'])
    args = parse_args()
    assert args.no_login is False


def test_parse_args_no_login_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['align_inventory_with_moviehistory.py', '--no-login'])
    args = parse_args()
    assert args.no_login is True


def test_parse_args_shuffle_defaults_false(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['align_inventory_with_moviehistory.py'])
    args = parse_args()
    assert args.shuffle is False


def test_parse_args_shuffle_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['align_inventory_with_moviehistory.py', '--shuffle'])
    args = parse_args()
    assert args.shuffle is True


# ── shuffle: randomises missing codes order ──────────────────────────────

def test_shuffle_changes_order_before_limit():
    import random

    inventory = {f'CODE-{i:03d}': [{}] for i in range(20)}
    sorted_codes = compute_missing_codes(inventory, {})
    assert sorted_codes == sorted(sorted_codes)

    shuffled = list(sorted_codes)
    rng = random.Random(42)
    rng.shuffle(shuffled)
    assert shuffled != sorted_codes


# ── staging+commit rewrite (ADR-005 PR-4 follow-up) ───────────────────────


def test_stage_aligned_movie_stages_all_nonempty_categories():
    """The staging helper records the movie plus every non-empty torrent
    bucket (pre-ADR-005 behaviour), skipping empty categories."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class _Repo:
        def __init__(self):
            self.movies = []
            self.torrents = []

        def stage_movie(self, session_id, payload):
            self.movies.append((session_id, payload))
            return 'm-seq'

        def stage_torrent(self, session_id, payload):
            self.torrents.append((session_id, payload))
            return 't-seq'

    repo = _Repo()
    kwargs = mod._build_db_upsert_kwargs(
        'https://javdb.com/v/abc', 'ABC-123',
        {
            'hacked_subtitle': 'magnet:?xt=urn:btih:A',
            'no_subtitle': 'magnet:?xt=urn:btih:B',
            'subtitle': '',  # explicitly empty → must be skipped
            # 'hacked_no_subtitle' absent → defaults to '' → must be skipped
            'size_hacked_subtitle': '1.2GB',
            'size_no_subtitle': '900MB',
            'file_count_hacked_subtitle': 3,
            'file_count_no_subtitle': 1,
            'resolution_hacked_subtitle': 1080,
            'resolution_no_subtitle': 720,
        },
        'Actor A', 'female', '/actors/x', '[]',
    )
    mod._stage_aligned_movie(repo, 'SID-1', kwargs)

    assert repo.movies == [
        ('SID-1', {
            'Href': 'https://javdb.com/v/abc',
            'VideoCode': 'ABC-123',
            'ActorName': 'Actor A',
            'ActorGender': 'female',
            'ActorLink': '/actors/x',
            # '[]' is coerced to None so an empty supporting-actor parse does
            # not clobber existing data at commit (see _blank_actor_field_to_none).
            'SupportingActors': None,
        }),
    ]
    # Only the two non-empty buckets are staged; empty ones are skipped.
    staged_cats = sorted(p['Category'] for _sid, p in repo.torrents)
    assert staged_cats == ['hacked_subtitle', 'no_subtitle']
    by_cat = {p['Category']: p for _sid, p in repo.torrents}
    assert by_cat['hacked_subtitle']['MagnetUri'] == 'magnet:?xt=urn:btih:A'
    assert by_cat['hacked_subtitle']['FileCount'] == 3
    assert by_cat['hacked_subtitle']['ResolutionType'] == 1080
    assert by_cat['no_subtitle']['Size'] == '900MB'


def test_finalize_alignment_session_commits_on_success(monkeypatch):
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    committed = []
    rolled_back = []

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def commit_session(self, session_id, **_kw):
            committed.append(session_id)
            return {'movies_upserted': 1, 'torrents_upserted': 2}

    class _FakeSessionRepo:
        def __init__(self, **_kw):
            pass

        def rollback_session(self, session_id, **kwargs):
            rolled_back.append((session_id, kwargs))
            return {}

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)

    rc = mod._finalize_alignment_session('SID-1', 0)

    assert rc == 0
    assert committed == ['SID-1']
    assert rolled_back == []


def test_finalize_alignment_session_leaves_committed_session_alone(
    monkeypatch, caplog,
):
    """A post-commit failure must not attempt (or report) a rollback.

    The commit boundary sits mid-``_run_alignment_core``; the CSV writes and
    ``_enqueue_qb_from_csv`` run after it. When one of those raises, the outer
    guard finalizes with rc=1 on an already-``committed`` session —
    ``rollback_session`` refuses it, and the old code surfaced that refusal as
    "pending writes left undrained", sending the operator to re-commit a
    session that had drained cleanly (GH Actions run 30196995600).
    """
    import logging

    from javdb.migrations.tools import align_inventory_with_moviehistory as mod
    import javdb.storage.db._db_reports as reports_mod

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def commit_session(self, session_id, **_kw):
            raise AssertionError('rc != 0 must not commit')

    class _FakeSessionRepo:
        def __init__(self, **_kw):
            pass

        def rollback_session(self, session_id, **_kwargs):
            raise AssertionError('a committed session must not be rolled back')

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)
    monkeypatch.setattr(
        reports_mod, 'db_get_session_status',
        lambda *a, **k: ('pending', 'committed'),
    )

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        rc = mod._finalize_alignment_session('SID-1', 1)

    assert rc == 0
    assert 'undrained' not in caplog.text
    assert 'commit_session --session-id' not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert 'already committed' in caplog.text


def test_finalize_alignment_session_rolls_back_on_failure(monkeypatch):
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod
    import javdb.storage.db._db_reports as reports_mod

    committed = []
    rolled_back = []

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def commit_session(self, session_id, **_kw):
            committed.append(session_id)
            return {}

    class _FakeSessionRepo:
        def __init__(self, **_kw):
            pass

        def rollback_session(self, session_id, **kwargs):
            rolled_back.append((session_id, kwargs))
            return {}

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)
    monkeypatch.setattr(
        reports_mod, 'db_get_session_status',
        lambda *a, **k: ('pending', 'in_progress'),
    )

    # rc != 0 (e.g. parallel interrupt): roll back, never commit, preserve rc.
    rc = mod._finalize_alignment_session('SID-1', 130)

    assert rc == 0  # finalize returns 0; caller keeps its own non-zero rc
    assert committed == []
    assert len(rolled_back) == 1
    assert rolled_back[0][0] == 'SID-1'


def test_finalize_alignment_session_reports_unrecoverable_cleanup(
    monkeypatch, caplog,
):
    """A failed cleanup must name the stuck session, not just "rollback failed".

    BFR-035: ``rollback_session`` recovers a ``finalizing`` session by
    re-running the same drain, so a deterministic drain failure fails again
    and leaves the pending writes undrained. This log line is the only
    signal the operator gets before the 48h stale-session sweep.
    """
    import logging

    from javdb.migrations.tools import align_inventory_with_moviehistory as mod
    import javdb.storage.db._db_reports as reports_mod

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def commit_session(self, session_id, **_kw):
            raise AssertionError('rc != 0 must not commit')

    class _FakeSessionRepo:
        def __init__(self, **_kw):
            pass

        def rollback_session(self, session_id, **_kwargs):
            raise RuntimeError('UNIQUE constraint failed: MovieHistory.Href')

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)
    monkeypatch.setattr(
        reports_mod, 'db_get_session_status',
        lambda *a, **k: ('pending', 'finalizing'),
    )

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        rc = mod._finalize_alignment_session('SID-1', 1)

    assert rc == 0  # finalize returns 0; caller keeps its own non-zero rc
    assert 'SID-1' in caplog.text
    assert 'undrained' in caplog.text
    assert 'apps.cli.db.commit_session --session-id SID-1' in caplog.text


def test_blank_actor_field_to_none():
    """Blank / placeholder actor values become None; real values pass through."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    assert mod._blank_actor_field_to_none(None) is None
    assert mod._blank_actor_field_to_none('') is None
    assert mod._blank_actor_field_to_none('   ') is None
    assert mod._blank_actor_field_to_none('[]') is None
    assert mod._blank_actor_field_to_none('Jane Doe') == 'Jane Doe'
    assert mod._blank_actor_field_to_none('[{"name": "X"}]') == '[{"name": "X"}]'


def test_stage_aligned_movie_nulls_empty_actor_data_to_preserve_existing():
    """An empty-actor parse stages None for every actor field so the commit
    path leaves existing MovieHistory actor data untouched."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class _Repo:
        def __init__(self):
            self.movies = []

        def stage_movie(self, session_id, payload):
            self.movies.append(payload)
            return 'm'

        def stage_torrent(self, *a, **k):
            return 't'

    repo = _Repo()
    kwargs = mod._build_db_upsert_kwargs(
        'https://javdb.com/v/abc', 'ABC-123',
        {'no_subtitle': 'magnet:?xt=urn:btih:Z'},
        '', '', '', '[]',  # empty actor fields + empty supporting-actors JSON
    )
    mod._stage_aligned_movie(repo, 'SID-1', kwargs)

    payload = repo.movies[0]
    assert payload['ActorName'] is None
    assert payload['ActorGender'] is None
    assert payload['ActorLink'] is None
    assert payload['SupportingActors'] is None


def test_finalize_alignment_session_reraises_on_commit_failure(monkeypatch):
    """A commit that raises propagates (it is NOT swallowed) so the caller's
    outer guard can run the status-aware rollback/resume."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def commit_session(self, session_id, **_kw):
            raise RuntimeError('D1 drain failed')

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)

    with pytest.raises(RuntimeError, match='D1 drain failed'):
        mod._finalize_alignment_session('SID-1', 0)


def test_verify_adoptable_session_rejects_missing_and_non_pending(monkeypatch):
    """An adopted --session-id must exist, be pending, and be in_progress."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod
    import javdb.storage.db._db_reports as reports_mod

    # Missing session → SystemExit.
    monkeypatch.setattr(reports_mod, 'db_get_session_status', lambda *a, **k: None)
    with pytest.raises(SystemExit, match='does not exist'):
        mod._verify_adoptable_session('20260615T120000.000000Z-0001-0001')

    # Already committed → SystemExit.
    monkeypatch.setattr(reports_mod, 'db_get_session_status', lambda *a, **k: ('pending', 'committed'))
    with pytest.raises(SystemExit, match='not adoptable'):
        mod._verify_adoptable_session('20260615T120000.000000Z-0001-0001')

    # Valid pending / in_progress → no raise.
    monkeypatch.setattr(reports_mod, 'db_get_session_status', lambda *a, **k: ('pending', 'in_progress'))
    mod._verify_adoptable_session('20260615T120000.000000Z-0001-0001')


def test_run_alignment_empty_missing_codes_opens_no_session(monkeypatch, temp_dir):
    """Nothing to align → return early WITHOUT opening a session (no orphan)."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def load_history(self, **_kw):
            return {}

    class _FakeOperationsRepo:
        def __init__(self, **_kw):
            pass

        def load_rclone_inventory(self):
            return {}  # nothing in inventory → no missing codes

        def load_align_no_exact_match_codes(self):
            return set()

    opened = []

    class _FakeSessionRepo:
        def __init__(self, **_kw):
            pass

        def create_report_session(self, **kwargs):  # pragma: no cover
            opened.append(kwargs)
            return 'SID-SHOULD-NOT-EXIST'

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'OperationsRepo', _FakeOperationsRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)
    monkeypatch.setattr(mod, 'init_db', lambda *a, **k: None)

    args = SimpleNamespace(
        dry_run=False, session_id=None, limit=0, codes='', output_dir=temp_dir,
        enqueue_qb=False, qb_category='', execute_delete=False,
        no_proxy=True, use_proxy=False, no_login=False, shuffle=False,
    )

    rc = run_alignment(args)

    assert rc == 0
    assert opened == []  # no session created when there is no work


def test_run_alignment_rolls_back_session_on_core_error(monkeypatch, temp_dir):
    """An exception in the staging/commit body rolls the opened session back
    (instead of orphaning an in_progress row) and re-raises."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class _FakeHistoryRepo:
        def __init__(self, **_kw):
            pass

        def load_history(self, **_kw):
            return {}

    class _FakeOperationsRepo:
        def __init__(self, **_kw):
            pass

        def load_rclone_inventory(self):
            return {'ABC-123': [{'VideoCode': 'ABC-123'}]}

        def load_align_no_exact_match_codes(self):
            return set()

    SID = '20260615T120000.000000Z-0001-0001'
    rolled_back = []

    class _FakeSessionRepo:
        def __init__(self, **_kw):
            pass

        def create_report_session(self, **kwargs):
            return SID

        def rollback_session(self, session_id, **kwargs):
            rolled_back.append((session_id, kwargs))
            return {}

    def _boom(*args, **kwargs):
        raise RuntimeError('staging blew up mid-run')

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'OperationsRepo', _FakeOperationsRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)
    monkeypatch.setattr(mod, 'set_active_run_identity', lambda *a, **k: None)
    monkeypatch.setattr(mod, 'init_db', lambda *a, **k: None)
    monkeypatch.setattr(mod.spider_state, 'setup_proxy_pool', lambda **k: None)
    monkeypatch.setattr(mod.spider_state, 'initialize_request_handler', lambda: None)
    monkeypatch.setattr(mod, 'cfg', lambda key, default=None: temp_dir if key == 'REPORTS_DIR' else (default or 'https://javdb.com'))
    monkeypatch.setattr(mod, '_run_alignment_core', _boom)

    args = SimpleNamespace(
        dry_run=False, session_id=None, limit=0, codes='', output_dir=temp_dir,
        enqueue_qb=False, qb_category='', execute_delete=False,
        no_proxy=True, use_proxy=False, no_login=False, shuffle=False,
    )

    with pytest.raises(RuntimeError, match='staging blew up'):
        run_alignment(args)

    # The opened session was rolled back, not left in_progress.
    assert len(rolled_back) == 1
    assert rolled_back[0][0] == SID


def test_run_alignment_non_dry_run_opens_stages_and_commits(monkeypatch, temp_dir):
    """End-to-end (sequential path): a non-dry-run alignment opens a pending
    session, stages the matched movie + torrents, and commits the session."""
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    class FakeDetail:
        parse_success = True
        magnets = []

        def get_first_actor_name(self):
            return 'Actor A'

        def get_first_actor_gender(self):
            return 'female'

        def get_first_actor_href(self):
            return '/actors/x'

        def get_supporting_actors_json(self):
            return '[]'

    class _FakeHistoryRepo:
        staged_movies = []
        staged_torrents = []
        committed = []

        def __init__(self, **_kw):
            pass

        def load_history(self, **_kw):
            return {}

        def stage_movie(self, session_id, payload):
            _FakeHistoryRepo.staged_movies.append((session_id, payload))
            return 'm-seq'

        def stage_torrent(self, session_id, payload):
            _FakeHistoryRepo.staged_torrents.append((session_id, payload))
            return 't-seq'

        def commit_session(self, session_id, **_kw):
            _FakeHistoryRepo.committed.append(session_id)
            return {'movies_upserted': 1, 'torrents_upserted': 2}

    class _FakeOperationsRepo:
        def __init__(self, **_kw):
            pass

        def load_rclone_inventory(self):
            return {'ABC-123': [{'VideoCode': 'ABC-123'}]}

        def load_align_no_exact_match_codes(self):
            return set()

        def upsert_align_no_exact_match(self, *args, **kwargs):
            pass

        def delete_align_no_exact_match(self, *args, **kwargs):
            pass

    SID = '20260615T120000.000000Z-0001-0001'

    class _FakeSessionRepo:
        created = []

        def __init__(self, **_kw):
            pass

        def create_report_session(self, **kwargs):
            _FakeSessionRepo.created.append(kwargs)
            return SID

        def rollback_session(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError('rollback must not run on a clean commit')

    monkeypatch.setattr(mod, 'HistoryRepo', _FakeHistoryRepo)
    monkeypatch.setattr(mod, 'OperationsRepo', _FakeOperationsRepo)
    monkeypatch.setattr(mod, 'SessionLifecycleRepo', _FakeSessionRepo)
    monkeypatch.setattr(mod, 'set_active_run_identity', lambda *a, **k: None)
    monkeypatch.setattr(mod, 'init_db', lambda *args, **kwargs: None)
    monkeypatch.setattr(mod.spider_state, 'setup_proxy_pool', lambda **kwargs: None)
    monkeypatch.setattr(mod.spider_state, 'initialize_request_handler', lambda: None)
    monkeypatch.setattr(mod, 'cfg', lambda key, default=None: temp_dir if key == 'REPORTS_DIR' else default)
    monkeypatch.setattr(mod, 'get_page_url', lambda page_num, custom_url=None: custom_url or 'https://javdb.com/search')
    monkeypatch.setattr(mod, '_fetch_html', lambda session, url, use_proxy: '<html></html>')
    monkeypatch.setattr(
        mod,
        'parse_index_page',
        lambda html, page_num=1: SimpleNamespace(
            has_movie_list=True,
            movies=[SimpleNamespace(href='/v/abc', video_code='ABC-123')],
        ),
    )
    monkeypatch.setattr(mod, 'find_exact_video_code_match', lambda movies, code: movies[0])
    monkeypatch.setattr(mod, 'find_exact_entry_first_search_page', lambda movies, code: movies[0] if movies else None)
    monkeypatch.setattr(mod, 'parse_detail_page', lambda html: FakeDetail())
    monkeypatch.setattr(
        mod, 'extract_magnets',
        lambda payload, index='': {
            'hacked_subtitle': 'magnet:?xt=urn:btih:A',
            'no_subtitle': 'magnet:?xt=urn:btih:B',
        },
    )
    monkeypatch.setattr(
        mod,
        'build_alignment_upgrade_plan',
        lambda detail_href, video_code, magnet_links, inventory_entries: SimpleNamespace(
            qb_rows=[],
            purge_plan_rows=[],
            chosen_upgrade_category='',
        ),
    )

    args = SimpleNamespace(
        dry_run=False,
        session_id=None,
        limit=0,
        codes='',
        output_dir=temp_dir,
        enqueue_qb=False,
        qb_category='',
        execute_delete=False,
        no_proxy=True,
        use_proxy=False,
        no_login=False,
        shuffle=False,
    )

    rc = run_alignment(args)

    assert rc == 0
    # A pending alignment session was opened.
    assert len(_FakeSessionRepo.created) == 1
    assert _FakeSessionRepo.created[0]['report_type'] == 'alignment'
    assert _FakeSessionRepo.created[0]['write_mode'] == 'pending'
    # The matched movie was staged into that session and committed.
    assert _FakeHistoryRepo.staged_movies == [
        (SID, {
            'Href': '/v/abc',
            'VideoCode': 'ABC-123',
            'ActorName': 'Actor A',
            'ActorGender': 'female',
            'ActorLink': '/actors/x',
            'SupportingActors': None,  # '[]' coerced — see _blank_actor_field_to_none
        }),
    ]
    assert sorted(p['Category'] for _sid, p in _FakeHistoryRepo.staged_torrents) == [
        'hacked_subtitle', 'no_subtitle',
    ]
    assert all(sid == SID for sid, _p in _FakeHistoryRepo.staged_torrents)
    assert _FakeHistoryRepo.committed == [SID]
    # run_alignment stamped the resolved session back onto args.
    assert args.session_id == SID


def test_enqueue_qb_from_csv_import_target_is_live(monkeypatch):
    """The qB helpers are imported lazily, so only a real call proves the path.

    ADR-007 Phase 3 retired ``scripts/qb_uploader.py``; this function-local
    import kept pointing at it and only blew up in production, after the
    alignment session had already committed.
    """
    from javdb.integrations.qb.uploader import service as qb_service
    from javdb.migrations.tools import align_inventory_with_moviehistory as mod

    calls = []
    monkeypatch.setattr(
        qb_service, 'initialize_proxy_helper', lambda override: calls.append(override)
    )
    # Bail out right after the import so the test touches no network.
    monkeypatch.setattr(qb_service, 'test_qbittorrent_connection', lambda _p: False)

    assert mod._enqueue_qb_from_csv('missing.csv', use_proxy=False) is False
    assert calls == [False]

    # Every attribute the function reaches for must exist on the target module.
    for name in (
        'initialize_proxy_helper',
        'test_qbittorrent_connection',
        'login_to_qbittorrent',
        'read_csv_file',
        'get_existing_torrents',
        'is_torrent_exists',
        'add_torrent_to_qbittorrent',
    ):
        assert callable(getattr(qb_service, name)), name
