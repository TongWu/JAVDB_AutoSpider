#!/usr/bin/env python3
"""Standalone migration: align split SQLite DBs with current schema (v9).

The same schema steps run automatically on every ``utils.infra.db.init_db()`` when
any database file's ``SchemaVersion`` is below ``utils.infra.db.SCHEMA_VERSION``.

**MovieHistory actor columns (history.db):**

  - ``ActorName``, ``ActorGender``, ``ActorLink``, ``SupportingActors`` (lead + JSON supporting cast)

For **datetime normalization** after split, prefer ``migration/migrate_to_current.py --normalize-datetimes``.

This script adds:

  - Optional ``--backup`` before mutating files.
  - Optional ``--verify`` (integrity + version + columns).
  - Schema ``--dry-run`` (report only, no writes).
  - Optional ``--backfill-actors`` to fetch each movie's detail page and fill actor fields (no actor-index batching).

Usage:

    python3 migration/tools/migrate_v7_to_v8.py [--backup] [--verify] [--dry-run]
    python3 migration/tools/migrate_v7_to_v8.py --backfill-actors [--limit N] [--no-proxy] [--dry-run]
    python3 migration/migrate_to_current.py [--normalize-datetimes]   # unified entry
"""

from __future__ import annotations

import argparse
import json
import os
import queue as queue_module
import shutil
import sqlite3
import sys
import threading
from datetime import datetime
from typing import List
from urllib.parse import urljoin

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

import requests  # noqa: E402

from api.parsers.common import javdb_absolute_url, absolutize_supporting_actors_json  # noqa: E402
from utils.infra.config_helper import cfg  # noqa: E402
from utils.infra.logging_config import setup_logging, get_logger  # noqa: E402

setup_logging()
logger = get_logger(__name__)

from utils.infra.db import moviehistory_actor_layout_ok  # noqa: E402

EXPECTED_VERSION = 9


