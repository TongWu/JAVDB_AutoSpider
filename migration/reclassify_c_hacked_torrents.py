#!/usr/bin/env python3
"""
Ad hoc script to reclassify -C.无码破解 torrents in history CSV from no_subtitle to hacked_subtitle
"""

import csv
import os
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def reclassify_c_hacked_torrents(history_file):
    """Reclassify -C.无码破解 torrents from no_subtitle to hacked_subtitle"""
    
    if not os.path.exists(history_file):
        logger.error(f"History file not found: {history_file}")
        return False
    
    # Create backup
    backup_file = f"{history_file}.backup_reclassify_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"Creating backup: {backup_file}")
    
    try:
        # Read all records
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        
        # Create backup
        with open(backup_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        
        logger.info(f"Backup created successfully with {len(records)} records")
        
        # Reclassify torrents
        reclassified_count = 0
        for record in records:
            # Check if no_subtitle contains -C.无码破解
            no_subtitle_content = record.get('no_subtitle', '').strip()
            if no_subtitle_content and '-C.无码破解' in no_subtitle_content:
                # Move from no_subtitle to hacked_subtitle
                record['hacked_subtitle'] = no_subtitle_content
                record['no_subtitle'] = ''
                reclassified_count += 1
                logger.info(f"Reclassified {record.get('video_code', 'unknown')}: moved -C.无码破解 from no_subtitle to hacked_subtitle")
        
        # Write updated records back
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        
        logger.info(f"Reclassification completed. Processed {len(records)} records, reclassified {reclassified_count} torrents")
        return True
        
    except Exception as e:
        logger.error(f"Error during reclassification: {e}")
        return False

def main():
    """Main function"""
    history_file = "Daily Report/parsed_movies_history.csv"
    
    logger.info("Starting -C.无码破解 torrent reclassification...")
    logger.info(f"Target file: {history_file}")
    
    if reclassify_c_hacked_torrents(history_file):
        logger.info("Reclassification completed successfully!")
    else:
        logger.error("Reclassification failed!")

if __name__ == "__main__":
    main() 