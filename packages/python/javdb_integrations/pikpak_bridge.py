import asyncio
import argparse
import csv
import os
import time
import sys
from pathlib import Path
from datetime import datetime, timedelta
import requests
from pikpakapi import PikPakApi

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.python.javdb_platform.config_helper import cfg

QB_HOST = cfg('QB_HOST', 'your_qbittorrent_ip')
QB_PORT = cfg('QB_PORT', 'your_qbittorrent_port')
QB_USERNAME = cfg('QB_USERNAME', 'your_qbittorrent_username')
QB_PASSWORD = cfg('QB_PASSWORD', 'your_qbittorrent_password')
PIKPAK_EMAIL = cfg('PIKPAK_EMAIL', '')
PIKPAK_PASSWORD = cfg('PIKPAK_PASSWORD', '')
PIKPAK_LOG_FILE = cfg('PIKPAK_LOG_FILE', 'logs/pikpak_bridge.log')
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
TORRENT_CATEGORY = cfg('TORRENT_CATEGORY', 'JavDB')
TORRENT_CATEGORY_ADHOC = cfg('TORRENT_CATEGORY_ADHOC', 'Ad Hoc')

# Optional adhoc qBittorrent instance
QB_URL_ADHOC = cfg('QB_URL_ADHOC', '')
QB_USERNAME_ADHOC = cfg('QB_USERNAME_ADHOC', '') or QB_USERNAME  # fallback to primary
QB_PASSWORD_ADHOC = cfg('QB_PASSWORD_ADHOC', '') or QB_PASSWORD  # fallback to primary

GIT_USERNAME = cfg('GIT_USERNAME', 'github-actions')
GIT_PASSWORD = cfg('GIT_PASSWORD', '')
GIT_REPO_URL = cfg('GIT_REPO_URL', '')
GIT_BRANCH = cfg('GIT_BRANCH', 'main')

PIKPAK_REQUEST_DELAY = cfg('PIKPAK_REQUEST_DELAY', 3)

PROXY_HTTP = cfg('PROXY_HTTP', None)
PROXY_HTTPS = cfg('PROXY_HTTPS', None)
PROXY_MODULES = cfg('PROXY_MODULES', ['spider'])

# Proxy pool
PROXY_MODE = cfg('PROXY_MODE', 'pool')
PROXY_POOL = cfg('PROXY_POOL', [])
PROXY_POOL_MAX_FAILURES = cfg('PROXY_POOL_MAX_FAILURES', 3)
QB_ALLOW_INSECURE_HTTP = cfg('QB_ALLOW_INSECURE_HTTP', False)

from packages.python.javdb_platform.logging_config import setup_logging, get_logger
from packages.python.javdb_platform.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials
from packages.python.javdb_platform.proxy_policy import (
    add_proxy_arguments,
    describe_proxy_override,
    resolve_proxy_override,
    should_proxy_module,
)
from packages.python.javdb_core.masking import mask_ip_address, mask_username, mask_email, mask_full

# --------------------------
# Setup Logging
# --------------------------
setup_logging(log_file=PIKPAK_LOG_FILE)
logger = get_logger(__name__)

# Import proxy pool
from packages.python.javdb_platform.proxy_pool import ProxyPool, create_proxy_pool_from_config

# Import proxy helper from request handler
from packages.python.javdb_platform.request_handler import ProxyHelper, create_proxy_helper_from_config
from packages.python.javdb_platform.qb_config import (
    qb_allow_insecure_http,
    qb_base_url_candidates,
    qb_verify_tls,
)
from packages.python.javdb_integrations.qb_client import (
    QBittorrentClient as _SharedQBittorrentClient,
    remove_completed_torrents_keep_files as _shared_remove_completed,
)

QB_ALLOW_INSECURE_HTTP = qb_allow_insecure_http(QB_ALLOW_INSECURE_HTTP)

# Global proxy pool instance
global_proxy_pool = None

# Global proxy helper instance
global_proxy_helper = None

# Categories to process (from config)
CATEGORIES = [TORRENT_CATEGORY, TORRENT_CATEGORY_ADHOC]
PIKPAK_HISTORY_FILE = os.path.join(REPORTS_DIR, "pikpak_bridge_history.csv")