def _is_valid_sqlite(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return f.read(6) == b'SQLite'
    except OSError:
        return False


def _detect_version(db_path: str) -> int:
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        logger.warning("%s is not a valid SQLite database (Git LFS pointer?)", db_path)
        return -1
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
    """All four actor columns present *and* SQLite storage order: Name, Gender, Link, Supporting."""
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return moviehistory_actor_layout_ok(conn)
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
            logger.error(
                "history.db: MovieHistory actor layout incomplete or wrong column order "
                "(expected ActorName, ActorGender, ActorLink, SupportingActors)",
            )
            ok = False
        else:
            logger.info(
                "history.db: MovieHistory actor columns OK (Name → Gender → Link → SupportingActors)",
            )

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
    import utils.infra.db as db_mod
    from utils.infra.config_helper import use_sqlite

    if not use_sqlite():
        logger.error("SQLite storage mode required (config STORAGE_MODE / use_sqlite).")
        return 1

    h, r, o = db_mod.HISTORY_DB_PATH, db_mod.REPORTS_DB_PATH, db_mod.OPERATIONS_DB_PATH

    logger.info("=" * 60)
    logger.info("SCHEMA MIGRATION → current (split DB layout + MovieHistory v9)")
    for label, p in (("history", h), ("reports", r), ("operations", o)):
        if os.path.exists(p):
            logger.info("  %s: %s (version=%s)", label, p, _detect_version(p))
        else:
            logger.info("  %s: %s (missing)", label, p)
    logger.info("=" * 60)

    if not os.path.exists(h):
        logger.error("history.db not found: %s", h)
        logger.info("If you still use a single legacy DB, run migration/tools/migrate_v6_to_v7_split.py first.")
        return 1

    hist_ver = _detect_version(h)
    if hist_ver >= EXPECTED_VERSION and _moviehistory_has_actor_columns(h):
        logger.info(
            "history.db already at v%s with canonical MovieHistory actor column order. No schema migration needed.",
            EXPECTED_VERSION,
        )
        if verify:
            return 0 if verify_v8_layout(h, r, o) else 1
        return 0

    if hist_ver >= EXPECTED_VERSION and not _moviehistory_has_actor_columns(h):
        logger.warning(
            "SchemaVersion is %s but MovieHistory needs actor columns or column reorder; applying init_db(force=True).",
            hist_ver,
        )

    if hist_ver < 7:
        logger.error("history.db version is %s; expected at least 7 (split layout).", hist_ver)
        logger.info("Run migration/tools/migrate_v6_to_v7_split.py (or init_db) before upgrade.")
        return 1

    if dry_run:
        logger.info("[DRY RUN] Would run init_db(force=True) to apply current schema on all DB files.")
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

    logger.info("Schema migration to current version complete.")
    return 0


# ---------------------------------------------------------------------------
# Parallel actor backfill — powered by FetchEngine
# ---------------------------------------------------------------------------


def _is_meaningful_actor_data(an: str, al: str, sup: str) -> bool:
    """True when at least one actor field carries real content (not just ``'[]'``)."""
    if an.strip():
        return True
    if al.strip():
        return True
    s = sup.strip()
    return bool(s and s != '[]')


def _promote_single_female_actor(
    actor_name: str,
    actor_gender: str,
    actor_link: str,
    supporting_actors: str,
) -> tuple[str, str, str, str]:
    """Promote the only female actor to lead when current lead is not female.

    Rule applies only when:
      1) every actor has explicit gender in {female, male}
      2) exactly one actor is female
      3) current lead is not female

    Supporting actor relative order is preserved for everyone except the promoted
    female actor and the original lead (which becomes the second actor).
    """
    lead_name = (actor_name or '').strip()
    lead_gender = (actor_gender or '').strip()
    lead_link = (actor_link or '').strip()
    supporting_raw = (supporting_actors or '').strip()

    if not lead_name:
        return lead_name, lead_gender, lead_link, supporting_raw
    if lead_gender.lower() == 'female':
        return lead_name, lead_gender, lead_link, supporting_raw
    if not supporting_raw:
        return lead_name, lead_gender, lead_link, supporting_raw

    try:
        supporting_list = json.loads(supporting_raw)
    except (json.JSONDecodeError, TypeError):
        return lead_name, lead_gender, lead_link, supporting_raw
    if not isinstance(supporting_list, list) or not supporting_list:
        return lead_name, lead_gender, lead_link, supporting_raw

    actors = [{
        'name': lead_name,
        'gender': lead_gender,
        'link': lead_link,
    }]
    for item in supporting_list:
        if not isinstance(item, dict):
            return lead_name, lead_gender, lead_link, supporting_raw
        actors.append({
            'name': str(item.get('name') or '').strip(),
            'gender': str(item.get('gender') or '').strip(),
            'link': str(item.get('link') or '').strip(),
        })

    genders_lower = [a['gender'].lower() for a in actors]
    if any(g not in {'female', 'male'} for g in genders_lower):
        return lead_name, lead_gender, lead_link, supporting_raw
    if genders_lower.count('female') != 1:
        return lead_name, lead_gender, lead_link, supporting_raw

    female_idx = genders_lower.index('female')
    if female_idx == 0:
        return lead_name, lead_gender, lead_link, supporting_raw

    promoted = actors[female_idx]
    original_lead = actors[0]
    others = [a for idx, a in enumerate(actors[1:], start=1) if idx != female_idx]
    reordered = [promoted, original_lead, *others]
    new_supporting = json.dumps(reordered[1:], ensure_ascii=False)
    return (
        reordered[0]['name'],
        reordered[0]['gender'],
        reordered[0]['link'],
        new_supporting,
    )


def _apply_backfill_result(
    result,
    *,
    completed_ids: set[int],
    dry_run: bool,
    conn: sqlite3.Connection,
    now_fmt: str,
) -> tuple[int, int, int]:
    """Persist one engine result.  Returns ``(processed, failed, skipped)``."""
    meta = result.task.meta
    movie_id = meta['movie_id']
    video_code = meta['video_code']
    href = meta['href']
    idx_str = result.task.entry_index

    if not result.success:
        logger.warning("[%s] Failed to get actor for %s (%s)", idx_str, video_code, href)
        return 0, 1, 0

    data = result.data
    an = (data['actor_name'] or '').strip()
    ag = (data['actor_gender'] or '').strip()
    al = (data['actor_link'] or '').strip()
    sup = (data['supporting'] or '').strip()
    an, ag, al, sup = _promote_single_female_actor(an, ag, al, sup)
    base_url = cfg('BASE_URL', 'https://javdb.com')
    al = javdb_absolute_url(al, base_url) if al else al
    sup = absolutize_supporting_actors_json(sup, base_url) if sup else sup

    if not _is_meaningful_actor_data(an, al, sup):
        logger.debug("[%s] Mark attempted (no actor data): %s", idx_str, video_code)
        completed_ids.add(movie_id)
        if not dry_run:
            conn.execute(
                "UPDATE MovieHistory SET SupportingActors=?, DateTimeUpdated=? WHERE Id=?",
                ('[]', now_fmt, movie_id),
            )
            conn.commit()
        return 0, 0, 1

    logger.debug("[%s] %s -> %r %r", idx_str, video_code, an, al)
    completed_ids.add(movie_id)
    if not dry_run:
        conn.execute(
            """UPDATE MovieHistory SET ActorName=?, ActorGender=?, ActorLink=?,
               SupportingActors=?, DateTimeUpdated=? WHERE Id=?""",
            (an, ag, al, sup, now_fmt, movie_id),
        )
        conn.commit()
    return 1, 0, 0


# ---------------------------------------------------------------------------
# Actor backfill entry-point
# ---------------------------------------------------------------------------


def run_actor_backfill(
    history_db: str,
    *,
    dry_run: bool,
    limit: int,
    no_proxy: bool,
    use_cf_bypass: bool,
) -> int:
    from utils.infra.config_helper import use_sqlite
    from utils.infra.db import init_db

    if not use_sqlite():
        logger.error("SQLite storage mode required.")
        return 1

    init_db(force=True)

    if not os.path.exists(history_db):
        logger.error("History database not found: %s", history_db)
        return 1

    import scripts.spider.runtime.state as state
    from scripts.spider.runtime.config import (
        BASE_URL, REPORTS_DIR, PROXY_POOL,
        MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX,
    )
    from scripts.spider.runtime.sleep import movie_sleep_mgr

    ban_log_file = os.path.join(REPORTS_DIR, 'proxy_bans.csv')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    use_proxy = not no_proxy
    state.setup_proxy_pool(ban_log_file, use_proxy)
    state.initialize_request_handler()

    conn = sqlite3.connect(history_db)
    conn.row_factory = sqlite3.Row

    sql = (
        "SELECT Id, Href, VideoCode FROM MovieHistory "
        "WHERE (ActorName IS NULL OR ActorName = '') "
        "ORDER BY Id"
    )
    params: tuple = ()
    if limit > 0:
        sql += " LIMIT ?"
        params = (limit,)

    rows = conn.execute(sql, params).fetchall()
    total = len(rows)
    logger.info("Backfill: %d MovieHistory rows with empty ActorName", total)

    if total == 0:
        conn.close()
        return 0

    movie_sleep_mgr.apply_volume_multiplier(total)
    now_fmt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Parallel mode: FetchEngine with one worker per proxy
    # ------------------------------------------------------------------
    if use_proxy and PROXY_POOL:
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask
        from utils.parser import parse_detail

        completed_ids: set[int] = set()
        stop_event = threading.Event()

        def _backfill_parse(html: str, task: EngineTask):
            _m, actor_name, actor_gender, actor_link, supporting, ok = (
                parse_detail(html, task.entry_index, skip_sleep=True)
            )
            if not ok:
                return None
            return {
                'actor_name': actor_name or '',
                'actor_gender': actor_gender or '',
                'actor_link': actor_link or '',
                'supporting': supporting or '',
            }

        engine = FetchEngine.simple(
            parse_fn=_backfill_parse,
            use_cookie=True,
            ban_log_file=ban_log_file,
            stop_event=stop_event,
            sleep_min=movie_sleep_mgr.sleep_min,
            sleep_max=movie_sleep_mgr.sleep_max,
        )
        engine.start()

        for i, row in enumerate(rows, 1):
            engine.submit(
                urljoin(BASE_URL, row["Href"]),
                entry_index=f"backfill-{i}/{total}",
                meta={
                    'movie_id': row["Id"],
                    'href': row["Href"],
                    'video_code': row["VideoCode"],
                },
            )
        engine.mark_done()

        logger.info(
            "Starting %d workers for %d backfill tasks (one detail page per row)",
            len(engine._workers), total,
        )

        processed = 0
        failed = 0
        skipped = 0
        parallel_interrupted = False

        try:
            for result in engine.results():
                p, f, s = _apply_backfill_result(
                    result,
                    completed_ids=completed_ids,
                    dry_run=dry_run,
                    conn=conn,
                    now_fmt=now_fmt,
                )
                processed += p
                failed += f
                skipped += s
        except KeyboardInterrupt:
            parallel_interrupted = True
            logger.warning("Keyboard interrupt — shutting down engine …")
            orphaned = engine.shutdown(timeout=30)

            drained = 0
            while True:
                try:
                    result = engine._result_queue.get_nowait()
                except queue_module.Empty:
                    break
                drained += 1
                p, f, s = _apply_backfill_result(
                    result,
                    completed_ids=completed_ids,
                    dry_run=dry_run,
                    conn=conn,
                    now_fmt=now_fmt,
                )
                processed += p
                failed += f
                skipped += s
            if drained:
                logger.info("Flushed %d pending result(s) after shutdown", drained)

            logger.info(
                "Backfill interrupted (parallel, %d workers). "
                "Updated: %d, Skipped: %d, Failed: %d — "
                "%d tasks orphaned (re-run backfill to continue)",
                len(engine._workers), processed, skipped, failed, len(orphaned),
            )
        else:
            engine.shutdown()
            logger.info(
                "Backfill done (parallel, %d workers). "
                "Updated: %d, Skipped: %d, Failed: %d",
                len(engine._workers), processed, skipped, failed,
            )

        conn.close()
        return 130 if parallel_interrupted else 0

    # ------------------------------------------------------------------
    # Sequential fallback (--no-proxy or no PROXY_POOL configured)
    # ------------------------------------------------------------------
    from scripts.spider.fetch.fallback import fetch_detail_page_with_fallback

    session = requests.Session()
    processed = 0
    failed = 0
    sequential_interrupted = False

    try:
        for i, row in enumerate(rows, 1):
            mid = row["Id"]
            href = row["Href"]
            video_code = row["VideoCode"]
            detail_url = urljoin(BASE_URL, href)
            entry_index = f"backfill-{i}/{total}"

            m = fetch_detail_page_with_fallback(
                detail_url, session,
                use_cookie=True, use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
                entry_index=entry_index, is_adhoc_mode=True,
            )
            magnets, actor_name, actor_gender, actor_link, supporting_actors, parse_ok, _ep, _ecf = m

            an = (actor_name or "").strip()
            ag = (actor_gender or "").strip()
            al = (actor_link or "").strip()
            sup = (supporting_actors or "").strip()
            an, ag, al, sup = _promote_single_female_actor(an, ag, al, sup)
            base_url = cfg('BASE_URL', 'https://javdb.com')
            al = javdb_absolute_url(al, base_url) if al else al
            sup = absolutize_supporting_actors_json(sup, base_url) if sup else sup

            if not _is_meaningful_actor_data(an, al, sup):
                logger.warning(
                    "[%s] No actor for %s (%s, parse_ok=%s, magnets=%d)",
                    entry_index, video_code, href, parse_ok, len(magnets or []),
                )
                if parse_ok and not dry_run:
                    conn.execute(
                        "UPDATE MovieHistory SET SupportingActors=?, DateTimeUpdated=? WHERE Id=?",
                        ('[]', now_fmt, mid),
                    )
                    conn.commit()
                if not parse_ok:
                    failed += 1
                movie_sleep_mgr.sleep()
                continue

            logger.info("[%s] %s -> %r %r", entry_index, video_code, an, al)
            if not dry_run:
                conn.execute(
                    """UPDATE MovieHistory SET ActorName=?, ActorGender=?, ActorLink=?,
                       SupportingActors=?, DateTimeUpdated=? WHERE Id=?""",
                    (an, ag, al, sup, now_fmt, mid),
                )
                conn.commit()
            processed += 1
            movie_sleep_mgr.sleep()

    except KeyboardInterrupt:
        sequential_interrupted = True
        logger.warning(
            "Keyboard interrupt — sequential backfill stopped. "
            "Rows already written this session are committed; "
            "the current fetch (if any) is not saved.",
        )

    conn.close()
    status = "interrupted" if sequential_interrupted else "done"
    logger.info(
        "Backfill %s (sequential). Updated: %d, Failed: %d",
        status, processed, failed,
    )
    return 130 if sequential_interrupted else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate SQLite schema to current (MovieHistory actors v9) and optional actor backfill.",
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
        help="After schema migration, verify SchemaVersion and MovieHistory actor columns",
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
        help="Fetch each movie's detail page for rows with empty ActorName (no actor-index batch fill)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Backfill: max rows (0 = all)")
    parser.add_argument("--no-proxy", action="store_true", help="Backfill: direct HTTP (debug)")
    parser.add_argument(
        "--use-cf-bypass",
        action="store_true",
        help="Backfill: enable CF bypass on first fetch attempt",
    )
    args = parser.parse_args()

    import utils.infra.db as db_mod

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
                "Schema was not applied (--dry-run). Backfill still runs; ensure DB is already current.",
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
