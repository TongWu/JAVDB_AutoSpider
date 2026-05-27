# ADR-022 Phase 8 — MovieMetadata Backfill Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a backfill migration tool that iterates over existing `MovieHistory` rows that have no corresponding `MovieMetadata` entry, fetches each movie's detail page from JavDB, and writes the parsed metadata via `MetadataRepo.upsert()`. Wire the tool into the `migrate_to_current.py` CLI and the `Migration.yml` GitHub Actions workflow.

**Architecture:** The tool queries `MovieHistory LEFT JOIN MovieMetadata` to find un-enriched rows, then fetches detail pages in parallel (one worker per proxy, matching the `align_inventory_with_moviehistory.py` pattern) or sequentially. Unlike the align tool, no JavDB search step is needed — `href` is already a direct movie URL. `MetadataRepo.upsert()` writes outside the session flow, so no staging/commit is required. The tool is invoked via `--backfill-metadata` in `migrate_to_current.py`, which the Migration.yml workflow exposes as a dispatch input.

**Tech Stack:** Python 3.11, `javdb.parsing.parse_detail_page`, `MetadataRepo`, `FetchEngine`, `spider_state`, argparse.

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-01](IMP-ADR022-01-db-schema.md) · [IMP-ADR022-02](IMP-ADR022-02-metadata-repo.md)

**Depends on:** IMP-ADR022-01 (`MovieMetadata` table), IMP-ADR022-02 (`MetadataRepo`).

**Blocks:** Nothing — this phase is a leaf.

---

## File Map

**New files:**
- `javdb/migrations/tools/backfill_movie_metadata.py`

**Modified files:**
- `javdb/migrations/migrate_to_current.py` — add `--backfill-metadata` flags and delegation
- `.github/workflows/Migration.yml` — add workflow inputs and CMD block

---

## Task 1 — Create the backfill tool

**Files:**
- Create: `javdb/migrations/tools/backfill_movie_metadata.py`

- [ ] **Step 1: Create the file**