# --------------------------
# Proxy Helper Functions
# --------------------------
def get_proxies_dict(module_name, use_proxy_flag):
    """
    Get proxies dictionary for requests if module should use proxy.
    Delegated to global_proxy_helper.
    """
    if global_proxy_helper is None:
        logger.warning(f"[{module_name}] Proxy helper not initialized")
        return None
    
    return global_proxy_helper.get_proxies_dict(module_name, use_proxy_flag)


def initialize_proxy_helper(proxy_override):
    """Initialize global proxy pool and proxy helper."""
    global global_proxy_pool, global_proxy_helper
    
    if proxy_override is False:
        global_proxy_pool = None
        # When use_proxy=False, don't pass proxy configs to ensure no proxy is used
        global_proxy_helper = create_proxy_helper_from_config(
            proxy_pool=None,
            proxy_modules=PROXY_MODULES,
            proxy_mode=PROXY_MODE,
            proxy_http=None,
            proxy_https=None
        )
        return
    
    # Check if we have PROXY_POOL configuration
    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Proxy pool initialized successfully")
        elif PROXY_MODE == 'single':
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Single proxy initialized: {PROXY_POOL[0].get('name', 'Main-Proxy')}")
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        legacy_proxy = {
            'name': 'Legacy-Proxy',
            'http': PROXY_HTTP,
            'https': PROXY_HTTPS
        }
        global_proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            max_failures=PROXY_POOL_MAX_FAILURES
        )
    else:
        logger.warning("Proxy enabled but no proxy configuration found")
        global_proxy_pool = None
    
    # Create proxy helper with the initialized pool
    global_proxy_helper = create_proxy_helper_from_config(
        proxy_pool=global_proxy_pool,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS
    )
    logger.info("Proxy helper initialized successfully")


# --------------------------
# qBittorrent Client
# --------------------------
class QBittorrentClient(_SharedQBittorrentClient):
    """Backwards-compatible alias of the shared ``QBittorrentClient``.

    The pikpak_bridge historically owned this class; it now lives in
    ``packages.python.javdb_integrations.qb_client``. This subclass keeps
    the module-level import path working and wires the proxy helper that
    pikpak_bridge uses ('pikpak' module)."""

    def __init__(self, base_urls, username, password, use_proxy=False):
        super().__init__(
            base_urls,
            username,
            password,
            use_proxy=use_proxy,
            proxies_getter=lambda flag=use_proxy: get_proxies_dict('pikpak', flag),
        )


def remove_completed_torrents_keep_files(
    qb_client, categories, dry_run=False, qb_label="qBittorrent"
):
    """Thin wrapper around the shared cleanup implementation, kept here so
    existing callers and tests that import it from ``pikpak_bridge``
    continue to work. See
    ``packages.python.javdb_integrations.qb_client.remove_completed_torrents_keep_files``
    for the canonical implementation."""
    return _shared_remove_completed(
        qb_client, categories, dry_run=dry_run, qb_label=qb_label
    )


# --------------------------
# PikPak API (using pikpakapi)
# --------------------------
async def process_pikpak_batch(magnets, email, password, delay_between_requests=3):
    """
    Process PikPak offline downloads in batch with delay between requests to avoid rate limiting
    
    Args:
        magnets: List of magnet URIs to download
        email: PikPak email
        password: PikPak password
        delay_between_requests: Delay in seconds between each download request (default: 3)
    """
    if not magnets:
        return [], []
        
    client = PikPakApi(username=email, password=password)
    await client.login()
    await client.refresh_access_token()

    success_magnets = []
    failed_magnets = []
    
    logger.info(f"Starting batch upload of {len(magnets)} torrents to PikPak as {mask_email(email)}...")
    
    for i, magnet in enumerate(magnets):
        try:
            logger.info(f"Processing magnet {i+1}/{len(magnets)}: {magnet[:100]}...")
            result = await client.offline_download(magnet)
            
            logger.info(f"Successfully added magnet to PikPak: {magnet[:100]}...")
            success_magnets.append(magnet)
            
            # Add delay between requests (except for the last one)
            if i < len(magnets) - 1:
                logger.debug(f"Waiting {delay_between_requests} seconds before next request...")
                await asyncio.sleep(delay_between_requests)
                
        except Exception as e:
            logger.error(f"Failed to add magnet: {magnet[:100]}..., Error: {e}")
            failed_magnets.append((magnet, str(e)))
            
            # Still add delay even after failed requests to be respectful
            if i < len(magnets) - 1:
                logger.debug(f"Waiting {delay_between_requests} seconds before next request...")
                await asyncio.sleep(delay_between_requests)
    
    logger.info(f"Batch upload completed: {len(success_magnets)} successful, {len(failed_magnets)} failed")
    return success_magnets, failed_magnets


