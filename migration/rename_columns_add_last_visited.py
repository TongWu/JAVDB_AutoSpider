#!/usr/bin/env python3
"""
Migration script: rename date columns and add last_visited_datetime.

Changes:
  - create_date   -> create_datetime
  - update_date   -> update_datetime
  - Adds new column: last_visited_datetime (after update_datetime)
  - Sets last_visited_datetime = update_datetime for all existing rows
"""

import csv
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'reports', 'parsed_movies_history.csv')

OLD_FIELDNAMES = [
    'href', 'phase', 'video_code', 'create_date', 'update_date',
    'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
]

NEW_FIELDNAMES = [
    'href', 'phase', 'video_code', 'create_datetime', 'update_datetime',
    'last_visited_datetime',
    'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
]

RENAME_MAP = {
    'create_date': 'create_datetime',
    'update_date': 'update_datetime',
}


def migrate():
    if not os.path.exists(HISTORY_FILE):
        logger.error(f"History file not found: {HISTORY_FILE}")
        return False

    with open(HISTORY_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        records = list(reader)

    if 'create_datetime' in headers:
        logger.info("Already migrated (create_datetime column exists), skipping")
        return True

    if 'create_date' not in headers:
        logger.error(f"Unexpected headers: {headers}")
        return False

    logger.info(f"Migrating {len(records)} records ...")
    logger.info(f"  Old headers: {headers}")
    logger.info(f"  New headers: {NEW_FIELDNAMES}")

    migrated = []
    for row in records:
        new_row = {}
        for old_key, value in row.items():
            new_key = RENAME_MAP.get(old_key, old_key)
            new_row[new_key] = value

        new_row.setdefault('last_visited_datetime',
                           new_row.get('update_datetime', ''))
        migrated.append(new_row)

    bom = b'\xef\xbb\xbf'
    with open(HISTORY_FILE, 'wb') as f:
        f.write(bom)

    with open(HISTORY_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=NEW_FIELDNAMES)
        writer.writeheader()
        for row in migrated:
            writer.writerow(row)

    logger.info(f"Migration complete — {len(migrated)} records written")
    return True


if __name__ == '__main__':
    migrate()