```python
"""Backfill MovieMetadata for existing MovieHistory rows that lack metadata.

For each MovieHistory.Href that has no corresponding MovieMetadata row,
fetches the JavDB detail page and upserts via MetadataRepo.

Parallel mode (one worker per proxy) is used when the proxy pool is
configured and --no-proxy is not set. Writes are outside the
Pending→Commit session flow -- failures are silent and retriable.

Usage (via migrate_to_current.py):
    python3 -m apps.cli.db.migration --backfill-metadata --dry-run
    python3 -m apps.cli.db.migration --backfill-metadata \\
        --backfill-metadata-limit-per-worker 50 --backfill-metadata-shuffle
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[4]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.config import cfg
from javdb.infra.logging import get_logger, setup_logging
from javdb.parsing import parse_detail_page
from javdb.storage.db import get_db, HISTORY_DB_PATH
from javdb.storage.repos.metadata_repo import MetadataRepo
import javdb.spider.runtime.state as spider_state
from javdb.spider.runtime.sleep import movie_sleep_mgr

setup_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_hrefs_without_metadata(
    only_hrefs: Optional[List[str]] = None,
) -> List[str]:
    """Return MovieHistory.Href values that have no MovieMetadata row.

    If *only_hrefs* is given, restrict to that set.
    """
    sql = """
        SELECT mh.Href
        FROM   MovieHistory mh
        LEFT JOIN MovieMetadata mm ON mm.href = mh.Href
        WHERE  mm.href IS NULL
        ORDER  BY mh.DateTimeCreated DESC
    """
    with get_db(HISTORY_DB_PATH) as conn:
        rows = conn.execute(sql).fetchall()
    all_missing = [r[0] for r in rows]

    if only_hrefs:
        only_set = set(only_hrefs)
        return [h for h in all_missing if h in only_set]
    return all_missing


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BackfillResult:
    href: str
    status: str           # 'ok' | 'parse_failed' | 'write_failed' | 'dry_run' | 'all_proxies_failed'
    message: str = ''


# ---------------------------------------------------------------------------
# Per-task process function (used by FetchEngine workers)
# ---------------------------------------------------------------------------

def _make_backfill_process_fn(dry_run: bool):
    """Return a process_fn compatible with FetchEngine.

    process_fn(html: str, task: EngineTask) -> dict | None
    Returns None to signal the engine to retry on another proxy.
    """
    def process_fn(html: str, task) -> Optional[dict]:
        href = task.meta['href']
        try:
            detail = parse_detail_page(html)
        except Exception as exc:
            logger.warning("[%s] parse_detail_page failed: %s", task.entry_index, exc)
            return None  # retry

        if not detail.parse_success:
            logger.debug("[%s] parse_success=False for %s", task.entry_index, href)
            return {'status': 'parse_failed', 'href': href}

        if not dry_run:
            try:
                MetadataRepo().upsert(href, detail.__dict__)
            except Exception as exc:
                logger.warning(
                    "[%s] MetadataRepo.upsert failed for %s: %s",
                    task.entry_index, href, exc,
                )
                return {'status': 'write_failed', 'href': href, 'message': str(exc)}

        return {'status': 'ok' if not dry_run else 'dry_run', 'href': href}

    return process_fn


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backfill_metadata(args: SimpleNamespace) -> int:
    """Run the MovieMetadata backfill.

    Args:
        args: Namespace with fields:
            dry_run (bool)
            limit (int)            — absolute cap, 0=all
            limit_per_worker (int) — per-proxy worker cap, 0=use limit
            hrefs (str)            — comma-separated href overrides, '' = all
            use_proxy (bool)
            shuffle (bool)

    Returns:
        0 on success, 1 on partial failure (some hrefs failed).
    """
    only_hrefs: Optional[List[str]] = None
    if args.hrefs:
        only_hrefs = [h.strip() for h in args.hrefs.split(',') if h.strip()]

    hrefs = _load_hrefs_without_metadata(only_hrefs)

    if args.shuffle:
        random.shuffle(hrefs)

    # Apply absolute limit
    limit = int(getattr(args, 'limit', 0) or 0)
    limit_per_worker = int(getattr(args, 'limit_per_worker', 0) or 0)
    use_proxy = getattr(args, 'use_proxy', True)

    if limit_per_worker > 0:
        from javdb.spider.runtime.config import PROXY_POOL
        num_workers = len(PROXY_POOL) if (use_proxy and PROXY_POOL) else 1
        effective_limit = limit_per_worker * num_workers
        hrefs = hrefs[:effective_limit]
    elif limit > 0:
        hrefs = hrefs[:limit]

    total = len(hrefs)
    logger.info(
        "MovieMetadata backfill: %d hrefs to process%s",
        total,
        " (dry-run)" if args.dry_run else "",
    )
    if total == 0:
        logger.info("Nothing to backfill — all MovieHistory rows already have metadata.")
        return 0

    spider_state.setup_proxy_pool(use_proxy=use_proxy)
    spider_state.initialize_request_handler()
    base_url = cfg('BASE_URL', 'https://javdb.com').rstrip('/')

    results: List[BackfillResult] = []
    process_fn = _make_backfill_process_fn(args.dry_run)

    from javdb.spider.runtime.config import PROXY_POOL

    # ------------------------------------------------------------------
    # Parallel mode
    # ------------------------------------------------------------------
    if use_proxy and PROXY_POOL:
        from javdb.spider.fetch.fetch_engine import FetchEngine

        movie_sleep_mgr.apply_volume_multiplier(total, num_workers=len(PROXY_POOL))
        stop_event = threading.Event()

        engine = FetchEngine(
            process_fn=process_fn,
            use_cookie=True,
            stop_event=stop_event,
            sleep_min=movie_sleep_mgr.base_min,
            sleep_max=movie_sleep_mgr.base_max,
            per_worker_task_limit=limit_per_worker if limit_per_worker > 0 else 0,
        )
        engine.start()

        for i, href in enumerate(hrefs, 1):
            detail_url = base_url + href
            engine.submit(
                detail_url,
                entry_index=f"meta-{i}/{total}",
                meta={'href': href},
            )
        engine.mark_done()

        logger.info(
            "Starting %d workers for %d metadata backfill tasks",
            len(engine._workers), total,
        )

        ok = failed = skipped = 0
        for engine_result in engine.iter_results():
            href = engine_result.task.meta['href']
            idx = engine_result.task.entry_index

            if not engine_result.success:
                logger.warning("[%s] All proxies failed for %s", idx, href)
                results.append(BackfillResult(href=href, status='all_proxies_failed'))
                failed += 1
                continue

            data = engine_result.data
            status = data.get('status', 'ok')
            results.append(BackfillResult(href=href, status=status, message=data.get('message', '')))

            if status == 'ok':
                logger.info("[%s] ✓ %s", idx, href)
                ok += 1
            elif status == 'dry_run':
                logger.info("[%s] (dry-run) %s", idx, href)
                ok += 1
            else:
                logger.warning("[%s] %s — %s: %s", idx, href, status, data.get('message', ''))
                failed += 1

        engine.stop()

    # ------------------------------------------------------------------
    # Sequential mode
    # ------------------------------------------------------------------
    else:
        import requests as _requests
        from javdb.infra.request import get_page_url

        session = _requests.Session()
        ok = failed = 0

        for i, href in enumerate(hrefs, 1):
            idx = f"meta-{i}/{total}"
            detail_url = base_url + href
            try:
                html = get_page_url(detail_url, session, use_proxy=False)
            except Exception as exc:
                logger.warning("[%s] Fetch failed for %s: %s", idx, href, exc)
                results.append(BackfillResult(href=href, status='all_proxies_failed', message=str(exc)))
                failed += 1
                continue

            class _FakeTask:
                entry_index = idx
                meta = {'href': href}

            result = process_fn(html, _FakeTask())
            if result is None:
                results.append(BackfillResult(href=href, status='parse_failed'))
                failed += 1
                logger.warning("[%s] parse failed for %s", idx, href)
            else:
                status = result.get('status', 'ok')
                results.append(BackfillResult(href=href, status=status, message=result.get('message', '')))
                if status in ('ok', 'dry_run'):
                    logger.info("[%s] ✓ %s", idx, href)
                    ok += 1
                else:
                    logger.warning("[%s] %s — %s", idx, href, status)
                    failed += 1

    logger.info(
        "MovieMetadata backfill complete: %d ok, %d failed out of %d",
        ok, failed, total,
    )
    return 0 if failed == 0 else 1


def parse_args() -> SimpleNamespace:
    parser = argparse.ArgumentParser(
        description="Backfill MovieMetadata for MovieHistory rows that lack metadata."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse but do not write to DB.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max hrefs to process in total (0=all). "
                             "Ignored when --limit-per-worker > 0.")
    parser.add_argument("--limit-per-worker", type=int, default=0,
                        dest="limit_per_worker",
                        help="Max completed tasks per proxy worker (0=use --limit or all).")
    parser.add_argument("--hrefs", type=str, default='',
                        help="Comma-separated movie hrefs to process (default: all missing).")
    parser.add_argument("--no-proxy", action="store_true",
                        help="Direct HTTP without proxy (debug).")
    parser.add_argument("--shuffle", action="store_true",
                        help="Randomise processing order.")
    args = parser.parse_args()
    return SimpleNamespace(
        dry_run=args.dry_run,
        limit=args.limit,
        limit_per_worker=args.limit_per_worker,
        hrefs=args.hrefs,
        use_proxy=not args.no_proxy,
        shuffle=args.shuffle,
    )


if __name__ == "__main__":
    raise SystemExit(run_backfill_metadata(parse_args()))
```

