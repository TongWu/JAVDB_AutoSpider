#!/usr/bin/env python3
"""Standalone migration: align split SQLite DBs with current schema (v9).

The same schema steps run automatically on every ``utils.db.init_db()`` when
any database file's ``SchemaVersion`` is below ``utils.db.SCHEMA_VERSION``.

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
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

import requests  # noqa: E402

from api.parsers.common import javdb_absolute_url, absolutize_supporting_actors_json  # noqa: E402
from utils.config_helper import cfg  # noqa: E402
from utils.logging_config import setup_logging, get_logger  # noqa: E402

setup_logging()
logger = get_logger(__name__)

from utils.db import moviehistory_actor_layout_ok  # noqa: E402

EXPECTED_VERSION = 9


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
    import utils.db as db_mod
    from utils.config_helper import use_sqlite

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
# Parallel actor backfill — one worker per proxy (mirrors ProxyWorker)
# ---------------------------------------------------------------------------


def _requeue_front(q: queue_module.Queue, item) -> None:
    """Put *item* at the front of a Queue so it gets picked up next."""
    with q.mutex:
        q.queue.appendleft(item)
        q.not_empty.notify()


@dataclass
class BackfillTask:
    url: str
    movie_id: int
    href: str
    video_code: str
    entry_index: str
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)


@dataclass
class BackfillResult:
    task: BackfillTask
    actor_name: str
    actor_gender: str
    actor_link: str
    supporting_actors: str
    parse_success: bool
    is_skipped: bool = False


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


def _apply_one_parallel_backfill_result(
    result: BackfillResult,
    *,
    completed_ids: set[int],
    completed_lock: threading.Lock,
    dry_run: bool,
    conn: sqlite3.Connection,
    now_fmt: str,
) -> tuple[int, int, int]:
    """Persist one worker result. Returns ``(processed_delta, failed, skipped)``."""
    task = result.task
    if result.is_skipped:
        return 0, 0, 1
    if not result.parse_success:
        logger.warning(
            "[%s] Failed to get actor for %s (%s)",
            task.entry_index, task.video_code, task.href,
        )
        return 0, 1, 0

    an = result.actor_name.strip()
    ag = result.actor_gender.strip()
    al = result.actor_link.strip()
    sup = result.supporting_actors.strip()
    an, ag, al, sup = _promote_single_female_actor(an, ag, al, sup)
    base_url = cfg('BASE_URL', 'https://javdb.com')
    al = javdb_absolute_url(al, base_url) if al else al
    sup = absolutize_supporting_actors_json(sup, base_url) if sup else sup
    if not an and not al and not sup:
        logger.debug(
            "[%s] Skip UPDATE: empty actor fields (parse_success=%s)",
            task.entry_index,
            result.parse_success,
        )
        return 0, 0, 0

    logger.debug("[%s] %s -> %r %r", task.entry_index, task.video_code, an, al)
    with completed_lock:
        completed_ids.add(task.movie_id)
    if not dry_run:
        conn.execute(
            """UPDATE MovieHistory SET ActorName=?, ActorGender=?, ActorLink=?,
               SupportingActors=?, DateTimeUpdated=? WHERE Id=?""",
            (an, ag, al, sup, now_fmt, task.movie_id),
        )
        conn.commit()
    return 1, 0, 0


def _drain_parallel_result_queue_on_interrupt(
    result_queue: queue_module.Queue,
    *,
    completed_ids: set[int],
    completed_lock: threading.Lock,
    dry_run: bool,
    conn: sqlite3.Connection,
    now_fmt: str,
    phase: str,
) -> tuple[int, int, int]:
    """Apply every ``BackfillResult`` currently waiting on *result_queue* (non-blocking).

    Returns aggregate ``(processed, failed, skipped)`` deltas.
    A second ``KeyboardInterrupt`` during draining stops the drain early.
    """
    dp = df = ds = 0
    drained = 0
    while True:
        try:
            try:
                result: BackfillResult = result_queue.get_nowait()
            except queue_module.Empty:
                break
        except KeyboardInterrupt:
            logger.warning(
                "Second interrupt while draining result queue (%s); stopping flush early",
                phase,
            )
            break
        drained += 1
        p, f, s = _apply_one_parallel_backfill_result(
            result,
            completed_ids=completed_ids,
            completed_lock=completed_lock,
            dry_run=dry_run,
            conn=conn,
            now_fmt=now_fmt,
        )
        dp += p
        df += f
        ds += s
    if drained:
        logger.info(
            "Flushed %d pending backfill result(s) from queue (%s)",
            drained, phase,
        )
    return dp, df, ds


def _drain_task_queue_preserve_tasks(
    q: queue_module.Queue,
    sink: List[BackfillTask],
) -> None:
    """Remove all pending ``BackfillTask`` items from *q* (skip stray ``None``)."""
    while True:
        try:
            item = q.get_nowait()
        except queue_module.Empty:
            break
        if item is not None:
            sink.append(item)


def _parallel_signal_shutdown(
    *,
    stop_event: threading.Event,
    task_queue: queue_module.Queue[BackfillTask | None],
    login_queue: queue_module.Queue[BackfillTask],
    num_workers: int,
    not_started_tasks: List[BackfillTask],
) -> None:
    """Wake workers (interruptible sleep), reclaim queued work, send stop sentinels."""
    stop_event.set()
    _drain_task_queue_preserve_tasks(task_queue, not_started_tasks)
    _drain_task_queue_preserve_tasks(login_queue, not_started_tasks)
    for _ in range(num_workers):
        task_queue.put(None)


_backfill_login_lock = threading.Lock()
_backfill_logged_in_worker_id: int | None = None


class BackfillWorker(threading.Thread):
    """Worker thread bound to a single proxy for actor backfill.

    Architecture mirrors ``scripts.spider.parallel.ProxyWorker``:
    each proxy gets its own ``RequestHandler``, ``MovieSleepManager``,
    and CF-bypass tracking.  Workers share a task queue and result queue;
    the main thread collects results and writes to the DB.
    """

    def __init__(
        self,
        worker_id: int,
        proxy_config: dict,
        task_queue: queue_module.Queue[BackfillTask | None],
        result_queue: queue_module.Queue[BackfillResult],
        login_queue: queue_module.Queue[BackfillTask],
        total_workers: int,
        use_cookie: bool,
        use_cf_bypass: bool,
        movie_sleep_min: float,
        movie_sleep_max: float,
        ban_log_file: str,
        all_workers: list,
        completed_ids: set | None = None,
        completed_lock: threading.Lock | None = None,
        stop_event: threading.Event | None = None,
        shutdown_orphan_tasks: list[BackfillTask] | None = None,
        shutdown_orphan_lock: threading.Lock | None = None,
    ):
        super().__init__(daemon=True, name=f"BackfillWorker-{proxy_config.get('name', worker_id)}")
        self.worker_id = worker_id
        self.proxy_config = proxy_config
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.login_queue = login_queue
        self.total_workers = total_workers
        self.use_cookie = use_cookie
        self.all_workers = all_workers
        self.completed_ids = completed_ids if completed_ids is not None else set()
        self.completed_lock = completed_lock if completed_lock is not None else threading.Lock()
        self._stop_event = stop_event or threading.Event()
        self._shutdown_orphan_tasks = shutdown_orphan_tasks
        self._shutdown_orphan_lock = shutdown_orphan_lock

        self.needs_cf_bypass = use_cf_bypass
        self._first_request = True

        from scripts.spider.sleep_manager import MovieSleepManager
        from utils.proxy_pool import create_proxy_pool_from_config
        from utils.request_handler import RequestHandler, RequestConfig
        from scripts.spider.config_loader import (
            BASE_URL,
            CF_BYPASS_SERVICE_PORT,
            CF_BYPASS_ENABLED,
            CF_TURNSTILE_COOLDOWN,
            FALLBACK_COOLDOWN,
            JAVDB_SESSION_COOKIE,
            PROXY_POOL_COOLDOWN_SECONDS,
            PROXY_POOL_MAX_FAILURES,
            LOGIN_PROXY_NAME,
        )
        self._sleep_mgr = MovieSleepManager(movie_sleep_min, movie_sleep_max)
        self.login_proxy_name: str | None = LOGIN_PROXY_NAME

        self._proxy_pool = create_proxy_pool_from_config(
            [proxy_config],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES,
            ban_log_file=ban_log_file,
        )
        self._handler = RequestHandler(
            proxy_pool=self._proxy_pool,
            config=RequestConfig(
                base_url=BASE_URL,
                cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
                cf_bypass_enabled=CF_BYPASS_ENABLED,
                cf_bypass_max_failures=3,
                cf_turnstile_cooldown=CF_TURNSTILE_COOLDOWN,
                fallback_cooldown=FALLBACK_COOLDOWN,
                javdb_session_cookie=JAVDB_SESSION_COOKIE,
                proxy_http=proxy_config.get('http'),
                proxy_https=proxy_config.get('https'),
                proxy_modules=['all'],
                proxy_mode='single',
            ),
        )

    # -- internal helpers --------------------------------------------------

    def _fetch_html(self, url: str, use_cf: bool) -> str | None:
        return self._handler.get_page(
            url, use_cookie=self.use_cookie, use_proxy=True,
            module_name='spider', max_retries=1, use_cf_bypass=use_cf,
        )

    def _orphan_current_task(self, task: BackfillTask) -> None:
        """Release a claimed task so it can be retried on the next backfill run."""
        if self._shutdown_orphan_tasks is None or self._shutdown_orphan_lock is None:
            return
        with self._shutdown_orphan_lock:
            self._shutdown_orphan_tasks.append(task)

    def _interruptible_movie_sleep(self) -> bool:
        """Sleep like ``MovieSleepManager`` but return True if shutdown was requested."""
        t = self._sleep_mgr.get_sleep_time()
        logger.debug("[%s] Movie sleep: %.1fs (interruptible)", self.proxy_name, t)
        deadline = time.monotonic() + t
        chunk = 0.5
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._stop_event.wait(timeout=min(chunk, remaining)):
                return True
        return False

    def _try_fetch_and_parse(self, task: BackfillTask, use_cf: bool, context: str):
        """Returns ``(actor_name, actor_gender, actor_link, supporting, success, needs_login)``."""
        from utils.parser import parse_detail
        from scripts.spider.session import is_login_page

        logger.debug("[%s] [%s] %s", self.proxy_name, task.entry_index, context)
        try:
            html = self._fetch_html(task.url, use_cf)
            if html:
                if is_login_page(html):
                    logger.warning("[%s] [%s] Login page: %s", self.proxy_name, task.entry_index, context)
                    return '', '', '', '', False, True
                _m, actor_name, actor_gender, actor_link, supporting, ok = parse_detail(
                    html, task.entry_index, skip_sleep=True)
                if ok:
                    return (
                        actor_name or '', actor_gender or '', actor_link or '',
                        supporting or '', True, False,
                    )
                logger.debug("[%s] [%s] parse failed: %s", self.proxy_name, task.entry_index, context)
            else:
                logger.debug("[%s] [%s] no HTML: %s", self.proxy_name, task.entry_index, context)
        except Exception as e:
            logger.debug("[%s] [%s] error in %s: %s", self.proxy_name, task.entry_index, context, e)
        return '', '', '', '', False, False

    def _try_direct_then_cf(self, task: BackfillTask):
        """Returns ``(actor_name, actor_gender, actor_link, supporting, success, used_cf, needs_login)``."""
        if self.needs_cf_bypass:
            an, ag, al, sup, ok, login = self._try_fetch_and_parse(task, True, "CF Bypass (marked)")
            return an, ag, al, sup, ok, True, login

        an, ag, al, sup, ok, login = self._try_fetch_and_parse(task, False, "Direct")
        if ok:
            return an, ag, al, sup, True, False, False
        if login:
            return an, ag, al, sup, False, False, True

        an, ag, al, sup, ok, login = self._try_fetch_and_parse(task, True, "CF Bypass")
        if ok:
            self.needs_cf_bypass = True
            logger.info("[%s] CF Bypass succeeded — marked for runtime", self.proxy_name)
            return an, ag, al, sup, True, True, False
        return '', '', '', '', False, False, login

    def _try_login_refresh(self) -> bool:
        global _backfill_logged_in_worker_id
        import scripts.spider.state as st
        from scripts.spider.session import attempt_login_refresh

        with _backfill_login_lock:
            if self.login_proxy_name and self.proxy_name != self.login_proxy_name:
                return False

            if st.login_attempted:
                if st.refreshed_session_cookie is not None:
                    self._handler.config.javdb_session_cookie = st.refreshed_session_cookie
                    _backfill_logged_in_worker_id = self.worker_id
                    return True
                return False

            if self.login_proxy_name:
                success, new_cookie, _ = attempt_login_refresh(None, None)
            else:
                proxy_for_login = {
                    k: v for k, v in {
                        'http': self.proxy_config.get('http'),
                        'https': self.proxy_config.get('https'),
                    }.items() if v
                } or None

                success, new_cookie, _ = attempt_login_refresh(
                    explicit_proxies=proxy_for_login,
                    explicit_proxy_name=self.proxy_name,
                )
            if success and new_cookie:
                self._handler.config.javdb_session_cookie = new_cookie
                _backfill_logged_in_worker_id = self.worker_id
                return True
            return False

    def _handle_login_required(self, task: BackfillTask):
        global _backfill_logged_in_worker_id
        import scripts.spider.state as st
        from scripts.spider.session import can_attempt_login
        from scripts.spider.parallel_login import should_delegate_login_task

        if should_delegate_login_task(self.login_proxy_name, self.proxy_name):
            with _backfill_login_lock:
                if _backfill_logged_in_worker_id is not None:
                    li_nm = self.all_workers[_backfill_logged_in_worker_id].proxy_name
                    task.failed_proxies.discard(li_nm)
                if self.login_proxy_name:
                    task.failed_proxies.discard(self.login_proxy_name)
            self.login_queue.put(task)
            logger.info(
                "[%s] [%s] Login required for %s, routing to LOGIN_PROXY_NAME worker [%s]",
                self.proxy_name, task.entry_index, task.video_code, self.login_proxy_name,
            )
            return

        with _backfill_login_lock:
            if _backfill_logged_in_worker_id is not None:
                if _backfill_logged_in_worker_id != self.worker_id:
                    logged_in_proxy = self.all_workers[_backfill_logged_in_worker_id].proxy_name
                    task.failed_proxies.discard(logged_in_proxy)
                    self.login_queue.put(task)
                    logger.info(
                        "[%s] [%s] Login required for %s, routing to [%s]",
                        self.proxy_name, task.entry_index, task.video_code, logged_in_proxy,
                    )
                    return
                logger.warning(
                    "[%s] [%s] Own session stale — requeueing %s",
                    self.proxy_name, task.entry_index, task.video_code,
                )
                _backfill_logged_in_worker_id = None
                st.refreshed_session_cookie = None
                st.logged_in_proxy_name = None
                task.failed_proxies.add(self.proxy_name)
                _requeue_front(self.task_queue, task)
                return

        if can_attempt_login(True, is_index_page=False):
            if self._try_login_refresh():
                logger.info("[%s] Logged in, becoming login worker", self.proxy_name)
                self.login_queue.put(task)
                return

        logger.warning("[%s] [%s] Login unavailable, marking failed", self.proxy_name, task.entry_index)
        self.result_queue.put(BackfillResult(
            task=task, actor_name='', actor_gender='', actor_link='',
            supporting_actors='', parse_success=False,
        ))

    def _get_next_task(self) -> BackfillTask | None:
        from scripts.spider.parallel_login import use_login_queue_priority

        while True:
            with _backfill_login_lock:
                am_logged_in = use_login_queue_priority(
                    self.login_proxy_name,
                    self.proxy_name,
                    _backfill_logged_in_worker_id,
                    self.worker_id,
                )

            if am_logged_in:
                try:
                    return self.login_queue.get_nowait()
                except queue_module.Empty:
                    pass

            try:
                return self.task_queue.get(timeout=0.3 if am_logged_in else None)
            except queue_module.Empty:
                continue

    # -- main loop ---------------------------------------------------------

    def run(self):
        while True:
            task = self._get_next_task()
            if task is None:
                break

            with self.completed_lock:
                if task.movie_id in self.completed_ids:
                    self.result_queue.put(BackfillResult(
                        task=task, actor_name='', actor_gender='', actor_link='',
                        supporting_actors='', parse_success=False, is_skipped=True,
                    ))
                    continue

            if self.proxy_name in task.failed_proxies:
                if len(task.failed_proxies) >= self.total_workers:
                    self.result_queue.put(BackfillResult(
                        task=task, actor_name='', actor_gender='', actor_link='',
                        supporting_actors='', parse_success=False,
                    ))
                    continue
                _requeue_front(self.task_queue, task)
                time.sleep(0.1)
                continue

            if not self._first_request:
                if self._interruptible_movie_sleep():
                    self._orphan_current_task(task)
                    continue
            self._first_request = False

            if self._stop_event.is_set():
                self._orphan_current_task(task)
                continue

            an, ag, al, sup, success, used_cf, needs_login = self._try_direct_then_cf(task)
            if success:
                cf_tag = " +CF" if used_cf else ""
                has_actor_row = bool(an.strip() or al.strip() or sup.strip())
                if has_actor_row:
                    logger.info(
                        "[%s] Parsed %s%s [%s]",
                        task.entry_index, task.video_code, cf_tag, self.proxy_name,
                    )
                else:
                    logger.warning(
                        "[%s] Page loaded%s [%s] but actor name/link/supporting are empty "
                        "(parse_detail succeeded on magnets only; CF or HTML may differ from normal). "
                        "%s",
                        task.entry_index, cf_tag, self.proxy_name, task.video_code,
                    )
                self.result_queue.put(BackfillResult(
                    task=task, actor_name=an, actor_gender=ag, actor_link=al,
                    supporting_actors=sup, parse_success=True,
                ))
            elif needs_login:
                self._handle_login_required(task)
            else:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                _requeue_front(self.task_queue, task)
                logger.info(
                    "[%s] [%s] Failed %s, re-queued (%d/%d proxies)",
                    self.proxy_name, task.entry_index, task.video_code,
                    len(task.failed_proxies), self.total_workers,
                )


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
    global _backfill_logged_in_worker_id
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
    from scripts.spider.config_loader import (
        BASE_URL, REPORTS_DIR, PROXY_POOL,
        MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX,
    )
    from scripts.spider.sleep_manager import movie_sleep_mgr

    ban_log_file = os.path.join(REPORTS_DIR, 'proxy_bans.csv')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    use_proxy = not no_proxy
    state.setup_proxy_pool(ban_log_file, use_proxy)
    state.initialize_request_handler()

    conn = sqlite3.connect(history_db)
    conn.row_factory = sqlite3.Row

    sql = (
        "SELECT Id, Href, VideoCode FROM MovieHistory "
        "WHERE ActorName IS NULL OR ActorName = '' "
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
    # Parallel mode: one BackfillWorker per proxy
    # ------------------------------------------------------------------
    if use_proxy and PROXY_POOL:
        _backfill_logged_in_worker_id = None

        completed_ids: set[int] = set()
        completed_lock = threading.Lock()
        stop_event = threading.Event()
        shutdown_orphan_tasks: list[BackfillTask] = []
        shutdown_orphan_lock = threading.Lock()

        task_queue: queue_module.Queue[BackfillTask | None] = queue_module.Queue()
        result_queue: queue_module.Queue[BackfillResult] = queue_module.Queue()
        login_queue: queue_module.Queue[BackfillTask] = queue_module.Queue()

        all_workers: list[BackfillWorker] = []
        for idx, proxy_cfg in enumerate(PROXY_POOL):
            w = BackfillWorker(
                worker_id=idx,
                proxy_config=proxy_cfg,
                task_queue=task_queue,
                result_queue=result_queue,
                login_queue=login_queue,
                total_workers=len(PROXY_POOL),
                use_cookie=True,
                use_cf_bypass=use_cf_bypass,
                movie_sleep_min=movie_sleep_mgr.sleep_min,
                movie_sleep_max=movie_sleep_mgr.sleep_max,
                ban_log_file=ban_log_file,
                all_workers=all_workers,
                completed_ids=completed_ids,
                completed_lock=completed_lock,
                stop_event=stop_event,
                shutdown_orphan_tasks=shutdown_orphan_tasks,
                shutdown_orphan_lock=shutdown_orphan_lock,
            )
            all_workers.append(w)

        for i, row in enumerate(rows, 1):
            task_queue.put(BackfillTask(
                url=urljoin(BASE_URL, row["Href"]),
                movie_id=row["Id"],
                href=row["Href"],
                video_code=row["VideoCode"],
                entry_index=f"backfill-{i}/{total}",
            ))

        logger.info(
            "Starting %d workers for %d backfill tasks (one detail page per row)",
            len(all_workers), total,
        )
        for w in all_workers:
            w.start()

        processed = 0
        failed = 0
        skipped = 0
        results_received = 0
        parallel_interrupted = False

        try:
            while results_received < total:
                result: BackfillResult = result_queue.get()
                results_received += 1
                p, f, s = _apply_one_parallel_backfill_result(
                    result,
                    completed_ids=completed_ids,
                    completed_lock=completed_lock,
                    dry_run=dry_run,
                    conn=conn,
                    now_fmt=now_fmt,
                )
                processed += p
                failed += f
                skipped += s
        except KeyboardInterrupt:
            parallel_interrupted = True
            not_started: list[BackfillTask] = []
            logger.warning(
                "Keyboard interrupt — signalling workers, draining task queues, flushing results …",
            )
            _parallel_signal_shutdown(
                stop_event=stop_event,
                task_queue=task_queue,
                login_queue=login_queue,
                num_workers=len(all_workers),
                not_started_tasks=not_started,
            )
            ep, ef, es = _drain_parallel_result_queue_on_interrupt(
                result_queue,
                completed_ids=completed_ids,
                completed_lock=completed_lock,
                dry_run=dry_run,
                conn=conn,
                now_fmt=now_fmt,
                phase="before worker shutdown",
            )
            processed += ep
            failed += ef
            skipped += es

            for w in all_workers:
                w.join(timeout=30)

            ep2, ef2, es2 = _drain_parallel_result_queue_on_interrupt(
                result_queue,
                completed_ids=completed_ids,
                completed_lock=completed_lock,
                dry_run=dry_run,
                conn=conn,
                now_fmt=now_fmt,
                phase="after worker shutdown",
            )
            processed += ep2
            failed += ef2
            skipped += es2

            with shutdown_orphan_lock:
                n_orphan = len(shutdown_orphan_tasks)
            n_queued = len(not_started)
            logger.info(
                "Backfill interrupted (parallel, %d workers). "
                "Updated: %d, Skipped: %d, Failed: %d — "
                "%d tasks reclaimed from queues, %d released mid-worker (re-run backfill to continue)",
                len(all_workers), processed, skipped, failed, n_queued, n_orphan,
            )
        else:
            for _ in all_workers:
                task_queue.put(None)
            for w in all_workers:
                w.join(timeout=10)

            logger.info(
                "Backfill done (parallel, %d workers). "
                "Updated: %d, Skipped: %d, Failed: %d",
                len(all_workers), processed, skipped, failed,
            )

        conn.close()
        return 130 if parallel_interrupted else 0

    # ------------------------------------------------------------------
    # Sequential fallback (--no-proxy or no PROXY_POOL configured)
    # ------------------------------------------------------------------
    from scripts.spider.fallback import fetch_detail_page_with_fallback

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

            if not an and not al and not sup:
                logger.warning(
                    "[%s] No actor for %s (%s, parse_ok=%s, magnets=%d)",
                    entry_index, video_code, href, parse_ok, len(magnets or []),
                )
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
