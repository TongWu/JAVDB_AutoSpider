#!/usr/bin/env python3
"""Standalone migration: upgrade split SQLite layout from schema v7 to v8.

The same schema steps run automatically on every ``utils.db.init_db()`` when
any database file's ``SchemaVersion`` is below 8 (add ``ActorName`` /
``ActorLink`` on ``MovieHistory``, bump version). This script adds:

  - Optional ``--backup`` before mutating files.
  - Optional ``--verify`` (integrity + version + columns).
  - Schema ``--dry-run`` (report only, no writes).
  - Optional ``--backfill-actors`` to fetch detail pages and fill empty
    ``ActorName`` / ``ActorLink`` (same stack as the spider: proxy pool,
    ``fetch_detail_page_with_fallback``, ``movie_sleep_mgr``).

**v8 changes (history.db only, data columns):**

  - ``MovieHistory.ActorName TEXT DEFAULT ''``
  - ``MovieHistory.ActorLink TEXT DEFAULT ''``
  - ``SchemaVersion`` set to 8 on **all** open DB files (history / reports /
    operations) via ``init_db(force=True)`` so versions stay aligned.

Usage:

    python3 migration/migrate_v7_to_v8.py [--backup] [--verify] [--dry-run]
    python3 migration/migrate_v7_to_v8.py --backfill-actors [--limit N] [--no-proxy] [--dry-run]
    python3 migration/migrate_v7_to_v8.py [--backup] --backfill-actors

    # Only refill actors, schema already v8:
    python3 migration/migrate_v7_to_v8.py --skip-schema --backfill-actors
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from urllib.parse import urljoin

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

import requests  # noqa: E402

from utils.logging_config import setup_logging, get_logger  # noqa: E402

setup_logging()
logger = get_logger(__name__)

EXPECTED_VERSION = 8


def _detect_version(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'SchemaVersion' in tables:
            row = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
            return int(row[0]) if row else 0
        if 'schema_version' in tables:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            return int(row[0]) if row else 0
        return 0
    finally:
        conn.close()


def _moviehistory_has_actor_columns(db_path: str) -> bool:
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(MovieHistory)").fetchall()}
        return 'ActorName' in cols and 'ActorLink' in cols
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def backup_db_file(db_path: str, label: str) -> str | None:
    if not os.path.exists(db_path):
        return None
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.backup_v7_{ts}"
    shutil.copy2(db_path, backup_path)
    logger.info("Backup [%s]: %s", label, backup_path)
    return backup_path


def verify_v8_layout(
    history_path: str,
    reports_path: str,
    operations_path: str,
) -> bool:
    ok = True
    for label, path in (
        ('history.db', history_path),
        ('reports.db', reports_path),
        ('operations.db', operations_path),
    ):
        if not os.path.exists(path):
            logger.warning("Missing %s (%s) — skip checks for that file", label, path)
            continue
        ver = _detect_version(path)
        if ver != EXPECTED_VERSION:
            logger.error("%s: SchemaVersion is %s, expected %s", label, ver, EXPECTED_VERSION)
            ok = False
        else:
            logger.info("%s: SchemaVersion = %s", label, ver)

    if os.path.exists(history_path):
        if not _moviehistory_has_actor_columns(history_path):
            logger.error("history.db: MovieHistory missing ActorName/ActorLink columns")
            ok = False
        else:
            logger.info("history.db: MovieHistory has ActorName / ActorLink")

        conn = sqlite3.connect(history_path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != 'ok':
                logger.error("history.db integrity_check: %s", integrity)
                ok = False
            else:
                logger.info("history.db: integrity_check OK")
        finally:
            conn.close()

    return ok


def run_schema_migration(
    *,
    backup: bool,
    dry_run: bool,
    verify: bool,
) -> int:
    import utils.db as db_mod
    from utils.config_helper import use_sqlite

    if not use_sqlite():
        logger.error("SQLite storage mode required (config STORAGE_MODE / use_sqlite).")
        return 1

    h, r, o = db_mod.HISTORY_DB_PATH, db_mod.REPORTS_DB_PATH, db_mod.OPERATIONS_DB_PATH

    logger.info("=" * 60)
    logger.info("SCHEMA MIGRATION v7 → v8 (split DB layout)")
    for label, p in (("history", h), ("reports", r), ("operations", o)):
        if os.path.exists(p):
            logger.info("  %s: %s (version=%s)", label, p, _detect_version(p))
        else:
            logger.info("  %s: %s (missing)", label, p)
    logger.info("=" * 60)

    if not os.path.exists(h):
        logger.error("history.db not found: %s", h)
        logger.info("If you still use a single legacy DB, run migrate_v6_to_v7_split first.")
        return 1

    hist_ver = _detect_version(h)
    if hist_ver >= EXPECTED_VERSION and _moviehistory_has_actor_columns(h):
        logger.info("history.db already at v%s with actor columns. No schema migration needed.", EXPECTED_VERSION)
        if verify:
            return 0 if verify_v8_layout(h, r, o) else 1
        return 0

    if hist_ver >= EXPECTED_VERSION and not _moviehistory_has_actor_columns(h):
        logger.warning(
            "SchemaVersion is %s but MovieHistory lacks actor columns; applying fixes via init_db.",
            hist_ver,
        )

    if hist_ver < 7:
        logger.error("history.db version is %s; expected at least 7 (split layout).", hist_ver)
        logger.info("Run migrate_v6_to_v7_split (or init_db) before v7→v8.")
        return 1

    if dry_run:
        logger.info("[DRY RUN] Would run init_db(force=True) to apply v8 schema on all DB files.")
        return 0

    if backup:
        for label, p in (("history", h), ("reports", r), ("operations", o)):
            backup_db_file(p, label)

    logger.info("Running init_db(force=True) …")
    db_mod.init_db(force=True)

    new_h = _detect_version(h)
    logger.info("history.db SchemaVersion after migration: %s", new_h)

    if verify:
        logger.info("-" * 60)
        if not verify_v8_layout(h, r, o):
            logger.error("Verification FAILED")
            return 1
        logger.info("Verification PASSED")

    logger.info("Schema migration v7 → v8 complete.")
    return 0


def run_actor_backfill(
    history_db: str,
    *,
    dry_run: bool,
    limit: int,
    no_proxy: bool,
    use_cf_bypass: bool,
) -> int:
    from utils.config_helper import use_sqlite
    from utils.db import init_db

    if not use_sqlite():
        logger.error("SQLite storage mode required.")
        return 1

    init_db(force=True)

    if not os.path.exists(history_db):
        logger.error("History database not found: %s", history_db)
        return 1

    import scripts.spider.state as state
    from scripts.spider.config_loader import BASE_URL, REPORTS_DIR
    from scripts.spider.fallback import fetch_detail_page_with_fallback
    from scripts.spider.sleep_manager import movie_sleep_mgr

    ban_log_file = os.path.join(REPORTS_DIR, 'proxy_bans.csv')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    use_proxy = not no_proxy
    state.setup_proxy_pool(ban_log_file, use_proxy)
    state.initialize_request_handler()

    session = requests.Session()
    conn = sqlite3.connect(history_db)
    conn.row_factory = sqlite3.Row

    sql = (
        "SELECT Id, Href FROM MovieHistory "
        "WHERE ActorName IS NULL OR ActorName = '' "
        "ORDER BY Id"
    )
    params: tuple = ()
    if limit > 0:
        sql += " LIMIT ?"
        params = (limit,)

    rows = conn.execute(sql, params).fetchall()
    logger.info("Backfill: %d MovieHistory rows with empty ActorName", len(rows))

    now_fmt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    processed = 0

    for i, row in enumerate(rows, 1):
        mid = row["Id"]
        href = row["Href"]
        detail_url = urljoin(BASE_URL, href)
        entry_index = f"backfill-{i}/{len(rows)}"

        magnets, actor_name, actor_link, parse_ok, _ep, _ecf = fetch_detail_page_with_fallback(
            detail_url,
            session,
            use_cookie=True,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
            entry_index=entry_index,
            is_adhoc_mode=True,
        )

        an = (actor_name or "").strip()
        al = (actor_link or "").strip()

        if not an and not al:
            logger.warning(
                "[%s] No actor for %s (parse_ok=%s, magnets=%d)",
                entry_index,
                href,
                parse_ok,
                len(magnets or []),
            )
        else:
            logger.info("[%s] %s -> %r %r", entry_index, href, an, al)
            if not dry_run:
                conn.execute(
                    """UPDATE MovieHistory SET ActorName=?, ActorLink=?, DateTimeUpdated=?
                       WHERE Id=?""",
                    (an, al, now_fmt, mid),
                )
                conn.commit()
            processed += 1

        movie_sleep_mgr.sleep()

    conn.close()
    logger.info("Backfill done. Rows updated (or would update): %d", processed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate SQLite schema v7 → v8 (MovieHistory actors) and optional actor backfill.",
    )
    parser.add_argument(
        "--history-db",
        default=None,
        help="history.db path for --backfill-actors (default: from config)",
    )
    parser.add_argument("--backup", action="store_true", help="Backup DB files before schema migration")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After schema migration, verify version 8 and MovieHistory columns",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schema: preview only. With --backfill-actors: fetch but do not UPDATE.",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip init_db schema step (only use with --backfill-actors)",
    )
    parser.add_argument(
        "--backfill-actors",
        action="store_true",
        help="Fetch detail pages for rows with empty ActorName (network + proxy pool + movie sleep)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Backfill: max rows (0 = all)")
    parser.add_argument("--no-proxy", action="store_true", help="Backfill: direct HTTP (debug)")
    parser.add_argument(
        "--use-cf-bypass",
        action="store_true",
        help="Backfill: enable CF bypass on first fetch attempt",
    )
    args = parser.parse_args()

    import utils.db as db_mod

    history_db = args.history_db or db_mod.HISTORY_DB_PATH

    rc = 0
    if not args.skip_schema:
        rc = run_schema_migration(backup=args.backup, dry_run=args.dry_run, verify=args.verify)
        if rc != 0:
            return rc
    elif args.backfill_actors and args.verify:
        logger.info("--skip-schema: skipping schema verification phase")

    if args.backfill_actors:
        if args.dry_run and not args.skip_schema:
            logger.warning(
                "Schema was not applied (--dry-run). Backfill still runs; ensure DB is already v8.",
            )
        brc = run_actor_backfill(
            history_db,
            dry_run=args.dry_run,
            limit=args.limit,
            no_proxy=args.no_proxy,
            use_cf_bypass=args.use_cf_bypass,
        )
        if brc != 0:
            return brc

    if not args.backfill_actors and not args.skip_schema and not args.dry_run:
        logger.info("Tip: add --backfill-actors to populate ActorName/ActorLink from the site.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
