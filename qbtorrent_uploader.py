import csv
import requests
import logging
from datetime import datetime
import time
import os
import argparse
import sys
# Import unified configuration
try:
    from config import (
        QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD,
        TORRENT_CATEGORY, TORRENT_SAVE_PATH, AUTO_START, SKIP_CHECKING,
        REQUEST_TIMEOUT, DELAY_BETWEEN_ADDITIONS,
        UPLOADER_LOG_FILE, DAILY_REPORT_DIR, AD_HOC_DIR
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    QB_HOST = 'your_qbittorrent_ip'
    QB_PORT = 'your_qbittorrent_port'
    QB_USERNAME = 'your_qbittorrent_username'
    QB_PASSWORD = 'your_qbittorrent_password'
    
    TORRENT_CATEGORY = 'JavDB'
    TORRENT_SAVE_PATH = ''
    AUTO_START = True
    SKIP_CHECKING = False
    
    REQUEST_TIMEOUT = 30
    DELAY_BETWEEN_ADDITIONS = 1
    
    UPLOADER_LOG_FILE = 'logs/qbtorrent_uploader.log'
    DAILY_REPORT_DIR = 'Daily Report'
    AD_HOC_DIR = 'Ad Hoc'

os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(UPLOADER_LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# qBittorrent configuration
QB_BASE_URL = f'http://{QB_HOST}:{QB_PORT}'

def parse_arguments():
    parser = argparse.ArgumentParser(description='qBittorrent Uploader')
    parser.add_argument('--mode', choices=['adhoc', 'daily'], default='daily', help='Upload mode: adhoc (Ad Hoc folder) or daily (Daily Report folder)')
    return parser.parse_args()

def get_csv_filename(mode='daily'):
    """Get the CSV filename for current date and mode"""
    current_date = datetime.now().strftime("%Y%m%d")
    if mode == 'adhoc':
        folder = AD_HOC_DIR
    else:
        folder = DAILY_REPORT_DIR
    return os.path.join(folder, f'Javdb_TodayTitle_{current_date}.csv')

def test_qbittorrent_connection():
    """Test if qBittorrent is accessible"""
    try:
        logger.info(f"Testing connection to qBittorrent at {QB_BASE_URL}")
        response = requests.get(f'{QB_BASE_URL}/api/v2/app/version', timeout=10)
        if response.status_code == 200 or response.status_code == 403:
            logger.info("qBittorrent is accessible")
            return True
        else:
            logger.warning(f"qBittorrent responded with status code: {response.status_code}")
            return False
    except requests.RequestException as e:
        logger.error(f"Cannot connect to qBittorrent: {e}")
        return False

def login_to_qbittorrent(session):
    """Login to qBittorrent web UI"""
    login_url = f'{QB_BASE_URL}/api/v2/auth/login'
    login_data = {
        'username': QB_USERNAME,
        'password': QB_PASSWORD
    }
    
    try:
        logger.info(f"Attempting to login to qBittorrent at {QB_BASE_URL}")
        response = session.post(login_url, data=login_data, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 200:
            logger.info("Successfully logged in to qBittorrent")
            return True
        else:
            logger.error(f"Login failed with status code: {response.status_code}")
            logger.error("Please check your username and password in config.py")
            return False
            
    except requests.RequestException as e:
        logger.error(f"Login error: {e}")
        return False

def add_torrent_to_qbittorrent(session, magnet_link, title):
    """Add a torrent to qBittorrent"""
    add_url = f'{QB_BASE_URL}/api/v2/torrents/add'
    
    # Prepare the data for adding torrent
    torrent_data = {
        'urls': magnet_link,
        'name': title,
        'category': TORRENT_CATEGORY,
        'autoTMM': 'true',
        'savepath': TORRENT_SAVE_PATH,
        'downloadPath': '',
        'skip_checking': str(SKIP_CHECKING).lower(),
        'contentLayout': 'Original',
        'ratioLimit': '-2',
        'seedingTimeLimit': '-2',
        'addPaused': str(not AUTO_START).lower()
    }
    
    try:
        logger.debug(f"Adding torrent: {title}")
        response = session.post(add_url, data=torrent_data, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 200:
            logger.debug(f"Successfully added torrent: {title}")
            return True
        else:
            logger.error(f"Failed to add torrent '{title}' with status code: {response.status_code}")
            return False
            
    except requests.RequestException as e:
        logger.error(f"Error adding torrent '{title}': {e}")
        return False

def read_csv_file(filename):
    """Read the CSV file and extract all magnet links"""
    torrents = []
    
    if not os.path.exists(filename):
        logger.error(f"CSV file not found: {filename}")
        logger.info("Make sure you have run the spider script first to generate the CSV file")
        return torrents
    
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Extract magnet links from all four columns
                if row.get('hacked_subtitle') and row['hacked_subtitle'].strip():
                    torrents.append({
                        'magnet': row['hacked_subtitle'].strip(),
                        'title': f"{row['video_code']} [破解+字幕]",
                        'page': row.get('page', 'N/A'),
                        'type': 'hacked_subtitle'
                    })
                
                if row.get('hacked_no_subtitle') and row['hacked_no_subtitle'].strip():
                    torrents.append({
                        'magnet': row['hacked_no_subtitle'].strip(),
                        'title': f"{row['video_code']} [破解无字幕]",
                        'page': row.get('page', 'N/A'),
                        'type': 'hacked_no_subtitle'
                    })
                
                if row.get('subtitle') and row['subtitle'].strip():
                    torrents.append({
                        'magnet': row['subtitle'].strip(),
                        'title': f"{row['video_code']} [字幕]",
                        'page': row.get('page', 'N/A'),
                        'type': 'subtitle'
                    })
                
                if row.get('no_subtitle') and row['no_subtitle'].strip():
                    torrents.append({
                        'magnet': row['no_subtitle'].strip(),
                        'title': f"{row['video_code']} [无字幕]",
                        'page': row.get('page', 'N/A'),
                        'type': 'no_subtitle'
                    })
        
        logger.info(f"Found {len(torrents)} torrent links in {filename}")
        return torrents
        
    except Exception as e:
        logger.error(f"Error reading CSV file: {e}")
        return torrents

def main():
    args = parse_arguments()
    mode = args.mode
    logger.info("Starting qBittorrent uploader...")
    
    # Test qBittorrent connection first
    # if not test_qbittorrent_connection():
    #     logger.error("Cannot connect to qBittorrent. Please check:")
    #     logger.error("1. qBittorrent is running")
    #     logger.error("2. Web UI is enabled")
    #     logger.error("3. Host and port settings in config.py")
    #     return
    
    # Get CSV filename for current date
    csv_filename = get_csv_filename(mode)
    logger.info(f"Looking for CSV file: {csv_filename}")
    
    # Read torrent links from CSV
    torrents = read_csv_file(csv_filename)
    
    if not torrents:
        logger.warning("No torrent links found in CSV file")
        return
    
    # Create session for qBittorrent
    session = requests.Session()
    
    # Login to qBittorrent
    if not login_to_qbittorrent(session):
        logger.error("Failed to login to qBittorrent. Please check username and password.")
        return
    
    # Add torrents to qBittorrent
    hacked_subtitle_count = 0
    hacked_no_subtitle_count = 0
    subtitle_count = 0
    no_subtitle_count = 0
    failed_count = 0
    total_torrents = len(torrents)
    
    logger.info(f"Starting to add {total_torrents} torrents to qBittorrent...")
    
    for i, torrent in enumerate(torrents, 1):
        logger.info(f"[{i}/{total_torrents}] Adding: {torrent['title']}")
        
        success = add_torrent_to_qbittorrent(session, torrent['magnet'], torrent['title'])
        
        if success:
            if torrent['type'] == 'hacked_subtitle':
                hacked_subtitle_count += 1
            elif torrent['type'] == 'hacked_no_subtitle':
                hacked_no_subtitle_count += 1
            elif torrent['type'] == 'subtitle':
                subtitle_count += 1
            elif torrent['type'] == 'no_subtitle':
                no_subtitle_count += 1
        else:
            failed_count += 1
        
        # Small delay between additions
        time.sleep(DELAY_BETWEEN_ADDITIONS)
    
    # Generate summary
    logger.info("=" * 50)
    logger.info("UPLOAD SUMMARY")
    logger.info("=" * 50)
    logger.info(f"CSV file: {csv_filename}")
    logger.info(f"Total torrents found: {total_torrents}")
    logger.info(f"Successfully added: {hacked_subtitle_count + hacked_no_subtitle_count + subtitle_count + no_subtitle_count}")
    logger.info(f"  - Hacked subtitle torrents: {hacked_subtitle_count}")
    logger.info(f"  - Hacked no subtitle torrents: {hacked_no_subtitle_count}")
    logger.info(f"  - Subtitle torrents: {subtitle_count}")
    logger.info(f"  - No subtitle torrents: {no_subtitle_count}")
    logger.info(f"Failed to add: {failed_count}")
    logger.info(f"Success rate: {((hacked_subtitle_count + hacked_no_subtitle_count + subtitle_count + no_subtitle_count)/total_torrents*100):.1f}%" if total_torrents > 0 else "N/A")
    logger.info("=" * 50)

if __name__ == '__main__':
    main() 