async def process_pikpak_single(magnet, email, password):
    """
    Process a single PikPak offline download (for backward compatibility)
    """
    return await process_pikpak_batch([magnet], email, password)


# --------------------------
# PikPak History Management
# --------------------------
def save_to_pikpak_history(torrent_info, transfer_status, error_msg=None):
    """Save torrent transfer information to PikPak history."""
    from packages.python.javdb_platform.config_helper import use_sqlite, use_csv

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    record = {
        'torrent_hash': torrent_info['hash'],
        'torrent_name': torrent_info['name'],
        'category': torrent_info.get('category', 'Unknown'),
        'magnet_uri': torrent_info['magnet_uri'],
        'added_to_qb_date': datetime.fromtimestamp(torrent_info['added_on']).strftime("%Y-%m-%d %H:%M:%S"),
        'deleted_from_qb_date': current_time if transfer_status == 'success' else '',
        'uploaded_to_pikpak_date': current_time if transfer_status in ['success', 'failed_but_deleted'] else '',
        'transfer_status': transfer_status,
        'error_message': error_msg or ''
    }

    if use_sqlite():
        try:
            from packages.python.javdb_platform.db import init_db, db_append_pikpak_history
            init_db()
            db_append_pikpak_history(record)
        except Exception as e:
            logger.warning(f"Failed to write pikpak history to SQLite: {e}")

    if use_csv():
        os.makedirs(os.path.dirname(PIKPAK_HISTORY_FILE), exist_ok=True)
        file_exists = os.path.exists(PIKPAK_HISTORY_FILE)
        with open(PIKPAK_HISTORY_FILE, 'a', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                'torrent_hash', 'torrent_name', 'category', 'magnet_uri', 'added_to_qb_date',
                'deleted_from_qb_date', 'uploaded_to_pikpak_date', 'transfer_status', 'error_message'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(record)

    logger.info(f"Saved to PikPak history: {torrent_info['name']} - {transfer_status}")


# --------------------------
# Main Logic
# --------------------------
def pikpak_bridge(days, dry_run, batch_mode=True, use_proxy=None, from_pipeline=False, session_id=None):
    cutoff_date = (datetime.now() - timedelta(days=days)).date()
    logger.info(f"Processing torrents older than {days} days (before {cutoff_date})")
    
    # Initialize proxy helper
    initialize_proxy_helper(use_proxy)
    
    proxy_active = should_proxy_module('pikpak', use_proxy, PROXY_MODULES, proxy_mode=PROXY_MODE)
    logger.info(f"Proxy policy for PikPak: {describe_proxy_override(use_proxy)}")

    if proxy_active:
        if global_proxy_helper is not None:
            stats = global_proxy_helper.get_statistics()
            
            if PROXY_MODE == 'pool':
                logger.info(f"PROXY POOL MODE for PikPak bridge: {stats['total_proxies']} proxies with automatic failover")
            elif PROXY_MODE == 'single':
                logger.info(f"SINGLE PROXY MODE for PikPak bridge: Using main proxy only")
                if stats['total_proxies'] > 0 and stats['proxies']:
                    main_proxy_name = stats['proxies'][0]['name']
                    logger.info(f"Main proxy: {main_proxy_name}")
        else:
            logger.warning("PROXY ENABLED: But no proxy configured")
    else:
        logger.info("Proxy disabled for PikPak requests")

    qb = QBittorrentClient(
        qb_base_url_candidates(),
        QB_USERNAME,
        QB_PASSWORD,
        use_proxy,
    )
    remove_completed_torrents_keep_files(qb, CATEGORIES, dry_run=dry_run, qb_label="Primary QB")
    torrents = qb.get_torrents_multiple_categories(CATEGORIES)
    logger.info(f"Found {len(torrents)} torrents across categories {CATEGORIES} (primary QB)")

    # Tag each torrent with the set of QB clients that hold a copy — when
    # both the primary and adhoc QB have the same hash we need to delete
    # from *every* client after a successful PikPak upload, otherwise the
    # leftover copy keeps seeding stale files.
    torrent_qb_map: dict = {}  # torrent_hash -> set[QBittorrentClient]
    for t in torrents:
        torrent_qb_map.setdefault(t['hash'], set()).add(qb)

    # Scan adhoc QB instance if configured
    qb_adhoc = None
    if QB_URL_ADHOC:
        try:
            adhoc_candidates = qb_base_url_candidates(
                QB_URL_ADHOC,
                allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
            )
            qb_adhoc = QBittorrentClient(
                adhoc_candidates,
                QB_USERNAME_ADHOC,
                QB_PASSWORD_ADHOC,
                use_proxy,
            )
            adhoc_categories = [TORRENT_CATEGORY_ADHOC]
            remove_completed_torrents_keep_files(
                qb_adhoc, adhoc_categories, dry_run=dry_run, qb_label="Adhoc QB"
            )
            adhoc_torrents = qb_adhoc.get_torrents_multiple_categories(adhoc_categories)
            logger.info(f"Found {len(adhoc_torrents)} torrents in category {adhoc_categories} (adhoc QB)")

            # Deduplicate by hash — adhoc torrents that already exist in
            # primary are *not* re-added to ``torrents`` (to avoid double
            # processing), but their adhoc QB is still tracked so deletion
            # after upload cleans up both copies.
            existing_hashes = {t['hash'] for t in torrents}
            for t in adhoc_torrents:
                torrent_qb_map.setdefault(t['hash'], set()).add(qb_adhoc)
                if t['hash'] not in existing_hashes:
                    torrents.append(t)
                    # Track the hash so any repeat within ``adhoc_torrents``
                    # itself (defensive — the adhoc QB API shouldn't return
                    # duplicates, but nothing guarantees it) is collapsed
                    # instead of appended a second time.
                    existing_hashes.add(t['hash'])
                else:
                    logger.debug(f"Skipping duplicate torrent from adhoc QB: {t['name']}")
        except Exception as e:
            logger.warning(f"Failed to connect to adhoc qBittorrent: {e}")
            logger.warning("Continuing with primary QB only")

    old_torrents = [t for t in torrents if datetime.fromtimestamp(t['added_on']).date() <= cutoff_date]
    logger.info(f"Filtered {len(old_torrents)} torrents older than {days} days")

    if not old_torrents:
        logger.info("No torrents to process.")
        return

    if dry_run:
        logger.info(f"[Dry-Run] Would process {len(old_torrents)} torrents:")
        for torrent in old_torrents:
            logger.info(f"[Dry-Run] {torrent['name']} (added: {datetime.fromtimestamp(torrent['added_on']).strftime('%Y-%m-%d %H:%M:%S')})")
        
        # Dry-run summary with category breakdown
        category_counts = {}
        for torrent in old_torrents:
            category = torrent.get('category', 'Unknown')
            category_counts[category] = category_counts.get(category, 0) + 1
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("PIKPAK BRIDGE DRY-RUN SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Categories found: {list(category_counts.keys())}")
        logger.info(f"Total torrents found (older than {days} days): {len(old_torrents)}")
        
        # Per-category breakdown
        for category, count in category_counts.items():
            logger.info(f"Category '{category}': {count} torrents would be processed")
        
        logger.info(f"Would be uploaded to PikPak: {len(old_torrents)}")
        logger.info(f"Would be deleted from qBittorrent: {len(old_torrents)}")
        logger.info("")
        logger.info("Note: This was a dry-run. No actual transfers were performed.")
        logger.info("=" * 60)
        return
    
    successfully_transferred = []
    failed_transfers = []
    delete_failed_count = 0
    
    if batch_mode:
        logger.info("Using batch mode: uploading all torrents in one session")
        
        # Collect all magnet URIs for batch processing
        magnets_to_upload = [torrent['magnet_uri'] for torrent in old_torrents]
        torrent_by_magnet = {torrent['magnet_uri']: torrent for torrent in old_torrents}
        
        logger.info(f"Starting batch upload of {len(magnets_to_upload)} torrents to PikPak...")
        
        try:
            # Batch upload all magnets to PikPak
            success_magnets, failed_magnets = asyncio.run(
                process_pikpak_batch(magnets_to_upload, PIKPAK_EMAIL, PIKPAK_PASSWORD, delay_between_requests=PIKPAK_REQUEST_DELAY)
            )
            
            # Process successful uploads
            for magnet in success_magnets:
                torrent = torrent_by_magnet[magnet]
                logger.info(f"Successfully uploaded to PikPak: {torrent['name']}")

                # Delete from every QB instance that still holds this hash
                # (primary + adhoc can both have the same hash).
                target_clients = torrent_qb_map.get(torrent['hash']) or {qb}
                delete_errors = []
                for client in target_clients:
                    try:
                        client.delete_torrents([torrent['hash']], delete_files=True)
                    except Exception as delete_error:
                        delete_errors.append(delete_error)
                        logger.error(
                            f"Failed to delete from one qBittorrent instance "
                            f"after successful PikPak upload: {torrent['name']}, "
                            f"Error: {delete_error}"
                        )
                if not delete_errors:
                    logger.info(f"Successfully deleted from qBittorrent: {torrent['name']}")
                    save_to_pikpak_history(torrent, 'success')
                    successfully_transferred.append(torrent)
                else:
                    combined = "; ".join(str(e) for e in delete_errors)
                    save_to_pikpak_history(torrent, 'failed_but_deleted', combined)
                    delete_failed_count += 1
                    failed_transfers.append((torrent, f"qB delete failed: {combined}"))
            
            # Process failed uploads
            for magnet, error_msg in failed_magnets:
                torrent = torrent_by_magnet[magnet]
                logger.error(f"Failed to upload to PikPak: {torrent['name']}, Error: {error_msg}")
                save_to_pikpak_history(torrent, 'failed', error_msg)
                failed_transfers.append((torrent, error_msg))
                
        except Exception as e:
            logger.error(f"Unexpected error during batch processing: {e}")
            # If batch processing fails completely, mark all as failed
            failed_transfers = [(torrent, str(e)) for torrent in old_torrents]
            successfully_transferred = []
            for torrent in old_torrents:
                save_to_pikpak_history(torrent, 'failed', str(e))
    else:
        logger.info("Using individual mode: processing each torrent separately")
        
        # Process each torrent individually (original logic)
        for torrent in old_torrents:
            logger.info(f"Processing torrent: {torrent['name']}")
            
            try:
                # Try to upload to PikPak first (with configurable delay between requests)
                success_magnets, failed_magnets = asyncio.run(
                    process_pikpak_single(torrent['magnet_uri'], PIKPAK_EMAIL, PIKPAK_PASSWORD)
                )
                
                if success_magnets:  # Upload successful
                    logger.info(f"Successfully uploaded to PikPak: {torrent['name']}")

                    # Delete from every QB instance that still holds this hash.
                    target_clients = torrent_qb_map.get(torrent['hash']) or {qb}
                    delete_errors = []
                    for client in target_clients:
                        try:
                            client.delete_torrents([torrent['hash']], delete_files=True)
                        except Exception as delete_error:
                            delete_errors.append(delete_error)
                            logger.error(
                                f"Failed to delete from one qBittorrent instance "
                                f"after successful PikPak upload: {torrent['name']}, "
                                f"Error: {delete_error}"
                            )
                    if not delete_errors:
                        logger.info(f"Successfully deleted from qBittorrent: {torrent['name']}")
                        save_to_pikpak_history(torrent, 'success')
                        successfully_transferred.append(torrent)
                    else:
                        combined = "; ".join(str(e) for e in delete_errors)
                        save_to_pikpak_history(torrent, 'failed_but_deleted', combined)
                        delete_failed_count += 1
                        failed_transfers.append((torrent, f"qB delete failed: {combined}"))
                        
                else:  # Upload failed
                    error_msg = failed_magnets[0][1] if failed_magnets else "Unknown error"
                    logger.error(f"Failed to upload to PikPak: {torrent['name']}, Error: {error_msg}")
                    save_to_pikpak_history(torrent, 'failed', error_msg)
                    failed_transfers.append((torrent, error_msg))
                    
            except Exception as e:
                logger.error(f"Unexpected error processing torrent {torrent['name']}: {e}")
                save_to_pikpak_history(torrent, 'failed', str(e))
                failed_transfers.append((torrent, str(e)))
            
            # Add a small delay between processing different torrents to be respectful
            if torrent != old_torrents[-1]:  # Don't sleep after the last torrent
                logger.debug(f"Waiting {PIKPAK_REQUEST_DELAY} seconds before processing next torrent...")
                time.sleep(PIKPAK_REQUEST_DELAY)
    
    # Detailed Summary
    total_processed = len(old_torrents)
    successful_count = len(successfully_transferred)
    failed_count = len(failed_transfers)
    
    # Category breakdown
    category_stats = {}
    for torrent in old_torrents:
        category = torrent.get('category', 'Unknown')
        if category not in category_stats:
            category_stats[category] = {'total': 0, 'successful': 0, 'failed': 0}
        category_stats[category]['total'] += 1
        
        if torrent in successfully_transferred:
            category_stats[category]['successful'] += 1
        elif any(torrent == t[0] for t in failed_transfers):
            category_stats[category]['failed'] += 1
    
    logger.info("=" * 60)
    logger.info("PIKPAK BRIDGE TRANSFER SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Processed categories: {list(category_stats.keys())}")
    logger.info(f"Total torrents found (older than {days} days): {total_processed}")
    
    # Per-category stats
    for category, stats in category_stats.items():
        logger.info(f"Category '{category}': {stats['total']} total, {stats['successful']} successful, {stats['failed']} failed")
    
    logger.info(f"Overall: {successful_count} successful, {failed_count} failed")
    
    if successful_count > 0:
        logger.info(f"Success rate: {(successful_count/total_processed)*100:.1f}%")
    
    if successfully_transferred:
        logger.info("")
        logger.info("✓ Successfully transferred torrents:")
        for torrent in successfully_transferred:
            logger.info(f"  - {torrent['name']}")
    
    if failed_transfers:
        logger.info("")
        logger.warning("✗ Failed transfers:")
        for torrent, error in failed_transfers:
            logger.warning(f"  - {torrent['name']}: {error}")
    
    logger.info("")
    logger.info(f"PikPak transfer history saved to: {PIKPAK_HISTORY_FILE}")
    logger.info("=" * 60)

    if session_id and not dry_run:
        try:
            from packages.python.javdb_platform.config_helper import use_sqlite as _use_sqlite
            if _use_sqlite():
                from packages.python.javdb_platform.db import init_db, db_save_pikpak_stats
                init_db()
                db_save_pikpak_stats(session_id, {
                    'threshold_days': days,
                    'total_torrents': len(torrents),
                    'filtered_old': len(old_torrents),
                    'successful_count': successful_count,
                    'failed_count': failed_count,
                    'uploaded_count': successful_count + delete_failed_count,
                    'delete_failed_count': delete_failed_count,
                })
                logger.info(f"PikPak stats saved to SQLite (session_id={session_id})")
        except Exception as e:
            logger.warning(f"Failed to save pikpak stats to SQLite: {e}")

    # Git commit pikpak results (only if credentials are available)
    if not dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing PikPak bridge results...")
        # Flush log handlers to ensure all logs are written before commit
        flush_log_handlers()
        
        files_to_commit = [
            'logs/',
            REPORTS_DIR  # Contains pikpak_bridge_history.csv
        ]
        commit_message = f"Auto-commit: PikPak bridge results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        git_commit_and_push(
            files_to_add=files_to_commit,
            commit_message=commit_message,
            from_pipeline=from_pipeline,
            git_username=GIT_USERNAME,
            git_password=GIT_PASSWORD,
            git_repo_url=GIT_REPO_URL,
            git_branch=GIT_BRANCH
        )
    elif not dry_run:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")


def main():
    import atexit
    from packages.python.javdb_platform.db import close_db

    atexit.register(close_db)

    parser = argparse.ArgumentParser(description="PikPak Bridge - Transfer torrents from qBittorrent to PikPak")
    parser.add_argument("--days", type=int, default=3, help="Filter torrents older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Test mode: no delete or PikPak add")
    parser.add_argument("--individual", action="store_true", help="Process torrents individually instead of batch mode (default: batch mode)")
    add_proxy_arguments(
        parser,
        use_help='Force-enable proxy for PikPak and qBittorrent requests in this command',
        no_help='Force-disable proxy for PikPak and qBittorrent requests in this command',
    )
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--session-id", type=int, default=None, help="Report session ID for saving pikpak stats to SQLite")
    args = parser.parse_args()

    # Default to batch mode unless --individual is specified
    batch_mode = not args.individual
    proxy_override = resolve_proxy_override(args.use_proxy, args.no_proxy)
    
    pikpak_bridge(args.days, args.dry_run, batch_mode, proxy_override, args.from_pipeline,
                  session_id=args.session_id)


if __name__ == "__main__":
    main()
