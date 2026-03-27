import csv
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from migration.tools.align_inventory_with_moviehistory import (
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
    from migration.tools import align_inventory_with_moviehistory as mod

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

    monkeypatch.setattr(mod, 'db_load_history', lambda: {})
    monkeypatch.setattr(mod, 'db_load_rclone_inventory', lambda: {
        'ABC-123': [{'VideoCode': 'ABC-123'}],
    })
    monkeypatch.setattr(mod, 'init_db', lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, 'db_load_align_no_exact_match_codes', lambda: set())
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
    from packages.python.javdb_platform.db import (
        db_upsert_align_no_exact_match,
        db_load_align_no_exact_match_codes,
        db_delete_align_no_exact_match,
        _OPERATIONS_DDL,
    )

    db_path = os.path.join(temp_dir, 'ops_test.db')
    conn = sqlite3.connect(db_path)
    conn.executescript(_OPERATIONS_DDL)
    conn.commit()
    conn.close()

    assert db_load_align_no_exact_match_codes(db_path=db_path) == set()

    db_upsert_align_no_exact_match('abc-123', db_path=db_path)
    db_upsert_align_no_exact_match('DEF-456', reason='custom', db_path=db_path)

    codes = db_load_align_no_exact_match_codes(db_path=db_path)
    assert codes == {'ABC-123', 'DEF-456'}

    db_upsert_align_no_exact_match('abc-123', reason='updated', db_path=db_path)
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
