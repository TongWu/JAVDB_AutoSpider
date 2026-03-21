import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from migration.tools.align_inventory_with_moviehistory import (
    compute_missing_codes,
    _best_parsed_category,
    _to_purge_plan_rows,
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
