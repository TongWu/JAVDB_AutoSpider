#!/usr/bin/env python3
"""Align rclone inventory with MovieHistory for missing movie codes.

Scope:
1) Only process movie codes present in RcloneInventory but missing in MovieHistory.
2) For each missing code, search JavDB by code, strictly match exact video code,
   parse detail page, and upsert MovieHistory/TorrentHistory.
3) Compare parsed torrents against inventory per family. The planner keeps the
   best censored variant and the best uncensored variant independently, then
   generates qBittorrent upgrade tasks and an rclone purge plan for only the
   lower-quality entries inside the upgraded family.

Proxy-backed fetching is enabled by default. Parallel mode (one worker per
proxy) is used when proxy is enabled and the proxy pool is configured. Use
``--no-proxy`` to force direct/sequential fetching for debugging.

HTTP fetching matches the main spider: initial requests do not force CF bypass;
``RequestHandler`` / global handler may enable bypass on fallback after failures.
Search uses **only the first results page** per video code.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue as queue_module
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

from api.parsers.common import normalize_javdb_href_path
from api.parsers.detail_parser import parse_detail_page
from api.parsers.index_parser import parse_index_page, find_exact_video_code_match
from scripts.spider.fetch.fallback import get_page_url
import scripts.spider.runtime.state as spider_state
from scripts.ingestion.adapters import (
    build_alignment_purge_plan_rows as _ie_build_alignment_purge_plan_rows,
    build_alignment_qb_row as _ie_build_alignment_qb_row,
)
from scripts.ingestion.planner import build_alignment_upgrade_plan
from scripts.ingestion.policies import (
    alignment_best_inventory_rank as _ie_best_inventory_rank,
    alignment_best_parsed_category as _ie_best_parsed_category,
    alignment_inventory_entry_rank as _ie_inventory_entry_rank,
    alignment_parsed_category_rank as _ie_parsed_category_rank,
)
from utils.infra.config_helper import cfg
from utils.infra.db import db_load_history, db_load_rclone_inventory, db_upsert_history, init_db
from utils.infra.logging_config import get_logger, setup_logging
from utils.domain.magnet_extractor import extract_magnets
from utils.infra.path_helper import ensure_dated_dir
from utils.domain.url_helper import build_search_url

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

_PURGE_PLAN_FIELDNAMES = [
    'video_code',
    'source_path',
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
    return _ie_parsed_category_rank(category)


def _inventory_entry_rank(entry: dict) -> int:
    return _ie_inventory_entry_rank(entry)


def _best_inventory_rank(entries: List[dict]) -> int:
    return _ie_best_inventory_rank(entries)


def _best_parsed_category(magnet_links: Dict[str, str]) -> str:
    return _ie_best_parsed_category(magnet_links)


def _to_qb_row(href: str, video_code: str, chosen_category: str, magnet_links: Dict[str, str]) -> dict:
    return _ie_build_alignment_qb_row(href, video_code, chosen_category, magnet_links)


def _to_purge_plan_rows(
    video_code: str,
    inventory_entries: List[dict],
    parsed_best_rank: int,
    new_torrent_category: str,
) -> List[dict]:
    return _ie_build_alignment_purge_plan_rows(
        video_code,
        inventory_entries,
        parsed_best_rank,
        new_torrent_category,
    )


def _build_db_upsert_kwargs(detail_href: str, video_code: str, magnet_links: dict,
                            actor_name: str, actor_gender: str, actor_link: str,
                            supporting_actors: str) -> dict:
    """Build keyword arguments for ``db_upsert_history``."""
    return {
        'href': detail_href,
        'video_code': video_code,
        'magnet_links': {
            'hacked_subtitle': magnet_links.get('hacked_subtitle', ''),
            'hacked_no_subtitle': magnet_links.get('hacked_no_subtitle', ''),
            'subtitle': magnet_links.get('subtitle', ''),
            'no_subtitle': magnet_links.get('no_subtitle', ''),
        },
        'size_links': {
            'hacked_subtitle': magnet_links.get('size_hacked_subtitle', ''),
            'hacked_no_subtitle': magnet_links.get('size_hacked_no_subtitle', ''),
            'subtitle': magnet_links.get('size_subtitle', ''),
            'no_subtitle': magnet_links.get('size_no_subtitle', ''),
        },
        'file_count_links': {
            'hacked_subtitle': int(magnet_links.get('file_count_hacked_subtitle', 0) or 0),
            'hacked_no_subtitle': int(magnet_links.get('file_count_hacked_no_subtitle', 0) or 0),
            'subtitle': int(magnet_links.get('file_count_subtitle', 0) or 0),
            'no_subtitle': int(magnet_links.get('file_count_no_subtitle', 0) or 0),
        },
        'resolution_links': {
            'hacked_subtitle': magnet_links.get('resolution_hacked_subtitle'),
            'hacked_no_subtitle': magnet_links.get('resolution_hacked_no_subtitle'),
            'subtitle': magnet_links.get('resolution_subtitle'),
            'no_subtitle': magnet_links.get('resolution_no_subtitle'),
        },
        'actor_name': actor_name,
        'actor_gender': actor_gender,
        'actor_link': actor_link,
        'supporting_actors': supporting_actors,
    }


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
) -> Optional[str]:
    # Same default as spider main: no forced CF bypass; handler may fall back.
    return spider_state.get_page(
        url,
        session=session,
        use_cookie=True,
        use_proxy=use_proxy,
        module_name='spider',
        max_retries=3,
        use_cf_bypass=False,
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


def _make_align_process_fn(inventory_map):
    """Build the ``process_fn`` for FetchEngine (advanced mode).

    Multi-step: search JavDB by code → fetch detail → parse → compare ranks.
    Returns a non-None dict on success or definitive miss; ``None`` signals a
    proxy-level fetch failure so the engine re-queues to another proxy.
    """
    from scripts.spider.fetch.fetch_engine import LoginRequired, WorkerContext, EngineTask

    def _align_process(ctx: WorkerContext, task: EngineTask):
        from utils.domain.url_helper import get_page_url as _get_page_url

        meta = task.meta
        video_code = meta['video_code']
        search_url = meta['search_url']
        base_url = meta['base_url']
        inventory_entries = inventory_map.get(video_code, [])

        # 1) Search first results page
        page_num = 1
        paged_url = _get_page_url(page_num, base_url, custom_url=search_url)
        search_html = ctx.fetch(paged_url)
        if not search_html:
            return None

        parsed = parse_index_page(search_html, page_num=page_num)
        exact_entry = None
        if parsed.has_movie_list and parsed.movies:
            exact_entry = find_exact_video_code_match(parsed.movies, video_code)

        if exact_entry is None:
            return {
                'status': 'search_miss',
                'video_code': video_code,
                'proxy_name': ctx.proxy_name,
                'worker_id': ctx.worker_id,
                'message': 'exact_video_code_not_found',
            }

        # 2) Fetch detail page
        detail_href = normalize_javdb_href_path(exact_entry.href)
        detail_url = urljoin(base_url + '/', detail_href.lstrip('/'))
        detail_html = ctx.fetch(detail_url)
        if not detail_html:
            return None

        # 3) Parse detail page
        detail = parse_detail_page(detail_html)
        if not detail.parse_success:
            return {
                'status': 'detail_parse_failed',
                'video_code': video_code,
                'href': detail_href,
                'detail_href': detail_href,
                'proxy_name': ctx.proxy_name,
                'worker_id': ctx.worker_id,
                'message': 'parse_detail_page returned parse_success=False',
            }

        magnets_payload = [m.to_dict() for m in detail.magnets]
        magnet_links = extract_magnets(magnets_payload, index=video_code)
        actor_name = detail.get_first_actor_name()
        actor_gender = detail.get_first_actor_gender()
        actor_link = detail.get_first_actor_href()
        supporting_actors = detail.get_supporting_actors_json()

        db_kwargs = _build_db_upsert_kwargs(
            detail_href, video_code, magnet_links,
            actor_name, actor_gender, actor_link, supporting_actors,
        )

        # 4) Compare ranks and build upgrade plan
        upgrade_plan = build_alignment_upgrade_plan(
            detail_href=detail_href,
            video_code=video_code,
            magnet_links=magnet_links,
            inventory_entries=inventory_entries,
        )

        return {
            'status': 'ok',
            'video_code': video_code,
            'href': detail_href,
            'detail_href': detail_href,
            'proxy_name': ctx.proxy_name,
            'worker_id': ctx.worker_id,
            'actor_name': actor_name,
            'chosen_upgrade_category': upgrade_plan.chosen_upgrade_category,
            'db_upsert_kwargs': db_kwargs,
            'qb_rows': upgrade_plan.qb_rows,
            'purge_plan_rows': upgrade_plan.purge_plan_rows,
        }

    return _align_process


# ---------------------------------------------------------------------------
# Alignment entry-point
# ---------------------------------------------------------------------------


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

    total = len(missing_codes)
    logger.info("Missing movie codes to align: %d", total)
    if not missing_codes:
        return 0

    # Common network setup
    reports_dir = cfg('REPORTS_DIR', 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    ban_log = os.path.join(reports_dir, 'proxy_bans.csv')
    use_proxy = getattr(args, 'use_proxy', not getattr(args, 'no_proxy', False))
    spider_state.setup_proxy_pool(ban_log_file=ban_log, use_proxy=use_proxy)
    spider_state.initialize_request_handler()
    base_url = cfg('BASE_URL', 'https://javdb.com').rstrip('/')

    process_results: List[MissingProcessResult] = []
    qb_rows: List[dict] = []
    purge_plan_rows: List[dict] = []
    rc = 0

    from scripts.spider.runtime.config import PROXY_POOL
    from scripts.spider.runtime.sleep import movie_sleep_mgr

    # ------------------------------------------------------------------
    # Parallel mode: FetchEngine (advanced) with one worker per proxy
    # ------------------------------------------------------------------
    if use_proxy and PROXY_POOL:
        from scripts.spider.fetch.fetch_engine import FetchEngine

        movie_sleep_mgr.apply_volume_multiplier(total)
        stop_event = threading.Event()

        engine = FetchEngine(
            process_fn=_make_align_process_fn(inventory),
            use_cookie=True,
            ban_log_file=ban_log,
            stop_event=stop_event,
            sleep_min=movie_sleep_mgr.sleep_min,
            sleep_max=movie_sleep_mgr.sleep_max,
        )
        engine.start()

        for i, code in enumerate(missing_codes, 1):
            engine.submit(
                build_search_url(code, f='all', base_url=base_url),
                entry_index=f"align-{i}/{total}",
                meta={
                    'video_code': code,
                    'search_url': build_search_url(code, f='all', base_url=base_url),
                    'base_url': base_url,
                },
            )
        engine.mark_done()

        logger.info(
            "Starting %d workers for %d alignment tasks (search + detail per code)",
            len(engine._workers), total,
        )

        processed = 0
        failed = 0
        skipped = 0
        parallel_interrupted = False

        def _apply_align_result(result):
            nonlocal processed, failed, skipped
            video_code = result.task.meta['video_code']
            idx_str = result.task.entry_index

            if not result.success:
                logger.warning("[%s] All proxies failed for %s", idx_str, video_code)
                process_results.append(MissingProcessResult(
                    video_code=video_code, status='all_proxies_failed',
                    message=f'failed on all proxies',
                ))
                failed += 1
                return

            data = result.data
            status = data['status']
            proxy_name = str(data.get('proxy_name') or 'unknown-proxy')
            worker_id = data.get('worker_id')
            worker_label = f"{proxy_name}#w{worker_id}" if worker_id is not None else proxy_name

            if status == 'search_miss':
                process_results.append(MissingProcessResult(
                    video_code=video_code, status='search_miss',
                    message=data.get('message', ''),
                ))
                logger.info("[%s][%s] No exact match for %s", idx_str, worker_label, video_code)
                skipped += 1
                return

            if status == 'detail_parse_failed':
                process_results.append(MissingProcessResult(
                    video_code=video_code, status='detail_parse_failed',
                    href=data.get('href', ''),
                    detail_href=data.get('detail_href', ''),
                    message=data.get('message', ''),
                ))
                logger.warning(
                    "[%s][%s] Detail parse failed for %s",
                    idx_str,
                    worker_label,
                    video_code,
                )
                failed += 1
                return

            if data.get('db_upsert_kwargs') and not args.dry_run:
                db_upsert_history(**data['db_upsert_kwargs'])
            qb_rows.extend(data.get('qb_rows', []))
            purge_plan_rows.extend(data.get('purge_plan_rows', []))

            process_results.append(MissingProcessResult(
                video_code=video_code, status='ok',
                href=data.get('href', ''),
                detail_href=data.get('detail_href', ''),
                actor_name=data.get('actor_name', ''),
                chosen_upgrade_category=data.get('chosen_upgrade_category', ''),
            ))
            logger.info("[%s][%s] Parsed %s", idx_str, worker_label, video_code)
            processed += 1

        try:
            for result in engine.results():
                _apply_align_result(result)
        except KeyboardInterrupt:
            parallel_interrupted = True
            logger.warning("Keyboard interrupt — shutting down engine …")
            orphaned = engine.shutdown(timeout=30)

            while True:
                try:
                    result = engine._result_queue.get_nowait()
                except queue_module.Empty:
                    break
                _apply_align_result(result)

            logger.info(
                "Alignment interrupted (parallel, %d workers). "
                "Processed: %d, Skipped: %d, Failed: %d — "
                "%d tasks orphaned",
                len(engine._workers), processed, skipped, failed, len(orphaned),
            )
        else:
            engine.shutdown()
            logger.info(
                "Alignment done (parallel, %d workers). "
                "Processed: %d, Skipped: %d, Failed: %d",
                len(engine._workers), processed, skipped, failed,
            )

        if parallel_interrupted:
            rc = 130

    else:
        # ------------------------------------------------------------------
        # Sequential fallback (no proxy or no PROXY_POOL configured)
        # ------------------------------------------------------------------
        session = requests.Session()

        for idx, code in enumerate(missing_codes, 1):
            logger.info("[%d/%d] Processing %s", idx, total, code)
            search_url = build_search_url(code, f='all', base_url=base_url)
            page_num = 1
            paged_url = get_page_url(page_num, custom_url=search_url)
            search_html = _fetch_html(session, paged_url, use_proxy=use_proxy)
            exact_entry = None
            if search_html:
                parsed = parse_index_page(search_html, page_num=page_num)
                if parsed.has_movie_list and parsed.movies:
                    exact_entry = find_exact_video_code_match(parsed.movies, code)

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
            detail_html = _fetch_html(session, detail_url, use_proxy=use_proxy)
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
            if not detail.parse_success:
                process_results.append(
                    MissingProcessResult(
                        video_code=code,
                        status='detail_parse_failed',
                        href=detail_href,
                        detail_href=detail_href,
                        message='parse_detail_page returned parse_success=False',
                    )
                )
                continue

            magnets_payload = [m.to_dict() for m in detail.magnets]
            magnet_links = extract_magnets(magnets_payload, index=code)
            actor_name = detail.get_first_actor_name()
            actor_gender = detail.get_first_actor_gender()
            actor_link = detail.get_first_actor_href()
            supporting_actors = detail.get_supporting_actors_json()

            if not args.dry_run:
                db_upsert_history(**_build_db_upsert_kwargs(
                    detail_href, code, magnet_links,
                    actor_name, actor_gender, actor_link, supporting_actors,
                ))

            inventory_entries = inventory.get(code, [])
            upgrade_plan = build_alignment_upgrade_plan(
                detail_href=detail_href,
                video_code=code,
                magnet_links=magnet_links,
                inventory_entries=inventory_entries,
            )
            qb_rows.extend(upgrade_plan.qb_rows)
            purge_plan_rows.extend(upgrade_plan.purge_plan_rows)

            process_results.append(
                MissingProcessResult(
                    video_code=code,
                    status='ok',
                    href=detail_href,
                    detail_href=detail_href,
                    actor_name=actor_name,
                    chosen_upgrade_category=upgrade_plan.chosen_upgrade_category,
                )
            )

    # ------------------------------------------------------------------
    # Write outputs (common for both paths)
    # ------------------------------------------------------------------
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = ensure_dated_dir(args.output_dir)
    process_csv = os.path.join(out_dir, f'InventoryHistoryAlign_Result_{timestamp}.csv')
    qb_csv = os.path.join(out_dir, f'InventoryHistoryAlign_QBUpgrade_{timestamp}.csv')
    purge_plan_csv = os.path.join(out_dir, f'InventoryHistoryAlign_PurgePlan_{timestamp}.csv')
    summary_json = os.path.join(out_dir, f'InventoryHistoryAlign_Summary_{timestamp}.json')

    _write_csv(
        process_csv,
        ['video_code', 'status', 'href', 'detail_href', 'actor_name', 'chosen_upgrade_category', 'message'],
        [r.__dict__ for r in process_results],
    )
    _write_csv(qb_csv, _QB_FIELDNAMES, qb_rows)
    _write_csv(purge_plan_csv, _PURGE_PLAN_FIELDNAMES, purge_plan_rows)

    summary = {
        'missing_codes_total': len(missing_codes),
        'processed_ok': sum(1 for r in process_results if r.status == 'ok'),
        'search_miss': sum(1 for r in process_results if r.status == 'search_miss'),
        'detail_fetch_failed': sum(1 for r in process_results if r.status == 'detail_fetch_failed'),
        'detail_parse_failed': sum(1 for r in process_results if r.status == 'detail_parse_failed'),
        'qb_upgrade_rows': len(qb_rows),
        'purge_plan_rows': len(purge_plan_rows),
        'dry_run': args.dry_run,
        'files': {
            'result_csv': process_csv,
            'qb_upgrade_csv': qb_csv,
            'purge_plan_csv': purge_plan_csv,
        },
    }
    with open(summary_json, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info("Alignment summary saved: %s", summary_json)
    logger.info("Result CSV: %s", process_csv)
    logger.info("qB Upgrade CSV: %s", qb_csv)
    logger.info("Purge-plan CSV: %s", purge_plan_csv)

    if rc != 0:
        return rc

    if args.enqueue_qb and qb_rows:
        qb_ok = _enqueue_qb_from_csv(qb_csv, use_proxy=use_proxy, category_override=args.qb_category)
        if not qb_ok:
            logger.error("qB enqueue failed")
            return 1

    if args.execute_delete and purge_plan_rows:
        from scripts.rclone_manager import run_execute_inventory_purge_from_csv

        sdrc = run_execute_inventory_purge_from_csv(
            purge_plan_csv,
            dry_run=args.dry_run,
        )
        if sdrc != 0:
            logger.error("Rclone purge execution failed")
            return sdrc

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Align inventory-only movie codes into MovieHistory with JavDB search/detail enrichment.',
    )
    parser.add_argument('--dry-run', action='store_true', help='Parse and plan only; do not write DB.')
    parser.add_argument('--limit', type=int, default=0, help='Max number of missing codes to process (0=all).')
    parser.add_argument('--codes', type=str, default='', help='Comma-separated codes to process.')
    parser.add_argument(
        '--no-proxy',
        action='store_true',
        help='Direct HTTP without spider proxy configuration (debug; proxy enabled by default).',
    )
    parser.add_argument('--use-proxy', dest='legacy_use_proxy', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--output-dir', type=str, default=cfg('MIGRATION_REPORT_DIR', 'reports/Migration'))
    parser.add_argument('--enqueue-qb', action='store_true', help='Enqueue upgrade magnets to qBittorrent.')
    parser.add_argument('--qb-category', type=str, default=cfg('TORRENT_CATEGORY_ADHOC', 'Ad Hoc'))
    parser.add_argument(
        '--execute-delete',
        action='store_true',
        help='After planning, run rclone purge on each source_path in the purge-plan CSV (destructive).',
    )
    args = parser.parse_args()
    if args.no_proxy and args.legacy_use_proxy:
        parser.error('--no-proxy and deprecated --use-proxy cannot be used together')
    if args.legacy_use_proxy:
        logger.warning(
            '--use-proxy is deprecated; alignment now uses proxy by default. '
            'Use --no-proxy to disable proxy.',
        )
    setattr(args, 'use_proxy', not args.no_proxy)
    return args


def main() -> int:
    args = parse_args()
    return run_alignment(args)


if __name__ == '__main__':
    raise SystemExit(main())
