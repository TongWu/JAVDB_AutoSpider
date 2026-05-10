#!/usr/bin/env python3
"""One-shot tool: pull every business table from D1 down into local sqlite.

Usage::

    python -m scripts.sync_d1_to_sqlite              # dry-run report
    python -m scripts.sync_d1_to_sqlite --apply      # actually overwrite local DBs

Why this exists
---------------
On 2026-05-08 the local sqlite mirror drifted ~1k rows behind D1 because
some CI runs commit only the D1 side (no `git push reports/*.db`).  When
the planned cleanup of the 332/346 phantom audits runs, both sides need
to start from the same snapshot — otherwise pulling D1 into sqlite later
will quietly resurrect the very phantoms we just deleted.

What it does
------------
For each of ``javdb-history`` / ``javdb-reports`` / ``javdb-operations``,
walks the business tables (skipping ``_cf_KV``, ``sqlite_sequence``,
``SchemaVersion``), pages through ``SELECT *`` from D1 in chunks of
``--page-size`` rows, and replays them into local sqlite preserving
the original ``Id`` columns.  The local sqlite is wiped first
(``DELETE FROM <table>``) so dry-run row counts after sync exactly
match D1.

Safety
------
* Default mode is dry-run: prints a summary table of counts and
  diffs, and writes a JSON report to
  ``reports/sync_d1_to_sqlite_dryrun_<ts>.json``.
* ``--apply`` first copies the existing ``reports/*.db`` into
  ``reports/_backup_<ts>/`` so you can always roll back.
* Aborts if STORAGE_BACKEND is ``dual`` or ``d1`` while the script runs,
  to prevent live writes from racing the import.

Not for cron
------------
This is an incident-response tool.  Run it by hand, look at the output,
re-run with ``--apply``.  Never wire it into a workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

# Allow ``python scripts/sync_d1_to_sqlite.py`` from a fresh checkout.
if __package__ in (None, ""):
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_platform.d1_client import (
    D1Connection,
    get_d1_account_id,
    get_d1_api_token,
    get_d1_database_id,
)
from packages.python.javdb_platform import db as db_mod
from packages.python.javdb_platform.logging_config import (
    get_logger,
    log_group_end,
    log_group_start,
    setup_logging,
)


logger = get_logger("scripts.sync_d1_to_sqlite")

_DEFAULT_PAGE_SIZE = 500

# (logical_name, sqlite_path_cfg_key, sqlite_default)
_TARGETS = [
    ("history",
     "HISTORY_DB_PATH",
     os.path.join("reports", "history.db")),
    ("reports",
     "REPORTS_DB_PATH",
     os.path.join("reports", "reports.db")),
    ("operations",
     "OPERATIONS_DB_PATH",
     os.path.join("reports", "operations.db")),
]

_SKIP_TABLES = frozenset({"_cf_KV", "sqlite_sequence", "SchemaVersion"})


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.sync_d1_to_sqlite",
        description=(
            "Pull every business table from Cloudflare D1 down into the "
            "local sqlite mirror.  Default mode is dry-run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.set_defaults(dry_run=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="(default) Walk all tables on D1, report counts and diffs, "
             "write a JSON report under reports/, but DO NOT modify "
             "local sqlite.",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually overwrite local sqlite (after backing up to "
             "reports/_backup_<ts>/).",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=_DEFAULT_PAGE_SIZE,
        help="Rows per D1 page query (default 500).",
    )
    p.add_argument(
        "--logical-names",
        type=str,
        default=None,
        help="Comma-separated subset of logical DB names to sync. "
             "Defaults to all three (history,reports,operations).",
    )
    p.add_argument(
        "--report-path",
        type=str,
        default=None,
        help="Where to write the JSON report. Defaults to "
             "reports/sync_d1_to_sqlite_<dryrun|apply>_<ts>.json.",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return p.parse_args(argv)


def _refuse_when_dual_or_d1() -> None:
    """Hard-stop if a live dual / d1 backend is running concurrently.

    The sync writes directly into local sqlite via ``sqlite3.connect``,
    bypassing the ``DualConnection`` / ``D1Connection`` mirroring. If a
    spider / pipeline process is also writing through dual mode, the
    DELETE-then-INSERT cycle here would race their writes and leave
    sqlite missing rows that D1 has but the sync hasn't picked up yet.
    """
    backend = (
        os.environ.get("STORAGE_BACKEND")
        or cfg("STORAGE_BACKEND", "sqlite")
        or "sqlite"
    ).strip().lower()
    if backend in ("dual", "d1"):
        logger.error(
            "STORAGE_BACKEND=%s is set; pause live writes and re-run with "
            "STORAGE_BACKEND=sqlite (or unset). Exiting.",
            backend,
        )
        sys.exit(1)


def _list_business_tables(d1: D1Connection) -> List[str]:
    """Return ordered list of user tables on a D1 database."""
    cur = d1.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'd1_%' "
        "ORDER BY name"
    )
    rows = cur.fetchall() or []
    out: List[str] = []
    for r in rows:
        name = r["name"] if isinstance(r, dict) else r[0]
        if name and name not in _SKIP_TABLES:
            out.append(name)
    return out


def _table_columns(d1: D1Connection, table: str) -> List[str]:
    cur = d1.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall() or []
    cols: List[str] = []
    for r in rows:
        name = r["name"] if isinstance(r, dict) else r[1]
        if name:
            cols.append(name)
    return cols


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_count(d1: D1Connection, table: str) -> int:
    cur = d1.execute(f"SELECT COUNT(*) AS n FROM {_quote(table)}")
    row = cur.fetchone()
    if not row:
        return 0
    if isinstance(row, dict):
        return int(row.get("n") or 0)
    return int(row[0] or 0)


def _sqlite_table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_quote(table)}"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    return int(row[0] or 0)


def _ensure_local_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Bring a local mirror forward before inserting D1's current columns."""
    db_mod._ensure_rollback_columns(conn)
    conn.commit()


