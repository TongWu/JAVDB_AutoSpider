#!/usr/bin/env python3
"""Restore only MovieHistory.SupportingActors from a CSV dump.

Expects the same column order as ``SELECT * FROM MovieHistory`` (no header row):
Id, VideoCode, Href, ActorName, ActorGender, ActorLink, SupportingActors, ...

Rows are matched on ``Href`` (unique).

Usage:
  python migration/tools/restore_moviehistory_supporting_actors_from_csv.py \\
      --csv MovieHistory.csv --dry-run
  python migration/tools/restore_moviehistory_supporting_actors_from_csv.py \\
      --csv MovieHistory.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.db import HISTORY_DB_PATH  # noqa: E402

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
        updated = 0
        missing_href = 0
        skipped_short = 0

        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) <= _IDX_SUPPORTING:
                    skipped_short += 1
                    continue
                href = row[_IDX_HREF]
                supporting = row[_IDX_SUPPORTING]
                cur.execute(
                    "UPDATE MovieHistory SET SupportingActors = ? WHERE Href = ?",
                    (supporting, href),
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
