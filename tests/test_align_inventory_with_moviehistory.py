import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from migration.tools.align_inventory_with_moviehistory import (
    compute_missing_codes,
    _best_parsed_category,
    _to_purge_plan_rows,
    parse_args,
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
