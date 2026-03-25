#!/usr/bin/env python3
"""Restore only MovieHistory.SupportingActors from a CSV dump.

Expects the same column order as ``SELECT * FROM MovieHistory`` (no header row):
Id, VideoCode, Href, ActorName, ActorGender, ActorLink, SupportingActors, ...

Rows are matched on ``Href`` (unique).

Usage:
  python packages/python/javdb_migrations/tools/restore_moviehistory_supporting_actors_from_csv.py \\
      --csv MovieHistory.csv --dry-run
  python packages/python/javdb_migrations/tools/restore_moviehistory_supporting_actors_from_csv.py \\
      --csv MovieHistory.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.api.parsers.common import (  # noqa: E402
    movie_href_lookup_values,
    absolutize_supporting_actors_json,
)
from packages.python.javdb_platform.config_helper import cfg  # noqa: E402
from packages.python.javdb_platform.db import HISTORY_DB_PATH  # noqa: E402

_IDX_HREF = 2
_IDX_SUPPORTING = 6


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--csv",
        required=True,
        help="Path to MovieHistory.csv (no header, full-table column order)",
    )
    p.add_argument(
        "--db",
        default=HISTORY_DB_PATH,
        help=f"history.db path (default: {HISTORY_DB_PATH})",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Count updates only, no writes")
    g.add_argument("--apply", action="store_true", help="Commit UPDATEs")
    args = p.parse_args()

    csv_path = os.path.abspath(args.csv)
    db_path = os.path.abspath(args.db)
    if not os.path.isfile(csv_path):
        print(f"error: CSV not found: {csv_path}", file=sys.stderr)
        return 1
    if not os.path.isfile(db_path):
        print(f"error: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        base_url = cfg('BASE_URL', 'https://javdb.com')
        updated = 0
        missing_href = 0
        skipped_short = 0

        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) <= _IDX_SUPPORTING:
                    skipped_short += 1
                    continue
                href = row[_IDX_HREF]
                supporting = absolutize_supporting_actors_json(row[_IDX_SUPPORTING], base_url)
                path_href, abs_href = movie_href_lookup_values(href, base_url)
                if path_href and abs_href:
                    params = (supporting, path_href, abs_href)
                    sql = "UPDATE MovieHistory SET SupportingActors = ? WHERE Href IN (?, ?)"
                else:
                    lookup = path_href or abs_href or href
                    params = (supporting, lookup)
                    sql = "UPDATE MovieHistory SET SupportingActors = ? WHERE Href = ?"
                cur.execute(
                    sql,
                    params,
                )
                if cur.rowcount:
                    updated += cur.rowcount
                else:
                    missing_href += 1

        if args.apply:
            conn.commit()
            mode = "committed"
        else:
            conn.rollback()
            mode = "dry-run (rolled back)"

        print(
            f"{mode}: updated={updated} csv_rows_no_matching_href={missing_href} "
            f"skipped_too_few_columns={skipped_short}"
        )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