- [ ] **Step 2: Verify the script imports cleanly**

```bash
python3 -c "
from javdb.migrations.tools.backfill_movie_metadata import run_backfill_metadata, _load_hrefs_without_metadata
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Verify dry-run completes without errors**

```bash
python3 -m javdb.migrations.tools.backfill_movie_metadata --dry-run --limit 5 --no-proxy
```

Expected: logs `MovieMetadata backfill: N hrefs to process (dry-run)` and exits 0.

- [ ] **Step 4: Commit**

```bash
git add javdb/migrations/tools/backfill_movie_metadata.py
git commit -m "feat(migrations): add MovieMetadata backfill tool (ADR-022)"
```

---

## Task 2 — Wire into migrate_to_current.py

**Files:**
- Modify: `javdb/migrations/migrate_to_current.py`

- [ ] **Step 1: Add the import**

Near the top of `migrate_to_current.py`, alongside the other tool imports (search for `from javdb.migrations.tools` or `run_alignment`), add:

```python
from javdb.migrations.tools.backfill_movie_metadata import run_backfill_metadata
```

- [ ] **Step 2: Add argparse flags**

In the `main()` function's `argparse` block, after the existing `--align-*` arguments, add:

```python
    parser.add_argument(
        "--backfill-metadata",
        action="store_true",
        help="Backfill MovieMetadata for MovieHistory rows that lack metadata.",
    )
    parser.add_argument(
        "--backfill-metadata-limit",
        type=int,
        default=0,
        dest="backfill_metadata_limit",
        help="Metadata backfill: absolute max hrefs (0=all). "
             "Ignored when --backfill-metadata-limit-per-worker > 0.",
    )
    parser.add_argument(
        "--backfill-metadata-limit-per-worker",
        type=int,
        default=0,
        dest="backfill_metadata_limit_per_worker",
        help="Metadata backfill: max completed tasks per proxy worker (0=use --backfill-metadata-limit or all).",
    )
    parser.add_argument(
        "--backfill-metadata-hrefs",
        type=str,
        default='',
        dest="backfill_metadata_hrefs",
        help="Metadata backfill: comma-separated movie hrefs to process (default: all missing).",
    )
    parser.add_argument(
        "--backfill-metadata-no-proxy",
        action="store_true",
        dest="backfill_metadata_no_proxy",
        help="Metadata backfill: direct HTTP without proxy (debug).",
    )
    parser.add_argument(
        "--backfill-metadata-shuffle",
        action="store_true",
        dest="backfill_metadata_shuffle",
        help="Metadata backfill: randomise processing order.",
    )
