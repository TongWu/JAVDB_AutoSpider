#!/usr/bin/env python3
"""Backfill TorrentHistory.Size from ReportTorrents.Size (cross-database join).

TorrentHistory (history.db) may have empty Size values because size was not
always captured during earlier spider runs.  ReportTorrents (reports.db)
stores the size for every torrent that appeared in a report.  This script
fills empty TorrentHistory.Size by joining against ReportTorrents using
Href + SubtitleIndicator + CensorIndicator, taking the most recent
non-empty size value.

Usage:
    python3 migration/tools/backfill_torrent_size.py [--history-db PATH] [--reports-db PATH] [--dry-run]
"""

import argparse
import os
import sqlite3
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

DEFAULT_HISTORY_DB = os.path.join('reports', 'history.db')
DEFAULT_REPORTS_DB = os.path.join('reports', 'reports.db')


def backfill_torrent_sizes(
    history_db: str,
    reports_db: str,
    *,
    dry_run: bool = False,
) -> int:
    """Backfill empty TorrentHistory.Size from ReportTorrents.Size.

    Uses SQLite ATTACH to join across history.db and reports.db.

    Returns:
        Number of rows updated.
    """
    if not os.path.exists(history_db):
        logger.error(f"History database not found: {history_db}")
        return 0
    if not os.path.exists(reports_db):
        logger.error(f"Reports database not found: {reports_db}")
        return 0

    conn = sqlite3.connect(history_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("ATTACH DATABASE ? AS rpt", (reports_db,))

    # Count how many TorrentHistory rows have empty Size
    empty_count = conn.execute(
        "SELECT COUNT(*) FROM TorrentHistory WHERE Size IS NULL OR Size = ''"
    ).fetchone()[0]
    total_count = conn.execute("SELECT COUNT(*) FROM TorrentHistory").fetchone()[0]
    logger.info(f"TorrentHistory: {empty_count}/{total_count} rows have empty Size")

    if empty_count == 0:
        logger.info("Nothing to backfill.")
        conn.execute("DETACH DATABASE rpt")
        conn.close()
        return 0

    # Preview matches before updating
    preview = conn.execute("""
        SELECT
            mh.VideoCode,
            mh.Href,
            th.SubtitleIndicator,
            th.CensorIndicator,
            th.Size AS OldSize,
            (
                SELECT rt.Size
                FROM rpt.ReportTorrents rt
                JOIN rpt.ReportMovies rm ON rt.ReportMovieId = rm.Id
                WHERE rm.Href = mh.Href
                  AND rt.SubtitleIndicator = th.SubtitleIndicator
                  AND rt.CensorIndicator = th.CensorIndicator
                  AND rt.Size IS NOT NULL AND rt.Size != ''
                ORDER BY rt.Id DESC
                LIMIT 1
            ) AS NewSize
        FROM TorrentHistory th
        JOIN MovieHistory mh ON th.MovieHistoryId = mh.Id
        WHERE (th.Size IS NULL OR th.Size = '')
    """).fetchall()

    matchable = [r for r in preview if r['NewSize']]
    logger.info(f"Found {len(matchable)} empty-Size rows with matching ReportTorrents data")

    if not matchable:
        logger.info("No matching ReportTorrents data found for backfill.")
        conn.execute("DETACH DATABASE rpt")
        conn.close()
        return 0

    for r in matchable[:10]:
        logger.info(
            f"  {r['VideoCode']} (sub={r['SubtitleIndicator']}, "
            f"cen={r['CensorIndicator']}): '' -> {r['NewSize']}"
        )
    if len(matchable) > 10:
        logger.info(f"  ... and {len(matchable) - 10} more")

    if dry_run:
        logger.info("[DRY RUN] No changes made.")
        conn.execute("DETACH DATABASE rpt")
        conn.close()
        return 0

    # Perform the actual update
    cur = conn.execute("""
        UPDATE TorrentHistory
        SET Size = (
            SELECT rt.Size
            FROM rpt.ReportTorrents rt
            JOIN rpt.ReportMovies rm ON rt.ReportMovieId = rm.Id
            JOIN MovieHistory mh ON rm.Href = mh.Href
            WHERE mh.Id = TorrentHistory.MovieHistoryId
              AND rt.SubtitleIndicator = TorrentHistory.SubtitleIndicator
              AND rt.CensorIndicator = TorrentHistory.CensorIndicator
              AND rt.Size IS NOT NULL AND rt.Size != ''
            ORDER BY rt.Id DESC
            LIMIT 1
        )
        WHERE (TorrentHistory.Size IS NULL OR TorrentHistory.Size = '')
          AND EXISTS (
            SELECT 1
            FROM rpt.ReportTorrents rt
            JOIN rpt.ReportMovies rm ON rt.ReportMovieId = rm.Id
            JOIN MovieHistory mh ON rm.Href = mh.Href
            WHERE mh.Id = TorrentHistory.MovieHistoryId
              AND rt.SubtitleIndicator = TorrentHistory.SubtitleIndicator
              AND rt.CensorIndicator = TorrentHistory.CensorIndicator
              AND rt.Size IS NOT NULL AND rt.Size != ''
          )
    """)
    updated = cur.rowcount
    conn.commit()

    logger.info(f"Updated {updated} TorrentHistory rows with Size from ReportTorrents")

    # Verify
    remaining = conn.execute(
        "SELECT COUNT(*) FROM TorrentHistory WHERE Size IS NULL OR Size = ''"
    ).fetchone()[0]
    logger.info(f"Remaining empty Size rows: {remaining}/{total_count}")

    conn.execute("DETACH DATABASE rpt")
    conn.close()
    return updated


def main():
    parser = argparse.ArgumentParser(
        description='Backfill TorrentHistory.Size from ReportTorrents.Size')
    parser.add_argument('--history-db', default=None,
                        help=f'Path to history.db (default: {DEFAULT_HISTORY_DB})')
    parser.add_argument('--reports-db', default=None,
                        help=f'Path to reports.db (default: {DEFAULT_REPORTS_DB})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without writing')
    args = parser.parse_args()

    history_db = args.history_db or DEFAULT_HISTORY_DB
    reports_db = args.reports_db or DEFAULT_REPORTS_DB

    logger.info("=" * 60)
    logger.info("BACKFILL: TorrentHistory.Size from ReportTorrents.Size")
    logger.info(f"History DB: {history_db}")
    logger.info(f"Reports DB: {reports_db}")
    if args.dry_run:
        logger.info("Mode: DRY RUN")
    logger.info("=" * 60)

    updated = backfill_torrent_sizes(
        history_db, reports_db, dry_run=args.dry_run,
    )

    if updated > 0:
        logger.info(f"Done. {updated} rows backfilled.")
    else:
        logger.info("Done. No rows needed backfilling.")


if __name__ == '__main__':
    main()
