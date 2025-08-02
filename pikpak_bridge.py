import asyncio
import argparse
import csv
import os
from datetime import datetime, timedelta
import requests
from pikpakapi import PikPakApi

from config import QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD, PIKPAK_EMAIL, PIKPAK_PASSWORD, PIKPAK_LOG_FILE, DAILY_REPORT_DIR
from utils.logging_config import setup_logging, get_logger

# --------------------------
# Setup Logging
# --------------------------
setup_logging(log_file=PIKPAK_LOG_FILE)
logger = get_logger(__name__)

CATEGORY = "JavDB"
PIKPAK_HISTORY_FILE = os.path.join(DAILY_REPORT_DIR, "pikpak_bridge_history.csv")


# --------------------------
# qBittorrent Client
# --------------------------
class QBittorrentClient:
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.login(username, password)

    def login(self, username, password):
        resp = self.session.post(f"{self.base_url}/api/v2/auth/login", data={
            'username': username,
            'password': password
        })
        if resp.status_code != 200 or resp.text != 'Ok.':
            logger.error(f"qBittorrent login failed: {resp.text}")
            raise Exception(f"Failed to login qBittorrent: {resp.text}")
        logger.info("Logged into qBittorrent successfully.")

    def get_torrents(self, category):
        resp = self.session.get(f"{self.base_url}/api/v2/torrents/info",
                                params={"category": category, "filter": "downloading"})
        resp.raise_for_status()
        return resp.json()

    def delete_torrents(self, hashes, delete_files=True):
        resp = self.session.post(f"{self.base_url}/api/v2/torrents/delete", data={
            'hashes': '|'.join(hashes),
            'deleteFiles': 'true' if delete_files else 'false'
        })
        resp.raise_for_status()
        logger.info(f"Deleted {len(hashes)} torrents from qBittorrent.")
        return True


# --------------------------
# PikPak API (using pikpakapi)
# --------------------------
async def process_pikpak(magnets, email, password):
    client = PikPakApi(username=email, password=password)
    await client.login()
    await client.refresh_access_token()

    tasks = []
    for magnet in magnets:
        tasks.append(client.offline_download(magnet))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success_magnets = []
    failed_magnets = []
    
    for magnet, result in zip(magnets, results):
        if isinstance(result, Exception):
            logger.error(f"Failed to add magnet: {magnet}, Error: {result}")
            failed_magnets.append((magnet, str(result)))
        else:
            logger.info(f"Added magnet to PikPak: {magnet}")
            success_magnets.append(magnet)
    
    return success_magnets, failed_magnets


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
            'torrent_hash', 'torrent_name', 'magnet_uri', 'added_to_qb_date', 
            'deleted_from_qb_date', 'uploaded_to_pikpak_date', 'transfer_status', 'error_message'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        record = {
            'torrent_hash': torrent_info['hash'],
            'torrent_name': torrent_info['name'],
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
def pikpak_bridge(days, dry_run):
    cutoff_date = (datetime.now() - timedelta(days=days)).date()
    logger.info(f"Processing torrents older than {days} days (before {cutoff_date})")

    qb = QBittorrentClient(f"http://{QB_HOST}:{QB_PORT}", QB_USERNAME, QB_PASSWORD)
    torrents = qb.get_torrents(CATEGORY)
    logger.info(f"Found {len(torrents)} torrents in category {CATEGORY}")

    old_torrents = [t for t in torrents if datetime.fromtimestamp(t['added_on']).date() <= cutoff_date]
    logger.info(f"Filtered {len(old_torrents)} torrents older than {days} days")

    if not old_torrents:
        logger.info("No torrents to process.")
        return

    if dry_run:
        logger.info(f"[Dry-Run] Would process {len(old_torrents)} torrents:")
        for torrent in old_torrents:
            logger.info(f"[Dry-Run] {torrent['name']} (added: {datetime.fromtimestamp(torrent['added_on']).strftime('%Y-%m-%d %H:%M:%S')})")
        
        # Dry-run summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("PIKPAK BRIDGE DRY-RUN SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total torrents found (older than {days} days): {len(old_torrents)}")
        logger.info(f"Would be uploaded to PikPak: {len(old_torrents)}")
        logger.info(f"Would be deleted from qBittorrent: {len(old_torrents)}")
        logger.info("")
        logger.info("Note: This was a dry-run. No actual transfers were performed.")
        logger.info("=" * 60)
        return
    
    # Process each torrent individually for better error handling and history tracking
    successfully_transferred = []
    failed_transfers = []
    
    for torrent in old_torrents:
        logger.info(f"Processing torrent: {torrent['name']}")
        
        try:
            # Try to upload to PikPak first
            success_magnets, failed_magnets = asyncio.run(
                process_pikpak([torrent['magnet_uri']], PIKPAK_EMAIL, PIKPAK_PASSWORD)
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
    
    # Detailed Summary
    total_processed = len(old_torrents)
    successful_count = len(successfully_transferred)
    failed_count = len(failed_transfers)
    
    logger.info("=" * 60)
    logger.info("PIKPAK BRIDGE TRANSFER SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total torrents found (older than {days} days): {total_processed}")
    logger.info(f"Successfully uploaded to PikPak: {successful_count}")
    logger.info(f"Successfully deleted from qBittorrent: {successful_count}")
    logger.info(f"Failed transfers: {failed_count}")
    
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3, help="Filter torrents older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Test mode: no delete or PikPak add")
    args = parser.parse_args()

    pikpak_bridge(args.days, args.dry_run)