```

- [ ] **Step 3: Add delegation block**

After the existing `if args.align_inventory_history:` block (and its `return arc` line), add:

```python
    if args.backfill_metadata:
        meta_ns = SimpleNamespace(
            dry_run=args.dry_run,
            limit=args.backfill_metadata_limit,
            limit_per_worker=args.backfill_metadata_limit_per_worker,
            hrefs=args.backfill_metadata_hrefs,
            use_proxy=not args.backfill_metadata_no_proxy,
            shuffle=args.backfill_metadata_shuffle,
        )
        mrc = run_backfill_metadata(meta_ns)
        if mrc != 0:
            return mrc
```

Confirm that `SimpleNamespace` is already imported in `migrate_to_current.py` (it is used for the align block). If not, add `from types import SimpleNamespace` near the top.

- [ ] **Step 4: Verify the flag is registered**

```bash
python3 -m apps.cli.db.migration --help | grep backfill-metadata
```

Expected: at least one line matching `--backfill-metadata`.

- [ ] **Step 5: Verify dry-run via the canonical entrypoint**

```bash
python3 -m apps.cli.db.migration \
  --skip-schema \
  --backfill-metadata \
  --backfill-metadata-limit 3 \
  --backfill-metadata-no-proxy \
  --dry-run
```

Expected: exits 0; logs show `MovieMetadata backfill: N hrefs to process (dry-run)`.

- [ ] **Step 6: Commit**

```bash
git add javdb/migrations/migrate_to_current.py
git commit -m "feat(migrations): wire --backfill-metadata into migrate_to_current (ADR-022)"
```

---

## Task 3 — Add workflow inputs and CMD block to Migration.yml

**Files:**
- Modify: `.github/workflows/Migration.yml`

- [ ] **Step 1: Add workflow inputs**

In the `workflow_dispatch.inputs:` section, after the existing `align_*` input block, add the following inputs (follow the exact YAML indentation of the surrounding inputs):

```yaml
      backfill_metadata:
        description: 'Backfill MovieMetadata for MovieHistory rows that lack metadata'
        required: false
        type: boolean
        default: false

      backfill_metadata_limit:
        description: 'Metadata backfill: absolute max hrefs (0=all; ignored when limit_per_worker > 0)'
        required: false
        type: string
        default: '0'

      backfill_metadata_limit_per_worker:
        description: 'Metadata backfill: max completed tasks per proxy worker (0=use limit or all)'
        required: false
        type: string
        default: '0'

      backfill_metadata_hrefs:
        description: 'Metadata backfill: comma-separated movie hrefs to process (blank=all missing)'
        required: false
        type: string
        default: ''

      backfill_metadata_no_proxy:
        description: 'Metadata backfill: direct HTTP without proxy (debug)'
        required: false
        type: boolean
        default: false

      backfill_metadata_shuffle:
        description: 'Metadata backfill: randomise processing order (random pick behaviour)'
        required: false
        type: boolean
        default: false
