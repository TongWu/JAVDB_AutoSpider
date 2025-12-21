import asyncio
import argparse
import csv
import os
import time
import sys
from datetime import datetime, timedelta
import requests
from pikpakapi import PikPakApi

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from config import QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD, PIKPAK_EMAIL, PIKPAK_PASSWORD, PIKPAK_LOG_FILE, DAILY_REPORT_DIR, TORRENT_CATEGORY, TORRENT_CATEGORY_ADHOC

# Import GIT configuration with fallback
try:
    from config import GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH
except ImportError:
    GIT_USERNAME = 'github-actions'
    GIT_PASSWORD = ''
    GIT_REPO_URL = ''
    GIT_BRANCH = 'main'

# Import PikPak delay configuration with fallback
try:
    from config import PIKPAK_REQUEST_DELAY
except ImportError:
    PIKPAK_REQUEST_DELAY = 3  # Default 3 seconds if not configured

# Import proxy configuration with fallback
try:
    from config import PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES
except ImportError:
    PROXY_HTTP = None
    PROXY_HTTPS = None
    PROXY_MODULES = ['all']

# Import proxy pool configuration (with fallback)
try:
    from config import PROXY_MODE, PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES
except ImportError:
    PROXY_MODE = 'single'
    PROXY_POOL = []
    PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 days (691200 seconds)
    PROXY_POOL_MAX_FAILURES = 3

from utils.logging_config import setup_logging, get_logger
from utils.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials

# --------------------------
# Setup Logging
# --------------------------
setup_logging(log_file=PIKPAK_LOG_FILE)
logger = get_logger(__name__)

# Import proxy pool
from utils.proxy_pool import ProxyPool, create_proxy_pool_from_config

# Import proxy helper from request handler
from utils.request_handler import ProxyHelper, create_proxy_helper_from_config

# Global proxy pool instance
global_proxy_pool = None

# Global proxy helper instance
global_proxy_helper = None

# Categories to process (from config)
CATEGORIES = [TORRENT_CATEGORY, TORRENT_CATEGORY_ADHOC]
PIKPAK_HISTORY_FILE = os.path.join(DAILY_REPORT_DIR, "pikpak_bridge_history.csv")


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


