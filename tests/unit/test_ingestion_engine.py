import os
import sys
import logging

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from scripts.ingestion.engine import check_redownload_upgrade
from scripts.ingestion.models import ParsedMovie
from scripts.ingestion.planner import build_alignment_upgrade_plan, build_spider_ingestion_plan
from scripts.spider.services.dedup import RcloneEntry


def test_build_spider_ingestion_plan_skips_when_history_complete():
    parsed_movie = ParsedMovie(
        href='/v/abc',
        video_code='ABC-123',
        page_num=1,
        actor_name='Actor',
        magnet_links={
            'hacked_subtitle': 'magnet:?xt=urn:btih:1',
            'subtitle': 'magnet:?xt=urn:btih:2',
        },
        entry={
            'video_code': 'ABC-123',
            'rate': '4.5',
            'comment_number': '100',
        },
    )
    history_data = {
        '/v/abc': {
            'torrent_types': ['hacked_subtitle', 'subtitle'],
            'PerfectMatchIndicator': 1,
        }
    }

    plan = build_spider_ingestion_plan(
        parsed_movie,
        history_data=history_data,
        phase=1,
    )

    assert plan.should_skip is True
    assert plan.skip_reason == 'history_no_missing_types'
    assert plan.report_row is None


def test_build_spider_ingestion_plan_builds_report_and_history_payload():
    parsed_movie = ParsedMovie(
        href='/v/xyz',
        video_code='XYZ-999',
        page_num=2,
        actor_name='Actor',
        magnet_links={
            'subtitle': 'magnet:?xt=urn:btih:sub',
            'no_subtitle': 'magnet:?xt=urn:btih:nosub',
        },
        size_links={
            'subtitle': '10 GB',
            'no_subtitle': '9 GB',
        },
        file_count_links={
            'subtitle': 3,
        },
        resolution_links={
            'subtitle': 1080,
        },
        entry={
            'video_code': 'XYZ-999',
            'rate': '4.8',
            'comment_number': '88',
        },
    )

    plan = build_spider_ingestion_plan(
        parsed_movie,
        history_data={},
        phase=1,
    )

    assert plan.should_skip is False
    assert plan.report_row is not None
    assert plan.report_row['video_code'] == 'XYZ-999'
    assert plan.should_include_in_report is True
    assert plan.has_new_torrents is True
    assert plan.new_magnet_links == {'subtitle': 'magnet:?xt=urn:btih:sub'}
    assert plan.new_sizes == {'subtitle': '10 GB'}
    assert plan.new_file_counts == {'subtitle': 3}
    assert plan.new_resolutions == {'subtitle': 1080}


def test_check_redownload_upgrade_logs_video_code(caplog):
    history_data = {
        '/v/z4bgy5': {
            'video_code': 'IPZZ-414',
            'torrent_types': ['no_subtitle'],
            'size_no_subtitle': '1.41GB',
        }
    }
    magnet_links = {
        'no_subtitle': 'magnet:?xt=urn:btih:new',
        'size_no_subtitle': '3.52GB',
    }

    with caplog.at_level(logging.INFO, logger='packages.python.javdb_ingestion.policies'):
        categories = check_redownload_upgrade('/v/z4bgy5', history_data, magnet_links, threshold=0.30)

    assert categories == ['no_subtitle']
    assert 'Re-download upgrade for IPZZ-414 [no_subtitle]' in caplog.text
    assert '/v/z4bgy5' not in caplog.text


def test_build_spider_ingestion_plan_redownload_does_not_emit_dedup_for_size_only_upgrade():
    parsed_movie = ParsedMovie(
        href='/v/z4bgy5',
        video_code='IPZZ-414',
        page_num=1,
        actor_name='Actor',
        magnet_links={
            'no_subtitle': 'magnet:?xt=urn:btih:new',
        },
        size_links={
            'no_subtitle': '3.52GB',
        },
        entry={
            'video_code': 'IPZZ-414',
            'rate': '4.8',
            'comment_number': '120',
        },
    )
    history_data = {
        '/v/z4bgy5': {
            'video_code': 'IPZZ-414',
            'torrent_types': ['no_subtitle'],
            'size_no_subtitle': '1.41GB',
        }
    }
    rclone_entries = [
        RcloneEntry(
            video_code='IPZZ-414',
            sensor_category='有码',
            subtitle_category='无字',
            folder_path='drive:/IPZZ-414 [有码-无字]',
            folder_size=1513970770,
            file_count=1,
            scan_datetime='2026-03-25 21:54:53',
        )
    ]

    plan = build_spider_ingestion_plan(
        parsed_movie,
        history_data=history_data,
        phase=1,
        rclone_entries=rclone_entries,
        enable_dedup=True,
        enable_redownload=True,
        redownload_threshold=0.30,
    )

    assert plan.should_skip is False
    assert plan.redownload_categories == ['no_subtitle']
    assert plan.dedup_records == []
    assert plan.report_row is not None
    assert plan.report_row['video_code'] == 'IPZZ-414'
    assert plan.report_row['no_subtitle'] == 'magnet:?xt=urn:btih:new'


