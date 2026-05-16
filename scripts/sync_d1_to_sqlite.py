#!/usr/bin/env python3
"""One-shot tool: pull every business table from D1 down into local sqlite.

Usage::

    python -m scripts.sync_d1_to_sqlite              # dry-run report
    python -m scripts.sync_d1_to_sqlite --apply      # upsert D1 into sqlite
    python -m scripts.sync_d1_to_sqlite --apply --prune-local-only
                                                      # additionally remove
                                                      # local rows not on D1

Why this exists
---------------
On 2026-05-08 the local sqlite mirror drifted ~1k rows behind D1 because
some CI runs commit only the D1 side (no ``git push reports/*.db``). When
the planned cleanup of the 332/346 phantom audits runs, both sides need
to start from the same snapshot — otherwise pulling D1 into sqlite later
will quietly resurrect the very phantoms we just deleted.

What it does
------------
For each of ``javdb-history`` / ``javdb-reports`` / ``javdb-operations``,
walks the business tables (skipping ``_cf_KV``, ``sqlite_sequence``,
``SchemaVersion``), pages through ``SELECT *`` from D1 in chunks of
``--page-size`` rows, and **upserts** them into local sqlite preserving
the original ``Id`` columns. Default mode never deletes SQLite rows —
this protects against the legacy DELETE+REINSERT behaviour that
permanently destroyed SQLite-only rows whenever D1 was behind on even
a single asymmetric insert (e.g. the 2026-05 ``ReportSessions`` /
``SpiderStats`` -1 deltas).

Safety
------
* Default mode is dry-run: prints a summary table of counts and
  diffs, and writes a JSON report to
  ``reports/D1/sync_d1_to_sqlite/sync_d1_to_sqlite_dryrun_<ts>.json``.
* ``--apply`` first copies the existing ``reports/*.db`` into
  ``reports/_backup_<ts>/`` so you can always roll back.
* ``--apply`` is now **upsert-only** by default. Pass
  ``--prune-local-only`` to additionally delete SQLite rows whose PK
  is not present on D1. The prune step refuses to run on any table
  where the dry-run delta showed ``delta_d1_minus_sqlite_before < 0``
  (SQLite ahead of D1) unless ``--allow-local-prune-on-drift`` is
  explicitly set — that combination is the legacy DELETE+REINSERT
  behaviour and should only ever be reached after reconciling the
  drift log by hand.
* Aborts if STORAGE_BACKEND is ``dual`` or ``d1`` while the script runs,
  to prevent live writes from racing the import.

Not for cron
------------
This is an incident-response tool. Run it by hand, look at the output,
re-run with ``--apply``. Never wire it into a workflow.
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
    def _positive_int(raw: str) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise argparse.ArgumentTypeError(
                f"--page-size must be an integer, got {raw!r}"
            ) from exc
        if value <= 0:
            raise argparse.ArgumentTypeError(
                f"--page-size must be > 0, got {value}"
            )
        return value

    p.add_argument(
        "--page-size",
        type=_positive_int,
        default=_DEFAULT_PAGE_SIZE,
        help="Rows per D1 page query (default 500). Must be > 0.",
    )
    p.add_argument(
        "--prune-local-only",
        dest="prune_local_only",
        action="store_true",
        default=False,
        help=(
            "Destructive: when --apply is set, also delete local rows "
            "that do not exist in D1 (matched by primary key). Default "
            "off — the upsert-only mode preserves SQLite-only rows so a "
            "transient asymmetric insert (D1 -1 relative to SQLite) is "
            "not amplified into permanent local deletion. Required to "
            "reproduce the legacy DELETE+REINSERT mirror semantics."
        ),
    )
    p.add_argument(
        "--allow-local-prune-on-drift",
        dest="allow_local_prune_on_drift",
        action="store_true",
        default=False,
        help=(
            "By default --prune-local-only refuses to delete when the "
            "dry-run pass observes delta_d1_minus_sqlite_before < 0 "
            "(SQLite has rows D1 does not). Pass this flag to override "
            "that safety check after manually reconciling the drift log."
        ),
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
             "reports/D1/sync_d1_to_sqlite/sync_d1_to_sqlite_"
             "<dryrun|apply>_<ts>.json.",
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
    cur = d1.execute(f'PRAGMA table_info("{table}")')
    rows = cur.fetchall() or []
    cols: List[str] = []
    for r in rows:
        name = r["name"] if isinstance(r, dict) else r[1]
        if name:
            cols.append(name)
    return cols


def _table_pk_columns(d1: D1Connection, table: str) -> List[str]:
    """Return the ordered PRIMARY KEY columns of *table* (empty if none).

    Used by the UPSERT and prune paths so we can identify rows by their
    declared primary key instead of the (transient) ROWID. ``PRAGMA
    table_info`` reports a ``pk`` integer per column: 0 means "not a
    primary-key column", 1+ encodes the column's position in a possibly-
    composite key.
    """
    cur = d1.execute(f'PRAGMA table_info("{table}")')
    rows = cur.fetchall() or []
    pk_pairs: List[Tuple[int, str]] = []
    for r in rows:
        if isinstance(r, dict):
            name = r.get("name")
            pk = int(r.get("pk") or 0)
        else:
            # Row positional layout: (cid, name, type, notnull, dflt_value, pk)
            name = r[1] if len(r) > 1 else None
            pk = int(r[5] or 0) if len(r) > 5 else 0
        if name and pk > 0:
            pk_pairs.append((pk, name))
    pk_pairs.sort(key=lambda kv: kv[0])
    return [name for _, name in pk_pairs]


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
    """Bring a local mirror forward before inserting D1's current columns.

    The caller invokes this **inside** the data-sync transaction so any
    failure during the subsequent DELETE/INSERT phase rolls back the
    schema bump along with the partial data load — otherwise a
    half-applied schema would survive a failed sync.
    """
    db_mod._ensure_rollback_columns(conn)


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
    prune_local_only: bool = False,
    allow_local_prune_on_drift: bool = False,
) -> Dict[str, Any]:
    """Page through D1 and (optionally) mirror the rows into sqlite.

    P0-8 changes the default mode from DELETE-then-INSERT (which
    permanently destroyed SQLite-only rows whenever D1 was behind on
    even a single asymmetric write) to INSERT-OR-REPLACE keyed on the
    table's PRIMARY KEY. SQLite rows that have no D1 counterpart are
    preserved unless the caller explicitly opts into
    ``--prune-local-only``, and even then we refuse to prune if the
    dry-run delta showed SQLite leading D1 (``delta_d1_minus_sqlite_
    before < 0``) — exactly the scenario that produced the 2026-05
    ``ReportSessions`` / ``SpiderStats`` -1 deltas in
    ``reports/D1/sync_d1_to_sqlite/``.
    """
    columns = _table_columns(d1, table)
    if not columns:
        return {
            "table": table,
            "skipped": True,
            "reason": "no columns reported by D1",
        }
    pk_columns = _table_pk_columns(d1, table)
    quoted_cols = ", ".join(_quote(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)

    pre_d1 = _table_count(d1, table)
    pre_sqlite = _sqlite_table_count(sqlite_conn, table)

    # P0-8: default = upsert; preserve SQLite-only rows. INSERT OR
    # REPLACE keys on the table's declared UNIQUE / PRIMARY KEY
    # constraint(s); ON CONFLICT-DO-UPDATE would require knowing the PK
    # at SQL-compile time and is functionally equivalent here.
    insert_sql = (
        f"INSERT OR REPLACE INTO {_quote(table)} ({quoted_cols}) "
        f"VALUES ({placeholders})"
    )

    # Decide whether prune is allowed *before* mutating anything.
    delta_before = pre_d1 - pre_sqlite
    prune_blocked_reason: Optional[str] = None
    if prune_local_only and not pk_columns:
        prune_blocked_reason = (
            "table has no PRIMARY KEY; prune would require a full DELETE "
            "and is refused"
        )
    elif (
        prune_local_only
        and delta_before < 0
        and not allow_local_prune_on_drift
    ):
        prune_blocked_reason = (
            f"delta_d1_minus_sqlite_before={delta_before} (SQLite has "
            f"{-delta_before} extra row(s) D1 lacks); refusing to "
            f"prune without --allow-local-prune-on-drift"
        )

    written = 0
    offset = 0
    pages = 0
    last_id_seen: Optional[int] = None
    seen_pks: set = set() if (not dry_run and prune_local_only and pk_columns
                              and prune_blocked_reason is None) else set()
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
            sqlite_conn.executemany(insert_sql, tuples)
            if pk_columns and prune_local_only and prune_blocked_reason is None:
                pk_indices = [columns.index(pk) for pk in pk_columns]
                for t in tuples:
                    seen_pks.add(tuple(t[i] for i in pk_indices))
        written += len(page_rows)
        offset += len(page_rows)
        if "Id" in columns:
            try:
                if isinstance(page_rows[-1], dict):
                    extracted_id = int(page_rows[-1].get("Id") or 0)
                else:
                    idx = columns.index("Id")
                    extracted_id = int(page_rows[-1][idx] or 0)
                # Track the running max across pages so sqlite_sequence
                # ends up reflecting MAX(Id) even if D1's ROWID ordering
                # surfaces a lower Id on the final page.
                if last_id_seen is None or extracted_id > last_id_seen:
                    last_id_seen = extracted_id
            except (TypeError, ValueError, IndexError):
                pass
        if len(page_rows) < page_size:
            break

    pruned_rows = 0
    if (
        not dry_run
        and prune_local_only
        and pk_columns
        and prune_blocked_reason is None
    ):
        # Delete SQLite rows whose PK is not in the seen set. Done in
        # one statement using a temp table to handle composite keys and
        # avoid SQL-IN length limits.
        cur = sqlite_conn.cursor()
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _sync_seen_pk ("
            + ", ".join(f"k{i} TEXT" for i in range(len(pk_columns)))
            + ")"
        )
        cur.execute("DELETE FROM _sync_seen_pk")
        if seen_pks:
            cur.executemany(
                "INSERT INTO _sync_seen_pk VALUES ("
                + ", ".join("?" for _ in pk_columns)
                + ")",
                [tuple(str(v) for v in pk) for pk in seen_pks],
            )
        pk_join = " AND ".join(
            f"CAST(t.{_quote(c)} AS TEXT) = s.k{i}"
            for i, c in enumerate(pk_columns)
        )
        cur.execute(
            f"DELETE FROM {_quote(table)} AS t "
            f"WHERE NOT EXISTS (SELECT 1 FROM _sync_seen_pk AS s WHERE {pk_join})"
        )
        pruned_rows = cur.rowcount or 0

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
        "delta_d1_minus_sqlite_before": delta_before,
        "consistent_after": (
            (pre_d1 == post_sqlite) if (not dry_run and prune_local_only
                                        and prune_blocked_reason is None)
            else None
        ),
        "mode": (
            "dry-run" if dry_run
            else ("upsert+prune" if (prune_local_only
                                     and prune_blocked_reason is None)
                  else "upsert-only")
        ),
        "pk_columns": pk_columns,
        "pruned_rows": pruned_rows,
        "prune_blocked_reason": prune_blocked_reason,
    }


def _sync_one_logical(
    logical_name: str,
    sqlite_path: str,
    *,
    page_size: int,
    dry_run: bool,
    prune_local_only: bool = False,
    allow_local_prune_on_drift: bool = False,
) -> Dict[str, Any]:
    logger.info(
        "Syncing logical=%s sqlite=%s dry_run=%s prune_local_only=%s",
        logical_name, sqlite_path, dry_run, prune_local_only,
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
            if not dry_run:
                # Schema bump runs inside the transaction so a failure
                # during the data phase rolls it back too.
                _ensure_local_sqlite_schema(sqlite_conn)
            for table in tables:
                tbl_summary = _sync_one_table(
                    d1, sqlite_conn, table,
                    page_size=page_size, dry_run=dry_run,
                    prune_local_only=prune_local_only,
                    allow_local_prune_on_drift=allow_local_prune_on_drift,
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
            # Python's sqlite3 driver may auto-commit when DDL ran inside
            # the loop (PRAGMA / ALTER inside _ensure_local_sqlite_schema)
            # which terminates our outer BEGIN. Tolerate the "no
            # transaction is active" race that follows — it means the
            # transaction has already been flushed, which is functionally
            # what we wanted on the COMMIT path; on dry-run a missing
            # transaction is also harmless because no DML survived.
            if dry_run:
                try:
                    sqlite_conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
            else:
                try:
                    sqlite_conn.execute("COMMIT")
                except sqlite3.OperationalError:
                    pass
        except Exception:
            try:
                sqlite_conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
    finally:
        sqlite_conn.close()
        d1.close()
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)
    _refuse_when_dual_or_d1()

    available_names = [t[0] for t in _TARGETS]
    selected_logical = (
        [s.strip() for s in args.logical_names.split(",") if s.strip()]
        if args.logical_names
        else list(available_names)
    )
    if args.logical_names:
        unknown = [n for n in selected_logical if n not in available_names]
        if unknown:
            logger.error(
                "Unknown logical name(s) in --logical-names: %s. "
                "Valid choices: %s",
                ", ".join(unknown), ", ".join(available_names),
            )
            return 2
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
    overall["prune_local_only"] = args.prune_local_only
    overall["allow_local_prune_on_drift"] = args.allow_local_prune_on_drift
    for (logical_name, _key, _default), sqlite_path in zip(targets, sqlite_paths):
        try:
            result = _sync_one_logical(
                logical_name, sqlite_path,
                page_size=args.page_size, dry_run=args.dry_run,
                prune_local_only=args.prune_local_only,
                allow_local_prune_on_drift=args.allow_local_prune_on_drift,
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