def initialize_proxy_helper(use_proxy):
    """Initialize global proxy pool and proxy helper."""
    global global_proxy_pool, global_proxy_helper
    
    if not use_proxy:
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
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Proxy pool initialized successfully")
        elif PROXY_MODE == 'single':
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
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
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
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
class QBittorrentClient:
    def __init__(self, base_url, username, password, use_proxy=False):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.use_proxy = use_proxy
        self.proxies = get_proxies_dict('pikpak', use_proxy)
        self.login(username, password)

    def login(self, username, password):
        resp = self.session.post(f"{self.base_url}/api/v2/auth/login", data={
            'username': username,
            'password': password
        }, proxies=self.proxies)
        if resp.status_code != 200 or resp.text != 'Ok.':
            logger.error(f"qBittorrent login failed: {resp.text}")
            raise Exception(f"Failed to login qBittorrent: {resp.text}")
        logger.info("Logged into qBittorrent successfully.")

    def get_torrents(self, category):
        resp = self.session.get(f"{self.base_url}/api/v2/torrents/info",
                                params={"category": category, "filter": "downloading"},
                                proxies=self.proxies)
        resp.raise_for_status()
        return resp.json()
    
    def get_torrents_multiple_categories(self, categories):
        """Get torrents from multiple categories"""
        all_torrents = []
        for category in categories:
            try:
                torrents = self.get_torrents(category)
                logger.debug(f"Found {len(torrents)} torrents in category '{category}'")
                all_torrents.extend(torrents)
            except Exception as e:
                logger.warning(f"Failed to get torrents from category '{category}': {e}")
        return all_torrents

    def delete_torrents(self, hashes, delete_files=True):
        resp = self.session.post(f"{self.base_url}/api/v2/torrents/delete", data={
            'hashes': '|'.join(hashes),
            'deleteFiles': 'true' if delete_files else 'false'
        }, proxies=self.proxies)
        resp.raise_for_status()
        logger.info(f"Deleted {len(hashes)} torrents from qBittorrent.")
        return True


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
    
    logger.info(f"Starting batch upload of {len(magnets)} torrents to PikPak...")
    
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
    """Save torrent transfer information to PikPak history CSV"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(PIKPAK_HISTORY_FILE), exist_ok=True)
    
    # Check if file exists and create header if needed
    file_exists = os.path.exists(PIKPAK_HISTORY_FILE)
    
    with open(PIKPAK_HISTORY_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        fieldnames = [
            'torrent_hash', 'torrent_name', 'category', 'magnet_uri', 'added_to_qb_date', 
            'deleted_from_qb_date', 'uploaded_to_pikpak_date', 'transfer_status', 'error_message'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        record = {
            'torrent_hash': torrent_info['hash'],
            'torrent_name': torrent_info['name'],
            'category': torrent_info.get('category', 'Unknown'),
            'magnet_uri': torrent_info['magnet_uri'],
            'added_to_qb_date': datetime.fromtimestamp(torrent_info['added_on']).strftime("%Y-%m-%d %H:%M:%S"),
            'deleted_from_qb_date': current_time if transfer_status in ['success', 'failed_but_deleted'] else '',
            'uploaded_to_pikpak_date': current_time if transfer_status == 'success' else '',
            'transfer_status': transfer_status,  # 'success', 'failed', 'failed_but_deleted'
            'error_message': error_msg or ''
        }
        
        writer.writerow(record)
        logger.info(f"Saved to PikPak history: {torrent_info['name']} - {transfer_status}")


# --------------------------
# Main Logic
# --------------------------
def pikpak_bridge(days, dry_run, batch_mode=True, use_proxy=False, from_pipeline=False):
    cutoff_date = (datetime.now() - timedelta(days=days)).date()
    logger.info(f"Processing torrents older than {days} days (before {cutoff_date})")
    
    # Initialize proxy helper
    initialize_proxy_helper(use_proxy)
    
    if use_proxy:
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

    qb = QBittorrentClient(f"http://{QB_HOST}:{QB_PORT}", QB_USERNAME, QB_PASSWORD, use_proxy)
    torrents = qb.get_torrents_multiple_categories(CATEGORIES)
    logger.info(f"Found {len(torrents)} torrents across categories {CATEGORIES}")

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
                save_to_pikpak_history(torrent, 'success')
                
                # Now safe to delete from qBittorrent
                try:
                    qb.delete_torrents([torrent['hash']], delete_files=True)
                    logger.info(f"Successfully deleted from qBittorrent: {torrent['name']}")
                    successfully_transferred.append(torrent)
                except Exception as delete_error:
                    logger.error(f"Failed to delete from qBittorrent after successful PikPak upload: {torrent['name']}, Error: {delete_error}")
                    save_to_pikpak_history(torrent, 'failed_but_deleted', str(delete_error))
            
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
                    save_to_pikpak_history(torrent, 'success')
                    
                    # Now safe to delete from qBittorrent
                    try:
                        qb.delete_torrents([torrent['hash']], delete_files=True)
                        logger.info(f"Successfully deleted from qBittorrent: {torrent['name']}")
                        successfully_transferred.append(torrent)
                    except Exception as delete_error:
                        logger.error(f"Failed to delete from qBittorrent after successful PikPak upload: {torrent['name']}, Error: {delete_error}")
                        save_to_pikpak_history(torrent, 'failed_but_deleted', str(delete_error))
                        
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
    
    # Git commit pikpak results (only if credentials are available)
    if not dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing PikPak bridge results...")
        # Flush log handlers to ensure all logs are written before commit
        flush_log_handlers()
        
        files_to_commit = [
            'logs/',
            DAILY_REPORT_DIR  # Contains pikpak_bridge_history.csv
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PikPak Bridge - Transfer torrents from qBittorrent to PikPak")
    parser.add_argument("--days", type=int, default=3, help="Filter torrents older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Test mode: no delete or PikPak add")
    parser.add_argument("--individual", action="store_true", help="Process torrents individually instead of batch mode (default: batch mode)")
    parser.add_argument("--use-proxy", action="store_true", help="Enable proxy for qBittorrent API requests (proxy settings from config.py)")
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    args = parser.parse_args()

    # Default to batch mode unless --individual is specified
    batch_mode = not args.individual
    
    pikpak_bridge(args.days, args.dry_run, batch_mode, args.use_proxy, args.from_pipeline)

