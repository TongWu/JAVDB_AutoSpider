#!/usr/bin/env python3
"""
Ad hoc script to clean up existing history CSV file by applying priority rules:
- If hacked_subtitle has content, clear hacked_no_subtitle
- If subtitle has content, clear no_subtitle
"""

import csv
import os
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def cleanup_history_priorities(history_file):
    """Clean up history CSV by applying priority rules"""
    
    if not os.path.exists(history_file):
        logger.error(f"History file not found: {history_file}")
        return False
    
    # Create backup
    backup_file = f"{history_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
        
        # Apply cleanup rules
        cleaned_count = 0
        for record in records:
            original_record = record.copy()
            
            # Rule 1: If hacked_subtitle has content, clear hacked_no_subtitle
            if record.get('hacked_subtitle', '').strip():
                if record.get('hacked_no_subtitle', '').strip():
                    logger.debug(f"Clearing hacked_no_subtitle for {record.get('video_code', 'unknown')}")
                    record['hacked_no_subtitle'] = ''
                    cleaned_count += 1
            
            # Rule 2: If subtitle has content, clear no_subtitle
            if record.get('subtitle', '').strip():
                if record.get('no_subtitle', '').strip():
                    logger.debug(f"Clearing no_subtitle for {record.get('video_code', 'unknown')}")
                    record['no_subtitle'] = ''
                    cleaned_count += 1
        
        # Write cleaned records back
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        
        logger.info(f"Cleanup completed. Processed {len(records)} records, cleaned {cleaned_count} conflicts")
        return True
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        return False

def main():
    """Main function"""
    history_file = "Daily Report/parsed_movies_history.csv"
    
    logger.info("Starting history CSV cleanup...")
    logger.info(f"Target file: {history_file}")
    
    if cleanup_history_priorities(history_file):
        logger.info("Cleanup completed successfully!")
    else:
        logger.error("Cleanup failed!")

if __name__ == "__main__":
    main() 