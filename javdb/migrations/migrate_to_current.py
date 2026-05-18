#!/usr/bin/env python3
"""Upgrade all SQLite DBs to the current schema (split layout + MovieHistory v9).

Delegates schema bumps to ``utils.infra.db.init_db`` via ``run_schema_migration`` from
``migrate_v7_to_v8`` (which also handles version alignment across history /
reports / operations).

Optional steps (same flags as legacy migration scripts):

  - ``--normalize-datetimes`` — TEXT datetime normalization (from v6→v7 tooling)
  - ``--backfill-actors`` — fetch detail pages for empty lead actor fields

Usage::

    python3 -m apps.cli.db.migration [--backup] [--verify] [--dry-run]
    python3 -m apps.cli.db.migration --normalize-datetimes
    python3 -m apps.cli.db.migration --backfill-actors [--limit N] [--no-proxy]
    python3 -m apps.cli.db.migration --align-inventory-history [--align-limit-per-worker N] [--align-no-proxy] [--align-execute-delete]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.logging import (  # noqa: E402
    get_logger,
    log_section,
    setup_logging,
)

setup_logging()
logger = get_logger(__name__)


_LFS_POINTER_MAGIC = b"version https://git-lfs.github.com/spec/v1"


def _is_lfs_pointer(path: str) -> bool:
    """True iff *path* is a Git-LFS pointer (smudge filter did not run).

    A pointer file is always small (<1KB) and starts with the documented
    LFS magic line — we check both so a legitimately tiny SQLite header
    is not mistaken for a pointer.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size > 4096:
        return False
    try:
        with open(path, 'rb') as f:
            head = f.read(len(_LFS_POINTER_MAGIC))
    except OSError:
        return False
    return head == _LFS_POINTER_MAGIC


def _local_sqlite_needs_recovery(paths: list[str]) -> list[str]:
    """Return the subset of *paths* that are missing or LFS pointers."""
    bad: list[str] = []
    for p in paths:
        if not os.path.exists(p):
            bad.append(p)
        elif _is_lfs_pointer(p):
            bad.append(p)
    return bad


