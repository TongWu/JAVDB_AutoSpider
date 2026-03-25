#!/usr/bin/env python3
"""One-time script: normalize DB URL columns to absolute BASE_URL values.

Targets:
  - history.db / MovieHistory: Href, ActorLink, SupportingActors(JSON link/href)
  - optional reports.db / ReportMovies: Href
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

from api.parsers.common import (  # noqa: E402
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from utils.infra.config_helper import cfg  # noqa: E402
from utils.infra.db import HISTORY_DB_PATH, REPORTS_DB_PATH  # noqa: E402


@dataclass
class RunStats:
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0


def _process_history_db(conn: sqlite3.Connection, base_url: str, dry_run: bool) -> RunStats:
    stats = RunStats()
    cur = conn.execute(
        "SELECT Id, Href, ActorLink, SupportingActors FROM MovieHistory ORDER BY Id"
    )
    rows = cur.fetchall()
    stats.scanned = len(rows)
    for row_id, href, actor_link, supporting in rows:
        old_href = href or ''
        old_actor = actor_link or ''
        old_supporting = supporting or ''

        new_href = javdb_absolute_url(old_href, base_url) if old_href else old_href
        new_actor = javdb_absolute_url(old_actor, base_url) if old_actor else old_actor
        new_supporting = (
            absolutize_supporting_actors_json(old_supporting, base_url)
            if old_supporting
            else old_supporting
        )

        if (
            new_href == old_href
            and new_actor == old_actor
            and new_supporting == old_supporting
        ):
            stats.unchanged += 1
            continue

        if dry_run:
            stats.updated += 1
            continue

        try:
            conn.execute(
                """
                UPDATE MovieHistory
                SET Href = ?, ActorLink = ?, SupportingActors = ?
                WHERE Id = ?
                """,
                (new_href, new_actor, new_supporting, row_id),
            )
            stats.updated += 1
        except sqlite3.IntegrityError:
            # Skip rows that would violate unique Href after normalization.
            stats.conflicts += 1
    return stats


def _process_reports_db(conn: sqlite3.Connection, base_url: str, dry_run: bool) -> RunStats:
    stats = RunStats()
    cur = conn.execute("SELECT Id, Href FROM ReportMovies ORDER BY Id")
    rows = cur.fetchall()
    stats.scanned = len(rows)
    for row_id, href in rows:
        old_href = href or ''
        new_href = javdb_absolute_url(old_href, base_url) if old_href else old_href
        if new_href == old_href:
            stats.unchanged += 1
            continue
        if dry_run:
            stats.updated += 1
            continue
        conn.execute("UPDATE ReportMovies SET Href = ? WHERE Id = ?", (new_href, row_id))
        stats.updated += 1
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--history-db", default=HISTORY_DB_PATH, help=f"Path to history.db (default: {HISTORY_DB_PATH})")
    p.add_argument("--base-url", default=cfg('BASE_URL', 'https://javdb.com'), help="Base site URL used for absolute links")
    p.add_argument("--also-reports-db", action="store_true", help="Also normalize ReportMovies.Href in reports.db")
    p.add_argument("--reports-db", default=REPORTS_DB_PATH, help=f"Path to reports.db (default: {REPORTS_DB_PATH})")
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--apply", action="store_true", help="Apply updates and commit")
    mode.add_argument("--dry-run", action="store_true", help="Show counts only, no writes (default)")
    args = p.parse_args()

    history_db = os.path.abspath(args.history_db)
    reports_db = os.path.abspath(args.reports_db)
    base_url = args.base_url.strip() or cfg('BASE_URL', 'https://javdb.com')
    dry_run = not args.apply

    if not os.path.isfile(history_db):
        print(f"error: history db not found: {history_db}", file=sys.stderr)
        return 1

    h_conn = sqlite3.connect(history_db)
    try:
        h_stats = _process_history_db(h_conn, base_url, dry_run)
        if dry_run:
            h_conn.rollback()
            mode_text = "dry-run"
        else:
            h_conn.commit()
            mode_text = "applied"
    finally:
        h_conn.close()

    print(
        f"[{mode_text}] history MovieHistory: scanned={h_stats.scanned} "
        f"updated={h_stats.updated} unchanged={h_stats.unchanged} conflicts={h_stats.conflicts}"
    )

    if args.also_reports_db:
        if not os.path.isfile(reports_db):
            print(f"error: reports db not found: {reports_db}", file=sys.stderr)
            return 1
        r_conn = sqlite3.connect(reports_db)
        try:
            r_stats = _process_reports_db(r_conn, base_url, dry_run)
            if dry_run:
                r_conn.rollback()
            else:
                r_conn.commit()
        finally:
            r_conn.close()
        print(
            f"[{mode_text}] reports ReportMovies: scanned={r_stats.scanned} "
            f"updated={r_stats.updated} unchanged={r_stats.unchanged}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