```

- [ ] **Step 2: Expose inputs as env vars**

In the `env:` block of the "Run migration" step (where `INPUT_DRY_RUN`, `INPUT_ALIGN_*` etc. are set), add:

```yaml
          INPUT_BACKFILL_METADATA: ${{ inputs.backfill_metadata }}
          INPUT_BACKFILL_METADATA_LIMIT: ${{ inputs.backfill_metadata_limit }}
          INPUT_BACKFILL_METADATA_LIMIT_PER_WORKER: ${{ inputs.backfill_metadata_limit_per_worker }}
          INPUT_BACKFILL_METADATA_HREFS: ${{ inputs.backfill_metadata_hrefs }}
          INPUT_BACKFILL_METADATA_NO_PROXY: ${{ inputs.backfill_metadata_no_proxy }}
          INPUT_BACKFILL_METADATA_SHUFFLE: ${{ inputs.backfill_metadata_shuffle }}
```

- [ ] **Step 3: Add CMD construction block**

In the `run:` script of the "Run migration" step, after the existing `if [ "$INPUT_ALIGN_INVENTORY_HISTORY" = "true" ]; then ... fi` block, add:

```bash
          if [ "$INPUT_BACKFILL_METADATA" = "true" ]; then
            CMD+=(--backfill-metadata)

            META_LIMIT_PW="$INPUT_BACKFILL_METADATA_LIMIT_PER_WORKER"
            if [ -n "$META_LIMIT_PW" ] && [ "$META_LIMIT_PW" != "0" ]; then
              CMD+=(--backfill-metadata-limit-per-worker "$META_LIMIT_PW")
            fi

            META_LIMIT="$INPUT_BACKFILL_METADATA_LIMIT"
            if [ -n "$META_LIMIT" ] && [ "$META_LIMIT" != "0" ]; then
              CMD+=(--backfill-metadata-limit "$META_LIMIT")
            fi

            META_HREFS="$INPUT_BACKFILL_METADATA_HREFS"
            if [ -n "$META_HREFS" ]; then
              CMD+=(--backfill-metadata-hrefs "$META_HREFS")
            fi

            if [ "$INPUT_BACKFILL_METADATA_NO_PROXY" = "true" ]; then
              CMD+=(--backfill-metadata-no-proxy)
            fi

            if [ "$INPUT_BACKFILL_METADATA_SHUFFLE" = "true" ]; then
              CMD+=(--backfill-metadata-shuffle)
            fi
          fi
```

- [ ] **Step 4: Add to Display parameters step**

In the "Display parameters" step (`echo` block), add display lines for the new inputs after the align parameters:

```bash
          echo "backfill_metadata=${{ inputs.backfill_metadata }}"
          echo "backfill_metadata_limit=${{ inputs.backfill_metadata_limit }}"
          echo "backfill_metadata_limit_per_worker=${{ inputs.backfill_metadata_limit_per_worker }}"
          echo "backfill_metadata_hrefs=${{ inputs.backfill_metadata_hrefs }}"
          echo "backfill_metadata_no_proxy=${{ inputs.backfill_metadata_no_proxy }}"
          echo "backfill_metadata_shuffle=${{ inputs.backfill_metadata_shuffle }}"
```

- [ ] **Step 5: Validate YAML syntax**

```bash
python3 -c "
import yaml
with open('.github/workflows/Migration.yml') as f:
    yaml.safe_load(f)
print('YAML valid')
"
```

Expected: `YAML valid`

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/Migration.yml
git commit -m "ci(migrations): add backfill-metadata inputs and CMD block to Migration workflow (ADR-022)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Tool imports cleanly | `python3 -c "from javdb.migrations.tools.backfill_movie_metadata import run_backfill_metadata; print('OK')"` → `OK` |
| 2 | Dry-run via tool directly | `python3 -m javdb.migrations.tools.backfill_movie_metadata --dry-run --limit 5 --no-proxy` → exits 0 |
| 3 | Flag registered in canonical CLI | `python3 -m apps.cli.db.migration --help \| grep backfill-metadata` → matches |
| 4 | Dry-run via canonical CLI | `python3 -m apps.cli.db.migration --skip-schema --backfill-metadata --backfill-metadata-limit 3 --backfill-metadata-no-proxy --dry-run` → exits 0 |
| 5 | Migration.yml YAML valid | `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/Migration.yml'))"` → no error |
| 6 | Workflow dispatch shows new inputs | GitHub → Actions → Migration workflow → "Run workflow" dropdown shows `backfill_metadata` inputs |
| 7 | Real run writes metadata rows | Run without `--dry-run` on 5 hrefs → `SELECT COUNT(*) FROM MovieMetadata` on D1 increases by up to 5 |