def test_build_alignment_upgrade_plan_emits_upgrade_rows_for_better_category():
    plan = build_alignment_upgrade_plan(
        detail_href='/v/abc',
        video_code='ABC-123',
        magnet_links={
            'hacked_subtitle': 'magnet:?xt=urn:btih:1',
            'subtitle': 'magnet:?xt=urn:btih:2',
        },
        inventory_entries=[
            {
                'FolderPath': 'drive:/ABC-123 [有码-无字]',
                'SensorCategory': '有码',
                'SubtitleCategory': '无字',
            }
        ],
    )

    assert plan.chosen_upgrade_category == 'subtitle,hacked_subtitle'
    assert plan.chosen_upgrade_categories == ['subtitle', 'hacked_subtitle']
    assert len(plan.qb_rows) == 1
    assert plan.qb_rows[0]['hacked_subtitle'] == 'magnet:?xt=urn:btih:1'
    assert plan.qb_rows[0]['subtitle'] == 'magnet:?xt=urn:btih:2'
    assert len(plan.purge_plan_rows) == 1


def test_build_alignment_upgrade_plan_can_upgrade_censored_and_uncensored_families():
    plan = build_alignment_upgrade_plan(
        detail_href='/v/dual',
        video_code='DUAL-001',
        magnet_links={
            'hacked_subtitle': 'magnet:?xt=urn:btih:hacked-sub',
            'subtitle': 'magnet:?xt=urn:btih:sub',
        },
        inventory_entries=[
            {
                'FolderPath': 'drive:/DUAL-001 [有码-无字]',
                'SensorCategory': '有码',
                'SubtitleCategory': '无字',
            },
            {
                'FolderPath': 'drive:/DUAL-001 [无码破解-无字]',
                'SensorCategory': '无码破解',
                'SubtitleCategory': '无字',
            },
        ],
    )

    assert plan.chosen_upgrade_category == 'subtitle,hacked_subtitle'
    assert plan.chosen_upgrade_categories == ['subtitle', 'hacked_subtitle']
    assert len(plan.qb_rows) == 1
    assert plan.qb_rows[0]['subtitle'] == 'magnet:?xt=urn:btih:sub'
    assert plan.qb_rows[0]['hacked_subtitle'] == 'magnet:?xt=urn:btih:hacked-sub'
    assert len(plan.purge_plan_rows) == 2
    assert {row['source_path'] for row in plan.purge_plan_rows} == {
        'drive:/DUAL-001 [有码-无字]',
        'drive:/DUAL-001 [无码破解-无字]',
    }


def test_build_alignment_upgrade_plan_keeps_uncensored_inventory_when_only_censored_upgrades():
    plan = build_alignment_upgrade_plan(
        detail_href='/v/mixed',
        video_code='MIXED-001',
        magnet_links={
            'subtitle': 'magnet:?xt=urn:btih:sub',
        },
        inventory_entries=[
            {
                'FolderPath': 'drive:/MIXED-001 [有码-无字]',
                'SensorCategory': '有码',
                'SubtitleCategory': '无字',
            },
            {
                'FolderPath': 'drive:/MIXED-001 [无码破解-无字]',
                'SensorCategory': '无码破解',
                'SubtitleCategory': '无字',
            },
        ],
    )

    assert plan.chosen_upgrade_category == 'subtitle'
    assert plan.chosen_upgrade_categories == ['subtitle']
    assert len(plan.purge_plan_rows) == 1
    assert plan.purge_plan_rows[0]['source_path'] == 'drive:/MIXED-001 [有码-无字]'
