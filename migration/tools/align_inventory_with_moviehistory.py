#!/usr/bin/env python3
"""Align rclone inventory with MovieHistory for missing movie codes.

Scope:
1) Only process movie codes present in RcloneInventory but missing in MovieHistory.
2) For each missing code, search JavDB by code, strictly match exact video code,
   parse detail page, and upsert MovieHistory/TorrentHistory.
3) Compare parsed torrent category vs current inventory category. If parsed is
   better, generate qBittorrent upgrade tasks and rclone soft-delete plan rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import requests

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

from api.parsers.common import normalize_javdb_href_path
from api.parsers.detail_parser import parse_detail_page
from api.parsers.index_parser import parse_index_page, find_exact_video_code_match
from scripts.spider.fallback import get_page_url
import scripts.spider.state as spider_state
from utils.config_helper import cfg
from utils.db import db_load_history, db_load_rclone_inventory, db_upsert_history, init_db
from utils.logging_config import get_logger, setup_logging
from utils.magnet_extractor import extract_magnets
from utils.path_helper import ensure_dated_dir
from utils.url_helper import build_search_url

setup_logging()
logger = get_logger(__name__)

_QB_FIELDNAMES = [
    'href',
    'video_code',
    'page',
    'hacked_subtitle',
    'hacked_no_subtitle',
    'subtitle',
    'no_subtitle',
]

_SOFT_DELETE_FIELDNAMES = [
    'video_code',
    'source_path',
    'destination_path',
    'existing_sensor',
    'existing_subtitle',
    'new_torrent_category',
    'reason',
]


@dataclass
class MissingProcessResult:
    video_code: str
    status: str
    href: str = ''
    detail_href: str = ''
    actor_name: str = ''
    chosen_upgrade_category: str = ''
    message: str = ''


def _normalize_code(value: str) -> str:
    return (value or '').strip().upper()


def compute_missing_codes(
    inventory: Dict[str, List[dict]],
    history_by_href: Dict[str, dict],
    only_codes: Optional[Iterable[str]] = None,
) -> List[str]:
    history_codes = {
        _normalize_code(v.get('VideoCode', ''))
        for v in history_by_href.values()
        if _normalize_code(v.get('VideoCode', ''))
    }
    inventory_codes = {_normalize_code(c) for c in inventory.keys() if _normalize_code(c)}
    missing = sorted(inventory_codes - history_codes)
    if only_codes:
        wanted = {_normalize_code(c) for c in only_codes if _normalize_code(c)}
        missing = [c for c in missing if c in wanted]
    return missing


def _parsed_category_rank(category: str) -> int:
    rank_map = {
        'hacked_subtitle': 40,      # 无码破解 + 中字
        'hacked_no_subtitle': 30,   # 无码破解 + 无字
        'subtitle': 20,             # 有码 + 中字
        'no_subtitle': 10,          # 有码 + 无字
    }
    return rank_map.get(category, 0)


def _inventory_entry_rank(entry: dict) -> int:
    sensor = (entry.get('SensorCategory') or entry.get('sensor_category') or '').strip()
    subtitle = (entry.get('SubtitleCategory') or entry.get('subtitle_category') or '').strip()
    if sensor == '有码':
        return 20 if subtitle == '中字' else 10
    if sensor == '无码破解':
        return 40 if subtitle == '中字' else 30
    if sensor == '无码':
        return 55 if subtitle == '中字' else 50
    if sensor == '无码流出':
        return 65 if subtitle == '中字' else 60
    return 0


def _best_inventory_rank(entries: List[dict]) -> int:
    if not entries:
        return 0
    return max(_inventory_entry_rank(e) for e in entries)


def _best_parsed_category(magnet_links: Dict[str, str]) -> str:
    candidates = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
    best = ''
    best_rank = 0
    for cat in candidates:
        if not magnet_links.get(cat):
            continue
        rank = _parsed_category_rank(cat)
        if rank > best_rank:
            best = cat
            best_rank = rank
    return best


def _to_qb_row(href: str, video_code: str, chosen_category: str, magnet_links: Dict[str, str]) -> dict:
    row = {
        'href': href,
        'video_code': video_code,
        'page': 1,
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
    }
    if chosen_category:
        row[chosen_category] = magnet_links.get(chosen_category, '')
    return row


def _to_soft_delete_rows(
    video_code: str,
    inventory_entries: List[dict],
    parsed_best_rank: int,
    new_torrent_category: str,
    backup_prefix: str,
) -> List[dict]:
    rows: List[dict] = []
    for entry in inventory_entries:
        src = (entry.get('FolderPath') or entry.get('folder_path') or '').strip()
        if not src:
            continue
        if _inventory_entry_rank(entry) >= parsed_best_rank:
            continue
        dst = ''
        if backup_prefix:
            if ':' in src:
                _, rel = src.split(':', 1)
            else:
                rel = src
            dst = f"{backup_prefix.rstrip('/')}/{rel.lstrip('/')}"
        rows.append({
            'video_code': video_code,
            'source_path': src,
            'destination_path': dst,
            'existing_sensor': entry.get('SensorCategory') or entry.get('sensor_category') or '',
            'existing_subtitle': entry.get('SubtitleCategory') or entry.get('subtitle_category') or '',
            'new_torrent_category': new_torrent_category,
            'reason': 'parsed_better_version',
        })
    return rows


def _init_spider_requester(use_proxy: bool) -> requests.Session:
    reports_dir = cfg('REPORTS_DIR', 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    ban_log = os.path.join(reports_dir, 'proxy_bans.csv')
    spider_state.setup_proxy_pool(ban_log_file=ban_log, use_proxy=use_proxy)
    spider_state.initialize_request_handler()
    return requests.Session()


def _fetch_html(
    session: requests.Session,
    url: str,
    *,
    use_proxy: bool,
    use_cf_bypass: bool,
) -> Optional[str]:
    return spider_state.get_page(
        url,
        session=session,
        use_cookie=True,
        use_proxy=use_proxy,
        module_name='spider',
        max_retries=3,
        use_cf_bypass=use_cf_bypass,
    )


def _write_csv(path: str, fieldnames: List[str], rows: List[dict]) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _enqueue_qb_from_csv(csv_path: str, use_proxy: bool, category_override: str = '') -> bool:
    from scripts import qb_uploader

    qb_uploader.initialize_proxy_helper(use_proxy)
    if not qb_uploader.test_qbittorrent_connection(use_proxy):
        logger.error("qBittorrent is not reachable")
        return False
    session = requests.Session()
    if not qb_uploader.login_to_qbittorrent(session, use_proxy):
        logger.error("qBittorrent login failed")
        return False

    torrents, exists = qb_uploader.read_csv_file(csv_path)
    if not exists:
        logger.error("qB input CSV not found: %s", csv_path)
        return False
    if not torrents:
        logger.info("No upgrade torrents to enqueue")
        return True

    existing_hashes = qb_uploader.get_existing_torrents(session, use_proxy)
    added = 0
    for t in torrents:
        if qb_uploader.is_torrent_exists(t['magnet'], existing_hashes):
            continue
        ok = qb_uploader.add_torrent_to_qbittorrent(
            session,
            t['magnet'],
            t['title'],
            mode='adhoc',
            use_proxy=use_proxy,
            category_override=category_override or None,
        )
        if ok:
            added += 1
    logger.info("qB enqueue complete: %d/%d added", added, len(torrents))
    return True


def run_alignment(args: argparse.Namespace) -> int:
    init_db(force=True)
    history = db_load_history()
    inventory = db_load_rclone_inventory()
    only_codes = []
    if args.codes:
        only_codes = [c.strip() for c in args.codes.split(',') if c.strip()]

    missing_codes = compute_missing_codes(inventory, history, only_codes=only_codes)
    if args.limit and args.limit > 0:
        missing_codes = missing_codes[: args.limit]

    logger.info("Missing movie codes to align: %d", len(missing_codes))
    if not missing_codes:
        return 0

    session = _init_spider_requester(use_proxy=args.use_proxy)
    base_url = cfg('BASE_URL', 'https://javdb.com').rstrip('/')

    process_results: List[MissingProcessResult] = []
    qb_rows: List[dict] = []
    soft_delete_rows: List[dict] = []

    for idx, code in enumerate(missing_codes, 1):
        logger.info("[%d/%d] Processing %s", idx, len(missing_codes), code)
        search_url = build_search_url(code, f='all', base_url=base_url)
        exact_entry = None
        search_html = None

        for page_num in range(1, args.max_search_pages + 1):
            paged_url = get_page_url(page_num, custom_url=search_url)
            search_html = _fetch_html(
                session,
                paged_url,
                use_proxy=args.use_proxy,
                use_cf_bypass=args.use_cf_bypass,
            )
            if not search_html:
                continue
            parsed = parse_index_page(search_html, page_num=page_num)
            if not parsed.has_movie_list or not parsed.movies:
                continue
            exact_entry = find_exact_video_code_match(parsed.movies, code)
            if exact_entry is not None:
                break

        if exact_entry is None:
            process_results.append(
                MissingProcessResult(
                    video_code=code,
                    status='search_miss',
                    message='exact_video_code_not_found',
                )
            )
            continue

        detail_href = normalize_javdb_href_path(exact_entry.href)
        detail_url = urljoin(base_url + '/', detail_href.lstrip('/'))
        detail_html = _fetch_html(
            session,
            detail_url,
            use_proxy=args.use_proxy,
            use_cf_bypass=args.use_cf_bypass,
        )
        if not detail_html:
            process_results.append(
                MissingProcessResult(
                    video_code=code,
                    status='detail_fetch_failed',
                    href=detail_href,
                    detail_href=detail_href,
                )
            )
            continue

        detail = parse_detail_page(detail_html)
        magnets_payload = [m.to_dict() for m in detail.magnets]
        magnet_links = extract_magnets(magnets_payload, index=code)
        actor_name = detail.get_first_actor_name()
        actor_gender = detail.get_first_actor_gender()
        actor_link = detail.get_first_actor_href()
        supporting_actors = detail.get_supporting_actors_json()

        if not args.dry_run:
            db_upsert_history(
                href=detail_href,
                video_code=code,
                magnet_links={
                    'hacked_subtitle': magnet_links.get('hacked_subtitle', ''),
                    'hacked_no_subtitle': magnet_links.get('hacked_no_subtitle', ''),
                    'subtitle': magnet_links.get('subtitle', ''),
                    'no_subtitle': magnet_links.get('no_subtitle', ''),
                },
                size_links={
                    'hacked_subtitle': magnet_links.get('size_hacked_subtitle', ''),
                    'hacked_no_subtitle': magnet_links.get('size_hacked_no_subtitle', ''),
                    'subtitle': magnet_links.get('size_subtitle', ''),
                    'no_subtitle': magnet_links.get('size_no_subtitle', ''),
                },
                file_count_links={
                    'hacked_subtitle': int(magnet_links.get('file_count_hacked_subtitle', 0) or 0),
                    'hacked_no_subtitle': int(magnet_links.get('file_count_hacked_no_subtitle', 0) or 0),
                    'subtitle': int(magnet_links.get('file_count_subtitle', 0) or 0),
                    'no_subtitle': int(magnet_links.get('file_count_no_subtitle', 0) or 0),
                },
                resolution_links={
                    'hacked_subtitle': magnet_links.get('resolution_hacked_subtitle'),
                    'hacked_no_subtitle': magnet_links.get('resolution_hacked_no_subtitle'),
                    'subtitle': magnet_links.get('resolution_subtitle'),
                    'no_subtitle': magnet_links.get('resolution_no_subtitle'),
                },
                actor_name=actor_name,
                actor_gender=actor_gender,
                actor_link=actor_link,
                supporting_actors=supporting_actors,
            )

        parsed_best_cat = _best_parsed_category(magnet_links)
        parsed_best_rank = _parsed_category_rank(parsed_best_cat)
        inventory_entries = inventory.get(code, [])
        inventory_best_rank = _best_inventory_rank(inventory_entries)

        if parsed_best_cat and parsed_best_rank > inventory_best_rank:
            qb_rows.append(_to_qb_row(detail_href, code, parsed_best_cat, magnet_links))
            soft_delete_rows.extend(
                _to_soft_delete_rows(
                    code,
                    inventory_entries,
                    parsed_best_rank,
                    parsed_best_cat,
                    args.soft_delete_backup_prefix,
                )
            )

        process_results.append(
            MissingProcessResult(
                video_code=code,
                status='ok',
                href=detail_href,
                detail_href=detail_href,
                actor_name=actor_name,
                chosen_upgrade_category=parsed_best_cat,
            )
        )

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = ensure_dated_dir(args.output_dir)
    process_csv = os.path.join(out_dir, f'InventoryHistoryAlign_Result_{timestamp}.csv')
    qb_csv = os.path.join(out_dir, f'InventoryHistoryAlign_QBUpgrade_{timestamp}.csv')
    soft_delete_csv = os.path.join(out_dir, f'InventoryHistoryAlign_SoftDelete_{timestamp}.csv')
    summary_json = os.path.join(out_dir, f'InventoryHistoryAlign_Summary_{timestamp}.json')

    _write_csv(
        process_csv,
        ['video_code', 'status', 'href', 'detail_href', 'actor_name', 'chosen_upgrade_category', 'message'],
        [r.__dict__ for r in process_results],
    )
    _write_csv(qb_csv, _QB_FIELDNAMES, qb_rows)
    _write_csv(soft_delete_csv, _SOFT_DELETE_FIELDNAMES, soft_delete_rows)

    summary = {
        'missing_codes_total': len(missing_codes),
        'processed_ok': sum(1 for r in process_results if r.status == 'ok'),
        'search_miss': sum(1 for r in process_results if r.status == 'search_miss'),
        'detail_fetch_failed': sum(1 for r in process_results if r.status == 'detail_fetch_failed'),
        'qb_upgrade_rows': len(qb_rows),
        'soft_delete_rows': len(soft_delete_rows),
        'dry_run': args.dry_run,
        'files': {
            'result_csv': process_csv,
            'qb_upgrade_csv': qb_csv,
            'soft_delete_csv': soft_delete_csv,
        },
    }
    with open(summary_json, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info("Alignment summary saved: %s", summary_json)
    logger.info("Result CSV: %s", process_csv)
    logger.info("qB Upgrade CSV: %s", qb_csv)
    logger.info("Soft-delete CSV: %s", soft_delete_csv)

    if args.enqueue_qb and qb_rows:
        qb_ok = _enqueue_qb_from_csv(qb_csv, use_proxy=args.use_proxy, category_override=args.qb_category)
        if not qb_ok:
            logger.error("qB enqueue failed")
            return 1

    if args.execute_soft_delete and soft_delete_rows:
        from scripts.rclone_manager import run_execute_soft_delete_from_csv

        rc = run_execute_soft_delete_from_csv(
            soft_delete_csv,
            dry_run=args.dry_run,
            backup_prefix=args.soft_delete_backup_prefix,
        )
        if rc != 0:
            logger.error("Soft-delete execution failed")
            return rc

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Align inventory-only movie codes into MovieHistory with JavDB search/detail enrichment.',
    )
    parser.add_argument('--dry-run', action='store_true', help='Parse and plan only; do not write DB.')
    parser.add_argument('--limit', type=int, default=0, help='Max number of missing codes to process (0=all).')
    parser.add_argument('--codes', type=str, default='', help='Comma-separated codes to process.')
    parser.add_argument('--max-search-pages', type=int, default=3, help='Search pagination depth per code.')
    parser.add_argument('--use-proxy', action='store_true', help='Use spider proxy configuration.')
    parser.add_argument('--use-cf-bypass', action='store_true', help='Enable CF bypass in requester.')
    parser.add_argument('--output-dir', type=str, default=cfg('MIGRATION_REPORT_DIR', 'reports/Migration'))
    parser.add_argument('--enqueue-qb', action='store_true', help='Enqueue upgrade magnets to qBittorrent.')
    parser.add_argument('--qb-category', type=str, default=cfg('TORRENT_CATEGORY_ADHOC', 'Ad Hoc'))
    parser.add_argument('--execute-soft-delete', action='store_true', help='Execute rclone soft-delete move after planning.')
    parser.add_argument(
        '--soft-delete-backup-prefix',
        type=str,
        default=cfg('RCLONE_SOFT_DELETE_BACKUP_PREFIX', ''),
        help='Backup prefix for soft-delete destination (e.g. remote:backup_root).',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_alignment(args)


if __name__ == '__main__':
    raise SystemExit(main())
