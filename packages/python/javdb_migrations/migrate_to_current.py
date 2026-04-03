#!/usr/bin/env python3
"""Upgrade all SQLite DBs to the current schema (split layout + MovieHistory v9).

Delegates schema bumps to ``utils.infra.db.init_db`` via ``run_schema_migration`` from
``migrate_v7_to_v8`` (which also handles version alignment across history /
reports / operations).

Optional steps (same flags as legacy migration scripts):

  - ``--normalize-datetimes`` — TEXT datetime normalization (from v6→v7 tooling)
  - ``--backfill-actors`` — fetch detail pages for empty lead actor fields

Usage::

    python3 -m apps.cli.migration [--backup] [--verify] [--dry-run]
    python3 -m apps.cli.migration --normalize-datetimes
    python3 -m apps.cli.migration --backfill-actors [--limit N] [--no-proxy]
    python3 -m apps.cli.migration --align-inventory-history [--align-limit-per-worker N] [--align-no-proxy] [--align-execute-delete]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.python.javdb_platform.logging_config import setup_logging, get_logger  # noqa: E402

setup_logging()
logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate SQLite databases to current schema (v9 + aligned SchemaVersion).",
    )
    parser.add_argument(
        "--history-db",
        default=None,
        help="history.db path for --backfill-actors (default: from config)",
    )
    parser.add_argument("--backup", action="store_true", help="Backup DB files before migration")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify schema version and MovieHistory actor columns",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schema: preview only. With --backfill-actors: fetch but do not UPDATE.",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip schema init (use with --backfill-actors or --normalize-datetimes only)",
    )
    parser.add_argument(
        "--normalize-datetimes",
        action="store_true",
        help="Normalize DateTime TEXT columns (history / reports / operations)",
    )
    parser.add_argument(
        "--backfill-actors",
        action="store_true",
        help="Fill empty ActorName (and related columns) from live detail pages",
    )
    parser.add_argument("--limit", type=int, default=0, help="Backfill: max rows (0 = all)")
    parser.add_argument("--no-proxy", action="store_true", help="Backfill: direct HTTP (debug)")
    parser.add_argument(
        "--use-cf-bypass",
        action="store_true",
        help="Backfill: enable CF bypass on first fetch attempt",
    )
    parser.add_argument(
        "--align-inventory-history",
        action="store_true",
        help="Align inventory-only codes into MovieHistory with JavDB search/detail enrichment",
    )
    parser.add_argument(
        "--align-limit",
        type=int,
        default=0,
        help="Alignment: absolute max missing codes (0 = all). Ignored when --align-limit-per-worker > 0.",
    )
    parser.add_argument(
        "--align-limit-per-worker",
        type=int,
        default=0,
        dest="align_limit_per_worker",
        help="Alignment: max completed tasks per proxy worker (0 = use --align-limit or all). "
        "Queue size is at most per-worker x len(PROXY_POOL); each worker stops after this many "
        "successful outcomes -- remaining workers do not absorb a banned peer's share.",
    )
    parser.add_argument(
        "--align-codes",
        type=str,
        default='',
        help="Alignment: comma-separated video codes override",
    )
    parser.add_argument(
        "--align-no-proxy",
        action="store_true",
        help="Alignment: direct HTTP without proxy (debug; proxy enabled by default)",
    )
    parser.add_argument(
        "--align-use-proxy",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--align-no-login",
        action="store_true",
        help="Alignment: skip movies requiring JavDB login instead of attempting authentication",
    )
    parser.add_argument(
        "--align-shuffle",
        action="store_true",
        help="Alignment: randomise processing queue to avoid consecutive failures on similar prefixes",
    )
    parser.add_argument(
        "--align-enqueue-qb",
        action="store_true",
        help="Alignment: enqueue upgrade magnets to qBittorrent",
    )
    parser.add_argument(
        "--align-execute-delete",
        action="store_true",
        help="Alignment: run rclone purge on purge-plan CSV (destructive)",
    )
    parser.add_argument(
        "--align-output-dir",
        type=str,
        default='',
        help="Alignment: output directory for generated reports/plan files",
    )
    parser.add_argument(
        "--align-qb-category",
        type=str,
        default='',
        help="Alignment: qBittorrent category override for upgrade enqueue",
    )
    args = parser.parse_args()
    if args.align_no_proxy and args.align_use_proxy:
        parser.error("--align-no-proxy and deprecated --align-use-proxy cannot be used together")
    if args.align_use_proxy:
        logger.warning(
            "--align-use-proxy is deprecated; alignment now uses proxy by default. "
            "Use --align-no-proxy to disable proxy.",
        )

    import packages.python.javdb_platform.db as db_mod
    from packages.python.javdb_platform.config_helper import use_sqlite, cfg

    from migration.tools.migrate_v6_to_v7_split import _normalize_three_dbs
    from migration.tools.align_inventory_with_moviehistory import run_alignment
    from migration.tools.migrate_v7_to_v8 import (
        backup_db_file,
        run_actor_backfill,
        run_schema_migration,
        verify_v8_layout,
    )

    if not use_sqlite():
        logger.error("SQLite storage mode required (config STORAGE_MODE / use_sqlite).")
        return 1

    h, r, o = db_mod.HISTORY_DB_PATH, db_mod.REPORTS_DB_PATH, db_mod.OPERATIONS_DB_PATH
    history_db = args.history_db or h

    if args.dry_run:
        logger.info("[DRY RUN] No database writes for schema / normalize steps.")

    if args.backup and not args.dry_run:
        for label, p in (("history", h), ("reports", r), ("operations", o)):
            backup_db_file(p, label)

    rc = 0
    if not args.skip_schema:
        rc = run_schema_migration(backup=False, dry_run=args.dry_run, verify=False)
        if rc != 0:
            return rc
    elif args.verify and not args.backfill_actors:
        logger.info("--skip-schema: run --verify with an explicit schema pass if needed")

    if args.normalize_datetimes:
        if args.dry_run:
            logger.info("[DRY RUN] Would normalize datetime TEXT columns on split DB files")
        else:
            _normalize_three_dbs(h, r, o)

    if args.verify:
        logger.info("-" * 60)
        if not verify_v8_layout(h, r, o):
            logger.error("Verification FAILED")
            return 1
        logger.info("Verification PASSED")

    if args.backfill_actors:
        if args.dry_run and not args.skip_schema:
            logger.warning(
                "Schema was not applied (--dry-run). Ensure DB is already at current version.",
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

    if args.align_inventory_history:
        align_output_dir = args.align_output_dir or os.path.join(cfg('REPORTS_DIR', 'reports'), 'Migration')
        align_ns = SimpleNamespace(
            dry_run=args.dry_run,
            limit=args.align_limit,
            limit_per_worker=args.align_limit_per_worker,
            codes=args.align_codes,
            use_proxy=not args.align_no_proxy,
            no_login=args.align_no_login,
            shuffle=args.align_shuffle,
            output_dir=align_output_dir,
            enqueue_qb=args.align_enqueue_qb,
            qb_category=args.align_qb_category,
            execute_delete=args.align_execute_delete,
        )
        arc = run_alignment(align_ns)
        if arc != 0:
            return arc

    if (
        not args.backfill_actors
        and not args.align_inventory_history
        and not args.skip_schema
        and not args.dry_run
    ):
        logger.info("Tip: use --backfill-actors to populate actor fields from the site.")

    logger.info("migrate_to_current finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