def _backup_sqlite(target_paths: List[str]) -> Optional[str]:
    """Copy each existing sqlite file under reports/_backup_<ts>/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    reports_dir = (
        os.environ.get("REPORTS_DIR")
        or cfg("REPORTS_DIR", "reports")
        or "reports"
    )
    backup_dir = os.path.join(reports_dir, f"_backup_{ts}")
    any_copied = False
    for path in target_paths:
        if not os.path.exists(path):
            continue
        os.makedirs(backup_dir, exist_ok=True)
        dest = os.path.join(backup_dir, os.path.basename(path))
        shutil.copy2(path, dest)
        logger.info("Backed up %s -> %s", path, dest)
        any_copied = True
    return backup_dir if any_copied else None


def _sync_one_table(
    d1: D1Connection,
    sqlite_conn: sqlite3.Connection,
    table: str,
    *,
    page_size: int,
    dry_run: bool,
) -> Dict[str, Any]:
    """Page through D1 and (optionally) overwrite the sqlite table."""
    columns = _table_columns(d1, table)
    if not columns:
        return {
            "table": table,
            "skipped": True,
            "reason": "no columns reported by D1",
        }
    quoted_cols = ", ".join(_quote(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)

    pre_d1 = _table_count(d1, table)
    pre_sqlite = _sqlite_table_count(sqlite_conn, table)

    if not dry_run:
        sqlite_conn.execute(f"DELETE FROM {_quote(table)}")

    written = 0
    offset = 0
    pages = 0
    last_id_seen: Optional[int] = None
    while True:
        cur = d1.execute(
            f"SELECT {quoted_cols} FROM {_quote(table)} "
            f"ORDER BY ROWID LIMIT ? OFFSET ?",
            (page_size, offset),
        )
        page_rows = cur.fetchall() or []
        pages += 1
        if not page_rows:
            break
        if not dry_run:
            tuples: List[Tuple[Any, ...]] = []
            for r in page_rows:
                if isinstance(r, dict):
                    tuples.append(tuple(r.get(c) for c in columns))
                else:
                    tuples.append(tuple(r))
            sqlite_conn.executemany(
                f"INSERT INTO {_quote(table)} ({quoted_cols}) "
                f"VALUES ({placeholders})",
                tuples,
            )
        written += len(page_rows)
        offset += len(page_rows)
        if "Id" in columns:
            try:
                if isinstance(page_rows[-1], dict):
                    last_id_seen = int(page_rows[-1].get("Id") or 0)
                else:
                    idx = columns.index("Id")
                    last_id_seen = int(page_rows[-1][idx] or 0)
            except (TypeError, ValueError, IndexError):
                pass
        if len(page_rows) < page_size:
            break

    if not dry_run and "Id" in columns and last_id_seen is not None:
        # Update sqlite_sequence so future AUTOINCREMENT inserts don't
        # clash with ids we just imported.  No-op if the table doesn't
        # use AUTOINCREMENT (rowid sequence not tracked).
        try:
            sqlite_conn.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name=?",
                (last_id_seen, table),
            )
        except sqlite3.OperationalError:
            pass

    post_sqlite = _sqlite_table_count(sqlite_conn, table) if not dry_run else 0

    return {
        "table": table,
        "skipped": False,
        "d1_count": pre_d1,
        "sqlite_count_before": pre_sqlite,
        "sqlite_count_after": post_sqlite if not dry_run else None,
        "rows_streamed": written,
        "pages": pages,
        "last_id_seen": last_id_seen,
        "delta_d1_minus_sqlite_before": pre_d1 - pre_sqlite,
        "consistent_after": (
            (pre_d1 == post_sqlite) if not dry_run else None
        ),
    }


def _sync_one_logical(
    logical_name: str,
    sqlite_path: str,
    *,
    page_size: int,
    dry_run: bool,
) -> Dict[str, Any]:
    logger.info(
        "Syncing logical=%s sqlite=%s dry_run=%s",
        logical_name, sqlite_path, dry_run,
    )
    d1 = D1Connection(
        account_id=get_d1_account_id(),
        database_id=get_d1_database_id(logical_name),
        api_token=get_d1_api_token(),
    )
    os.makedirs(os.path.dirname(sqlite_path) or ".", exist_ok=True)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_conn.execute("PRAGMA foreign_keys=OFF")
    if not dry_run:
        _ensure_local_sqlite_schema(sqlite_conn)

    summary: Dict[str, Any] = {
        "logical_name": logical_name,
        "sqlite_path": sqlite_path,
        "tables": [],
    }
    try:
        tables = _list_business_tables(d1)
        if not tables:
            logger.warning("No business tables on logical=%s", logical_name)
        sqlite_conn.execute("BEGIN")
        try:
            for table in tables:
                tbl_summary = _sync_one_table(
                    d1, sqlite_conn, table,
                    page_size=page_size, dry_run=dry_run,
                )
                summary["tables"].append(tbl_summary)
                logger.info(
                    "  %s: D1=%s sqlite_before=%s rows_streamed=%s%s",
                    table,
                    tbl_summary.get("d1_count"),
                    tbl_summary.get("sqlite_count_before"),
                    tbl_summary.get("rows_streamed"),
                    "" if dry_run else (
                        f" sqlite_after={tbl_summary.get('sqlite_count_after')}"
                    ),
                )
            if dry_run:
                sqlite_conn.execute("ROLLBACK")
            else:
                sqlite_conn.execute("COMMIT")
        except Exception:
            sqlite_conn.execute("ROLLBACK")
            raise
    finally:
        sqlite_conn.close()
        d1.close()
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)
    _refuse_when_dual_or_d1()

    selected_logical = (
        [s.strip() for s in args.logical_names.split(",") if s.strip()]
        if args.logical_names
        else [t[0] for t in _TARGETS]
    )
    targets = [t for t in _TARGETS if t[0] in selected_logical]
    if not targets:
        logger.error("No matching logical names from %s", args.logical_names)
        return 2

    sqlite_paths = [cfg(key, default) for _, key, default in targets]

    if not args.dry_run:
        backup_dir = _backup_sqlite(sqlite_paths)
        logger.info("Backup directory: %s", backup_dir or "<no existing files to back up>")

    started = time.time()
    overall: Dict[str, Any] = {
        "kind": "sync_d1_to_sqlite",
        "dry_run": args.dry_run,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "logical_names": selected_logical,
        "results": [],
    }
    for (logical_name, _key, _default), sqlite_path in zip(targets, sqlite_paths):
        try:
            result = _sync_one_logical(
                logical_name, sqlite_path,
                page_size=args.page_size, dry_run=args.dry_run,
            )
        except Exception as exc:
            logger.exception(
                "Sync failed for logical=%s: %s", logical_name, exc,
            )
            result = {"logical_name": logical_name, "error": str(exc)}
        overall["results"].append(result)
    overall["elapsed_seconds"] = round(time.time() - started, 2)

    reports_dir = (
        os.environ.get("REPORTS_DIR")
        or cfg("REPORTS_DIR", "reports")
        or "reports"
    )
    report_path = args.report_path or os.path.join(
        reports_dir,
        "D1",
        "sync_d1_to_sqlite",
        "sync_d1_to_sqlite_{}_{}.json".format(
            "dryrun" if args.dry_run else "apply",
            datetime.now().strftime("%Y%m%d_%H%M%S"),
        ),
    )
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)
    logger.info("Wrote report: %s", report_path)
    # Verbatim JSON output is preserved (downstream pipelines / humans
    # may be parsing it), but wrap it in a GitHub Actions group so the
    # CI UI folds the multi-line dump by default.  On non-Actions
    # consoles ``log_group_*`` degrade to a section divider.
    log_group_start(logger, "JSON output")
    print(json.dumps(overall, ensure_ascii=False, indent=2))
    log_group_end(logger)

    # Non-zero exit when any logical sync errored or any post-apply
    # consistency check failed.
    failed = [
        r for r in overall["results"]
        if "error" in r or any(
            t.get("consistent_after") is False for t in r.get("tables", [])
        )
    ]
    if failed:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