def _try_lfs_pull(paths: list[str]) -> bool:
    """Best-effort ``git lfs pull`` for the given paths.

    Returns True only if every path is a valid SQLite file afterwards.
    Returns False if ``git-lfs`` is unavailable, the pull fails, or any
    path is still a pointer / missing — caller falls back to d1-only.
    """
    if shutil.which("git-lfs") is None and shutil.which("git") is None:
        logger.warning("git-lfs not installed; cannot resolve LFS pointers")
        return False
    include = ",".join(os.path.relpath(p) for p in paths)
    logger.info("Attempting `git lfs pull --include=%s` …", include)
    try:
        result = subprocess.run(
            ["git", "lfs", "pull", f"--include={include}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        logger.warning("`git lfs` not available on PATH; skipping LFS pull")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("git lfs pull timed out after 300s")
        return False
    if result.returncode != 0:
        logger.warning(
            "git lfs pull failed (rc=%s): %s",
            result.returncode,
            (result.stderr or result.stdout or '').strip()[:500],
        )
        return False
    remaining = _local_sqlite_needs_recovery(paths)
    if remaining:
        logger.warning(
            "git lfs pull completed but %d file(s) still missing/pointers: %s",
            len(remaining),
            ", ".join(remaining),
        )
        return False
    logger.info("git lfs pull succeeded; local SQLite files are now valid")
    return True


def _bootstrap_storage_backend_for_align(paths: list[str]) -> str:
    """Pick the storage backend before running ``--align-inventory-history``.

    Honours the user's explicit ``STORAGE_BACKEND`` env var if it's
    already set — operators who set ``d1`` or ``dual`` deliberately
    should not be silently downgraded.

    Otherwise: if local SQLite files are intact use ``dual`` (with
    ``STRICT_DUAL_WRITE=1`` so D1 write failures abort the batch); if
    any are missing or LFS pointers, attempt ``git lfs pull`` and fall
    back to ``d1`` when that doesn't recover them. ``d1`` mode skips
    local SQLite writes entirely so a missing mirror is not a blocker.

    Returns the effective backend ("sqlite", "dual", or "d1").
    """
    explicit = (os.environ.get("STORAGE_BACKEND") or "").strip().lower()
    bad = _local_sqlite_needs_recovery(paths)

    if explicit in ("sqlite", "dual", "d1"):
        if explicit in ("dual", "sqlite") and bad:
            logger.warning(
                "STORAGE_BACKEND=%s is set explicitly but %d local DB file(s) "
                "are missing or LFS pointers: %s. Attempting LFS pull …",
                explicit, len(bad), ", ".join(bad),
            )
            if not _try_lfs_pull(bad):
                if explicit == "sqlite":
                    logger.error(
                        "STORAGE_BACKEND=sqlite explicit + no recoverable local "
                        "files: cannot proceed without D1 fallback. Either run "
                        "`python3 -m apps.cli.db.sync_d1_to_sqlite --apply "
                        "--force-overwrite-all` first, or set STORAGE_BACKEND=d1.",
                    )
                else:
                    logger.warning(
                        "Downgrading STORAGE_BACKEND from dual → d1 since local "
                        "SQLite mirror is unrecoverable. Local DB writes will "
                        "be skipped for this run.",
                    )
                    os.environ["STORAGE_BACKEND"] = "d1"
                    return "d1"
        if explicit == "dual":
            os.environ.setdefault("STRICT_DUAL_WRITE", "1")
        return explicit

    if not bad:
        os.environ["STORAGE_BACKEND"] = "dual"
        os.environ.setdefault("STRICT_DUAL_WRITE", "1")
        logger.info(
            "STORAGE_BACKEND=dual selected for alignment (D1 + local SQLite, "
            "STRICT_DUAL_WRITE=1: D1 write failures abort the batch).",
        )
        return "dual"

    logger.warning(
        "Local SQLite mirror is incomplete (%d file(s) missing/pointers): %s",
        len(bad), ", ".join(bad),
    )
    if _try_lfs_pull(bad):
        os.environ["STORAGE_BACKEND"] = "dual"
        os.environ.setdefault("STRICT_DUAL_WRITE", "1")
        logger.info("STORAGE_BACKEND=dual after successful LFS pull")
        return "dual"

    os.environ["STORAGE_BACKEND"] = "d1"
    logger.warning(
        "Falling back to STORAGE_BACKEND=d1 — local SQLite writes will be "
        "SKIPPED for this run. D1 remains the source of truth; rebuild the "
        "local mirror later with `python3 -m apps.cli.db.sync_d1_to_sqlite "
        "--apply --force-overwrite-all`.",
    )
    return "d1"


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

    import javdb.storage.db.db as db_mod
    from javdb.infra.config import use_sqlite, cfg

    from javdb.migrations.tools.migrate_v6_to_v7_split import _normalize_three_dbs
    from javdb.migrations.tools.align_inventory_with_moviehistory import run_alignment
    from javdb.migrations.tools.migrate_v7_to_v8 import (
        backup_db_file,
        run_actor_backfill,
        run_schema_migration,
        verify_v8_layout,
    )

    h, r, o = db_mod.HISTORY_DB_PATH, db_mod.REPORTS_DB_PATH, db_mod.OPERATIONS_DB_PATH
    history_db = args.history_db or h

    # ── Bootstrap storage backend for --align-inventory-history ───────
    # The alignment flow needs to update both local SQLite and D1 with
    # D1 as the source of truth. When the local mirror is gone (LFS not
    # pulled), we try `git lfs pull` and fall back to d1-only writes
    # rather than failing — the SQLite use_sqlite() gate below would
    # otherwise reject every dual/d1 runtime regardless of why SQLite is
    # unavailable. Schema migration and v8 verify only make sense
    # against a real local SQLite file, so we skip them in d1-only mode.
    align_backend: str | None = None
    if args.align_inventory_history:
        align_backend = _bootstrap_storage_backend_for_align([h, r, o])
        if align_backend == "d1":
            if not args.skip_schema:
                logger.info(
                    "Auto-enabling --skip-schema: local SQLite unavailable, "
                    "schema migration / v8 verify is a no-op against D1 "
                    "(D1 schema is managed by wrangler d1 migrations).",
                )
                args.skip_schema = True
            if args.verify:
                logger.warning(
                    "--verify ignored under STORAGE_BACKEND=d1 (no local v8 "
                    "layout to inspect).",
                )
                args.verify = False
            # db_mod has already cached HISTORY_DB_PATH etc.; the
            # backend env flip is read on each _get_connection() call,
            # so subsequent get_db() calls now return D1Connection
            # facades — no module reload needed.

    if not use_sqlite() and align_backend != "d1":
        logger.error("SQLite storage mode required (config STORAGE_MODE / use_sqlite).")
        return 1

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
        log_section(logger, "Verify v8 layout", emoji='🔍')
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
