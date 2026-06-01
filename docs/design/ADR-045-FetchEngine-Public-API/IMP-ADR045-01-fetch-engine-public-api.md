# FetchEngine Reusable Public API — Implementation Plan

**Status:** Completed — implemented and locally verified on 2026-06-01.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** Parent ADR — [ADR-045](ADR-045-fetch-engine-public-api.md) (FetchEngine Reusable Public API Hardening). This is Phase 1 (the only planned phase).

**Goal:** Add two small public methods to `FetchEngine` (`drain_remaining()`, `run()`), route the metadata backfill through `FetchEngine.simple` so failed fetches re-queue to other proxies, and remove the private `_result_queue` access from the two existing migration callers.

**Architecture:** `FetchEngine`/`ParallelFetchBackend` is already a public, external-callable parallel fetch API (spider + two migration tools use it). We harden it: a public `drain_remaining()` replaces the private `engine._result_queue.get_nowait()` interrupt-salvage loop; a thin `run(tasks)` generator owns the happy-path lifecycle. `backfill_movie_metadata` gains a parallel branch (`FetchEngine.simple`, auto-login, DB writes in the main results loop) while keeping its existing sequential loop as the `--no-proxy` / no-pool fallback — mirroring `migrate_v7_to_v8` and `align_inventory_with_moviehistory`.

**Tech Stack:** Python 3, `pytest`, `unittest.mock`; existing `javdb.spider.fetch.fetch_engine`, `javdb.migrations.tools.*`, `javdb.storage.repos.metadata_repo`.

**Conventions:** Work on a feature branch (do **not** commit to `main`). Commits follow Conventional Commits (`feat(...)`, `refactor(...)`, `test(...)`, `docs(...)`); append the project's `Co-Authored-By` footer. Git identity: Ted / ted@wu.engineer.

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `javdb/spider/fetch/fetch_engine.py` | Add `drain_remaining()` + `run()` to `ParallelFetchBackend`; expose both on the `FetchEngine` facade; add `Iterable` import; fix stale `scripts.*` docstrings (D8) | Modify |
| `javdb/migrations/tools/backfill_movie_metadata.py` | Add parallel `FetchEngine.simple` branch + `_backfill_metadata_parse` + `_apply_metadata_result`; keep sequential fallback; fix stale comment (D8) | Modify |
| `javdb/migrations/tools/migrate_v7_to_v8.py` | Replace private `_result_queue.get_nowait()` loop with `engine.drain_remaining()` | Modify |
| `javdb/migrations/tools/align_inventory_with_moviehistory.py` | Replace private `_result_queue.get_nowait()` loop with `engine.drain_remaining()` | Modify |
| `tests/unit/test_engine.py` | New tests for `drain_remaining()` and `run()` | Modify |
| `tests/unit/test_backfill_movie_metadata_fetch.py` | Append tests for `_backfill_metadata_parse` + `_apply_metadata_result` (parallel helpers). The file's existing `_process_href` / `run_backfill_metadata` tests (all `use_proxy=False`) are the sequential-path regression guard and must stay green | Modify |

**No changes** to `.github/workflows/Migration.yml` (CLI surface unchanged: same `--backfill-metadata`, `--limit`, `--limit-per-worker`, `--no-proxy`, `--shuffle` flags). **No** handbook change required: `docs/handbook/en/developer/cli-reference.md` documents `--backfill-actors` only and never documented `--backfill-metadata`; the behaviour change (auto-login, parallel) is captured in ADR-045, not in any user-facing CLI doc. (See Task 6 Step 5 for the explicit decision record.)

---

## Task 1: Public `drain_remaining()` on `ParallelFetchBackend` + facade

**Files:**
- Modify: `javdb/spider/fetch/fetch_engine.py` (add method after `shutdown()` ~line 1719; add facade method after `shutdown()` ~line 1911)
- Test: `tests/unit/test_engine.py` (append a new test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_engine.py`:

```python
class TestDrainRemaining:

    def test_drain_remaining_yields_queued_results(self):
        from javdb.spider.fetch.fetch_engine import (
            ParallelFetchBackend, EngineTask, EngineResult, FetchRuntimeState,
        )

        backend = ParallelFetchBackend(
            process_fn=lambda ctx, task: None,
            runtime_state=FetchRuntimeState(use_proxy=False, use_cf_bypass=False),
        )
        t = EngineTask(url='https://javdb.com/v/a', entry_index='1')
        backend._result_queue.put(EngineResult(task=t, success=True, data={'x': 1}))
        backend._result_queue.put(EngineResult(task=t, success=False, error='boom'))

        drained = list(backend.drain_remaining())

        assert [r.success for r in drained] == [True, False]
        assert drained[0].data == {'x': 1}
        assert drained[1].error == 'boom'

    def test_drain_remaining_empty_when_no_results(self):
        from javdb.spider.fetch.fetch_engine import (
            ParallelFetchBackend, FetchRuntimeState,
        )

        backend = ParallelFetchBackend(
            process_fn=lambda ctx, task: None,
            runtime_state=FetchRuntimeState(use_proxy=False, use_cf_bypass=False),
        )

        assert list(backend.drain_remaining()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_engine.py::TestDrainRemaining -v`
Expected: FAIL with `AttributeError: 'ParallelFetchBackend' object has no attribute 'drain_remaining'`.

- [ ] **Step 3: Implement `drain_remaining()` on `ParallelFetchBackend`**

In `javdb/spider/fetch/fetch_engine.py`, immediately **after** the `shutdown()` method (the `return orphaned` at ~line 1719) and **before** `runtime_state()`, insert:

```python
    def drain_remaining(self) -> Iterator[EngineResult]:
        """Yield results already produced by workers, non-blocking.

        Intended to be called **after** :meth:`shutdown` — once workers are
        joined no new results can race in — to salvage results that workers
        had produced but the caller had not yet consumed (e.g. partial
        progress after a ``KeyboardInterrupt``). Drains only the result
        queue; tasks that never ran are returned by :meth:`shutdown` as
        ``orphaned`` and are **not** yielded here.
        """
        while True:
            try:
                result = self._result_queue.get_nowait()
            except queue_module.Empty:
                return
            with self._count_lock:
                self._received += 1
            yield result
```

- [ ] **Step 4: Expose `drain_remaining()` on the `FetchEngine` facade**

In the `FetchEngine` facade class, after the `shutdown()` method (~line 1911), insert:

```python
    def drain_remaining(self) -> Iterator[EngineResult]:
        return self._backend.drain_remaining()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/test_engine.py::TestDrainRemaining -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/fetch/fetch_engine.py tests/unit/test_engine.py
git commit -m "feat(spider): add public FetchEngine.drain_remaining()"
```

---

## Task 2: Thin `run(tasks)` on `ParallelFetchBackend` + facade

**Files:**
- Modify: `javdb/spider/fetch/fetch_engine.py` (add `Iterable` to the typing import at line 47; add `run()` after `drain_remaining()`; add facade `run()` after facade `drain_remaining()`)
- Test: `tests/unit/test_engine.py` (append a new test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_engine.py`:

```python
class TestRunLifecycle:

    def test_run_drives_lifecycle_in_order(self):
        from javdb.spider.fetch.fetch_engine import (
            ParallelFetchBackend, EngineTask, EngineResult, FetchRuntimeState,
        )

        backend = ParallelFetchBackend(
            process_fn=lambda ctx, task: None,
            runtime_state=FetchRuntimeState(use_proxy=False, use_cf_bypass=False),
        )
        calls = []
        backend.start = lambda: calls.append('start')
        backend.submit_task = lambda task: calls.append(('submit', task.url))
        backend.mark_done = lambda: calls.append('mark_done')
        backend.shutdown = lambda **_kw: (calls.append('shutdown'), [])[1]
        backend.results = lambda: iter(
            [EngineResult(task=EngineTask(url='x'), success=True)]
        )

        tasks = [EngineTask(url='a'), EngineTask(url='b')]
        results = list(backend.run(tasks))

        assert calls == ['start', ('submit', 'a'), ('submit', 'b'),
                         'mark_done', 'shutdown']
        assert len(results) == 1 and results[0].success is True

    def test_run_shuts_down_even_when_results_raises(self):
        import pytest
        from javdb.spider.fetch.fetch_engine import (
            ParallelFetchBackend, EngineTask, FetchRuntimeState,
        )

        backend = ParallelFetchBackend(
            process_fn=lambda ctx, task: None,
            runtime_state=FetchRuntimeState(use_proxy=False, use_cf_bypass=False),
        )
        calls = []
        backend.start = lambda: None
        backend.submit_task = lambda task: None
        backend.mark_done = lambda: None
        backend.shutdown = lambda **_kw: (calls.append('shutdown'), [])[1]

        def _boom():
            raise RuntimeError('boom')
            yield  # pragma: no cover — makes _boom a generator

        backend.results = _boom

        with pytest.raises(RuntimeError, match='boom'):
            list(backend.run([EngineTask(url='a')]))

        assert calls == ['shutdown']

    def test_facade_run_forwards_to_backend(self):
        from javdb.spider.fetch.fetch_engine import FetchEngine, EngineTask

        engine = FetchEngine.__new__(FetchEngine)
        engine._backend = MagicMock()
        engine._backend.run.return_value = iter(['r1', 'r2'])

        tasks = [EngineTask(url='u')]
        out = list(engine.run(tasks))

        engine._backend.run.assert_called_once_with(tasks)
        assert out == ['r1', 'r2']
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_engine.py::TestRunLifecycle -v`
Expected: FAIL with `AttributeError: 'ParallelFetchBackend' object has no attribute 'run'`.

- [ ] **Step 3: Add the `Iterable` import**

In `javdb/spider/fetch/fetch_engine.py` line 47, change:

```python
from typing import Any, Callable, Iterator, List, Optional, Union
```

to:

```python
from typing import Any, Callable, Iterable, Iterator, List, Optional, Union
```

- [ ] **Step 4: Implement `run()` on `ParallelFetchBackend`**

Immediately after the `drain_remaining()` method added in Task 1, insert:

```python
    def run(self, tasks: Iterable[EngineTask]) -> Iterator[EngineResult]:
        """Own the full lifecycle for a finite task list (happy-path helper).

        Starts the engine, submits every task, marks done, yields results, and
        shuts down in a ``finally``. This is **not** the interrupt-salvage
        path: if ``KeyboardInterrupt`` lands in the caller's loop body Python
        raises ``GeneratorExit`` here and the buffered results cannot be
        re-yielded. Callers that must salvage partial progress on interrupt
        should drive the explicit lifecycle (``start`` / ``submit_task`` /
        ``mark_done`` / ``results``) and call :meth:`drain_remaining` in their
        ``except`` block instead.
        """
        self.start()
        for task in tasks:
            self.submit_task(task)
        self.mark_done()
        try:
            for result in self.results():
                yield result
        finally:
            self.shutdown()
```

- [ ] **Step 5: Expose `run()` on the `FetchEngine` facade**

After the facade `drain_remaining()` added in Task 1, insert:

```python
    def run(self, tasks: Iterable[EngineTask]) -> Iterator[EngineResult]:
        return self._backend.run(tasks)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/unit/test_engine.py::TestRunLifecycle -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add javdb/spider/fetch/fetch_engine.py tests/unit/test_engine.py
git commit -m "feat(spider): add thin FetchEngine.run() happy-path lifecycle helper"
```

---

## Task 3: Replace private `_result_queue` access in the two existing tools

**Files:**
- Modify: `javdb/migrations/tools/migrate_v7_to_v8.py:530-548`
- Modify: `javdb/migrations/tools/align_inventory_with_moviehistory.py:861-866`

- [ ] **Step 1: Update `migrate_v7_to_v8.py`**

Replace the drain loop (current lines 530-548):

```python
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
```

with:

```python
            drained = 0
            for result in engine.drain_remaining():
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
```

- [ ] **Step 2: Update `align_inventory_with_moviehistory.py`**

Replace the drain loop (current lines 861-866):

```python
            while True:
                try:
                    result = engine._result_queue.get_nowait()
                except queue_module.Empty:
                    break
                _apply_align_result(result)
```

with:

```python
            for result in engine.drain_remaining():
                _apply_align_result(result)
```

- [ ] **Step 3: Verify no production code touches the private queue anymore**

Run: `grep -rn '\._result_queue' --include='*.py' javdb/ apps/`
Expected: **no output** (the only `_result_queue` references now live inside `fetch_engine.py` itself, which this grep over `javdb/` will still show — confirm the only matches are within `javdb/spider/fetch/fetch_engine.py`, and **none** in `javdb/migrations/`).

Run: `grep -rn '\._result_queue' javdb/migrations/`
Expected: **no output**.

- [ ] **Step 4: Check for now-unused `queue_module` imports**

Run: `grep -nE 'queue_module|import queue' javdb/migrations/tools/migrate_v7_to_v8.py javdb/migrations/tools/align_inventory_with_moviehistory.py`
If `queue_module` (or `queue`) is no longer referenced anywhere else in a file, remove its import in that file (orphan cleanup from this change only). If it is still used elsewhere, leave it.

- [ ] **Step 5: Run the affected tools' tests + the engine drain test**

Run: `pytest tests/unit/test_engine.py::TestDrainRemaining -q && pytest tests/unit -k "migrate or align or backfill" -q`
Expected: PASS (no failures). If a referenced test module does not exist, that filter simply matches fewer tests — not a failure.

- [ ] **Step 6: Commit**

```bash
git add javdb/migrations/tools/migrate_v7_to_v8.py javdb/migrations/tools/align_inventory_with_moviehistory.py
git commit -m "refactor(db): use public FetchEngine.drain_remaining() in migration tools"
```

---

## Task 4: Route `backfill_movie_metadata` through `FetchEngine.simple`

**Files:**
- Modify: `javdb/migrations/tools/backfill_movie_metadata.py`
- Test: `tests/unit/test_backfill_movie_metadata_fetch.py` (append parallel-helper tests; existing tests are the sequential-path regression guard)

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/unit/test_backfill_movie_metadata_fetch.py` (matches the file's existing `monkeypatch` + `types.SimpleNamespace` + `bm.` idioms; `import types` / `import javdb.migrations.tools.backfill_movie_metadata as bm` are already at the top of the file):

```python
# ---------------------------------------------------------------------------
# Parallel (FetchEngine) helpers — _backfill_metadata_parse / _apply_metadata_result
# ---------------------------------------------------------------------------

def test_backfill_metadata_parse_returns_detail_on_video_code(monkeypatch):
    detail = types.SimpleNamespace(video_code='ABC-123', title='')
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)
    assert bm._backfill_metadata_parse('<html>ok</html>', task=None) is detail


def test_backfill_metadata_parse_returns_detail_on_title(monkeypatch):
    detail = types.SimpleNamespace(video_code='', title='Some Title')
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)
    assert bm._backfill_metadata_parse('<html>ok</html>', task=None) is detail


def test_backfill_metadata_parse_returns_none_when_empty(monkeypatch):
    detail = types.SimpleNamespace(video_code='', title='')
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)
    assert bm._backfill_metadata_parse('<html>empty</html>', task=None) is None


def _make_engine_result(*, success, href, data=None, error=None,
                        entry_index='meta-1/1'):
    task = types.SimpleNamespace(meta={'href': href}, entry_index=entry_index)
    return types.SimpleNamespace(success=success, data=data, error=error, task=task)


def test_apply_metadata_result_success_upserts(monkeypatch):
    detail = types.SimpleNamespace(video_code='ABC-123')
    result = _make_engine_result(success=True, href='https://javdb.com/v/a', data=detail)
    upserts = {}
    monkeypatch.setattr(
        bm.MetadataRepo, 'upsert',
        lambda self, href, d: upserts.update(href=href, detail=d),
    )
    ok, failed = bm._apply_metadata_result(result, dry_run=False)
    assert (ok, failed) == (1, 0)
    assert upserts == {'href': 'https://javdb.com/v/a', 'detail': detail}


def test_apply_metadata_result_dry_run_skips_write(monkeypatch):
    result = _make_engine_result(success=True, href='https://javdb.com/v/a',
                                data=types.SimpleNamespace(video_code='X'))

    def _boom(self, href, d):
        raise AssertionError('dry-run must not upsert')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _boom)
    ok, failed = bm._apply_metadata_result(result, dry_run=True)
    assert (ok, failed) == (1, 0)


def test_apply_metadata_result_failure_counts_failed(monkeypatch):
    result = _make_engine_result(success=False, href='https://javdb.com/v/a',
                                error='all_proxies_failed')

    def _boom(self, href, d):
        raise AssertionError('failed result must not upsert')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _boom)
    ok, failed = bm._apply_metadata_result(result, dry_run=False)
    assert (ok, failed) == (0, 1)


def test_apply_metadata_result_write_failure_counts_failed(monkeypatch):
    result = _make_engine_result(success=True, href='https://javdb.com/v/a',
                                data=types.SimpleNamespace(video_code='X'))

    def _raise(self, href, d):
        raise RuntimeError('db down')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _raise)
    ok, failed = bm._apply_metadata_result(result, dry_run=False)
    assert (ok, failed) == (0, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_backfill_movie_metadata_fetch.py -k "parse_returns or apply_metadata" -v`
Expected: FAIL with `AttributeError: module 'javdb.migrations.tools.backfill_movie_metadata' has no attribute '_backfill_metadata_parse'` (and `_apply_metadata_result`).

- [ ] **Step 3: Add the two module-level helpers**

In `javdb/migrations/tools/backfill_movie_metadata.py`, after `_process_href` (ends at line 210) and before the `# Main entry point` banner, insert:

```python
# ---------------------------------------------------------------------------
# Parallel (FetchEngine) helpers
# ---------------------------------------------------------------------------

def _backfill_metadata_parse(html, task):
    """``FetchEngine.simple`` parse_fn: parse a detail page for metadata.

    Returns the parsed detail object on success, or ``None`` to let the engine
    re-queue the task to a different proxy. Login walls never reach here:
    ``FetchEngine.simple`` raises ``LoginRequired`` before invoking parse_fn,
    routing to the ``LoginCoordinator`` for an auto-login attempt. Mirrors the
    sequential path's accept rule (``_process_href``): accept the page whenever
    core metadata (``video_code`` OR ``title``) was extracted; reject only
    genuinely empty pages (CF challenge / deleted movie / no detail panel).
    """
    detail = parse_detail_page(html)
    if not (getattr(detail, 'video_code', '') or getattr(detail, 'title', '')):
        return None
    return detail


def _apply_metadata_result(result, *, dry_run: bool):
    """Apply one ``EngineResult`` in the main thread; return ``(ok, failed)`` deltas.

    DB writes happen here (single-threaded), never inside the worker — matching
    ``migrate_v7_to_v8._apply_backfill_result`` and ``align``'s result handler.
    """
    href = result.task.meta['href']
    idx = result.task.entry_index
    if not result.success:
        logger.warning(
            "[%s] %s — fetch_failed: %s", idx, href,
            result.error or 'all proxies failed',
        )
        return (0, 1)
    detail = result.data
    if dry_run:
        logger.info("[%s] ✓ %s (dry-run)", idx, href)
        return (1, 0)
    try:
        MetadataRepo().upsert(href, detail)
    except Exception as exc:  # noqa: BLE001 — write failures are retriable
        logger.warning("[%s] %s — write_failed: %s", idx, href, exc)
        return (0, 1)
    logger.info("[%s] ✓ %s", idx, href)
    return (1, 0)
```

- [ ] **Step 4: Run the new helper tests to verify they pass**

Run: `pytest tests/unit/test_backfill_movie_metadata_fetch.py -k "parse_returns or apply_metadata" -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Wire the parallel branch into `run_backfill_metadata`**

In `run_backfill_metadata`, replace the limit-resolution block (current lines 242-254):

```python
    limit = int(getattr(args, 'limit', 0) or 0)
    limit_per_worker = int(getattr(args, 'limit_per_worker', 0) or 0)
    use_proxy = getattr(args, 'use_proxy', True)

    # ``--limit-per-worker`` predates the switch to sequential execution; it is
    # kept for workflow-input compatibility and interpreted against the
    # configured proxy-pool size so the same input caps a comparable volume.
    if limit_per_worker > 0:
        from javdb.spider.runtime.config import PROXY_POOL
        num_workers = len(PROXY_POOL) if (use_proxy and PROXY_POOL) else 1
        hrefs = hrefs[: limit_per_worker * num_workers]
    elif limit > 0:
        hrefs = hrefs[:limit]
```

with:

```python
    from javdb.spider.runtime.config import PROXY_POOL

    limit = int(getattr(args, 'limit', 0) or 0)
    limit_per_worker = int(getattr(args, 'limit_per_worker', 0) or 0)
    use_proxy = getattr(args, 'use_proxy', True)
    parallel = bool(use_proxy and PROXY_POOL)

    # Resolve the work list. --limit-per-worker is enforced by the engine's
    # per_worker_task_limit, so the list is NOT pre-truncated to
    # limit_per_worker × pool size (ADR-045 D6). Per the CLI contract (--help +
    # the Migration.yml backfill_metadata_limit input) --limit is IGNORED once
    # --limit-per-worker is set, so the global cap only applies on its own. The
    # sequential fallback keeps the same precedence (--limit-per-worker first,
    # else --limit) as a direct list cap.
    if parallel:
        if limit_per_worker <= 0 and limit > 0:
            hrefs = hrefs[:limit]
    else:
        if limit_per_worker > 0:
            hrefs = hrefs[:limit_per_worker]
        elif limit > 0:
            hrefs = hrefs[:limit]
```

> **Post-review correction (PR #156):** the original draft made `--limit` an
> *absolute pre-submit cap* that applied even in parallel mode. That contradicted
> the documented CLI contract — both `--help` and the `Migration.yml`
> `backfill_metadata_limit` input state `--limit` is *ignored when
> `--limit-per-worker > 0`* — as well as the precedence used by the sequential
> path and `align_inventory_with_moviehistory`. The parallel branch is therefore
> gated on `limit_per_worker <= 0`, so `--limit-per-worker` takes precedence
> consistently across both paths (engine-level cap, no pre-truncation — D6).

- [ ] **Step 6: Add the parallel branch around the result loop**

In `run_backfill_metadata`, replace the sequential loop and its trailing summary (current lines 273-309):

```python
    ok = failed = login_gated = 0
    for i, href in enumerate(hrefs, 1):
        idx = f"meta-{i}/{total}"
        result = _process_href(
            href, _detail_url(href, base_url), session,
            use_proxy=use_proxy, dry_run=args.dry_run,
        )
        if result.status in ('ok', 'dry_run'):
            logger.info("[%s] ✓ %s", idx, href)
            ok += 1
        elif result.status == 'login_required':
            # Not a hard failure: the page exists but needs a valid session
            # cookie. Counted separately so it doesn't fail the job, but
            # surfaced so the operator knows to refresh the cookie.
            logger.warning(
                "[%s] %s — login_required: %s", idx, href, result.message
            )
            login_gated += 1
        else:
            logger.warning(
                "[%s] %s — %s: %s", idx, href, result.status, result.message
            )
            failed += 1
        if i < total:
            # Intentional non-cryptographic jitter for crawl timing (anti-ban).
            time.sleep(
                random.uniform(  # noqa: S311
                    movie_sleep_mgr.base_min, movie_sleep_mgr.base_max
                )
            )

    log_summary_block(logger, "MovieMetadata Backfill", {
        "OK": ok,
        "Failed": failed,
        "Login-gated": login_gated,
        "Total": total,
    })
```

with:

```python
    ok = failed = login_gated = 0
    interrupted = False

    if parallel:
        # Parallel-per-proxy via FetchEngine: failed fetches (banned proxy, CF
        # wall, empty body) re-queue to a different proxy instead of being
        # dropped. Login walls trigger LoginCoordinator auto-login. DB writes
        # stay in this main results loop (see _apply_metadata_result).
        from javdb.spider.fetch.fetch_engine import FetchEngine

        movie_sleep_mgr.apply_volume_multiplier(total, num_workers=len(PROXY_POOL))
        engine = FetchEngine.simple(
            parse_fn=_backfill_metadata_parse,
            use_cookie=True,
            sleep_min=movie_sleep_mgr.base_min,
            sleep_max=movie_sleep_mgr.base_max,
            per_worker_task_limit=limit_per_worker if limit_per_worker > 0 else 0,
        )
        engine.start()
        for i, href in enumerate(hrefs, 1):
            engine.submit(
                _detail_url(href, base_url),
                entry_index=f"meta-{i}/{total}",
                meta={'href': href},
            )
        engine.mark_done()

        try:
            for result in engine.results():
                o, f = _apply_metadata_result(result, dry_run=args.dry_run)
                ok += o
                failed += f
        except KeyboardInterrupt:
            interrupted = True
            logger.warning("Keyboard interrupt — shutting down engine …")
            orphaned = engine.shutdown(timeout=30)
            for result in engine.drain_remaining():
                o, f = _apply_metadata_result(result, dry_run=args.dry_run)
                ok += o
                failed += f
            logger.info(
                "Backfill interrupted (parallel, %d workers). "
                "OK: %d, Failed: %d — %d task(s) orphaned (re-run to continue)",
                engine.worker_count, ok, failed, len(orphaned),
            )
        else:
            engine.shutdown()
    else:
        # Sequential fallback (--no-proxy debug, or no PROXY_POOL configured).
        for i, href in enumerate(hrefs, 1):
            idx = f"meta-{i}/{total}"
            result = _process_href(
                href, _detail_url(href, base_url), session,
                use_proxy=use_proxy, dry_run=args.dry_run,
            )
            if result.status in ('ok', 'dry_run'):
                logger.info("[%s] ✓ %s", idx, href)
                ok += 1
            elif result.status == 'login_required':
                # Not a hard failure: the page exists but needs a valid session
                # cookie. Counted separately so it doesn't fail the job, but
                # surfaced so the operator knows to refresh the cookie.
                logger.warning(
                    "[%s] %s — login_required: %s", idx, href, result.message
                )
                login_gated += 1
            else:
                logger.warning(
                    "[%s] %s — %s: %s", idx, href, result.status, result.message
                )
                failed += 1
            if i < total:
                # Intentional non-cryptographic jitter for crawl timing (anti-ban).
                time.sleep(
                    random.uniform(  # noqa: S311
                        movie_sleep_mgr.base_min, movie_sleep_mgr.base_max
                    )
                )

    log_summary_block(logger, "MovieMetadata Backfill", {
        "OK": ok,
        "Failed": failed,
        "Login-gated": login_gated,
        "Total": total,
    })
```

- [ ] **Step 7: Preserve the interrupt exit code**

In `run_backfill_metadata`, the final return logic (current lines 310-326) keeps the login-gated warning and the `failed`/`ok` return. Immediately **before** the existing `if login_gated:` block, add an early interrupt return so an interrupted run reports the conventional SIGINT code:

```python
    if interrupted:
        return 130
```

(The existing `if login_gated: ...`, `if failed == 0: return 0`, `if ok > 0: ... return 0`, `return 1` tail stays unchanged.)

- [ ] **Step 8: Verify the module imports and the WHOLE backfill test file passes**

Run: `python3 -c "import javdb.migrations.tools.backfill_movie_metadata"`
Expected: no output, exit 0 (no syntax/import error).

Run: `pytest tests/unit/test_backfill_movie_metadata_fetch.py -v`
Expected: PASS — both the **new** parallel-helper tests AND the **existing** `_process_href` / `run_backfill_metadata` tests (the latter all use `use_proxy=False`, so they exercise the preserved sequential branch and prove it did not regress).

- [ ] **Step 9: Commit**

```bash
git add javdb/migrations/tools/backfill_movie_metadata.py tests/unit/test_backfill_movie_metadata_fetch.py
git commit -m "feat(db): route metadata backfill through FetchEngine for proxy requeue"
```

---

## Task 5: Fix stale comment + stale `scripts.*` docstring references (D8)

**Files:**
- Modify: `javdb/migrations/tools/backfill_movie_metadata.py` (docstring lines 6-14)
- Modify: `javdb/spider/fetch/fetch_engine.py` (docstring lines 182, 196, 258, 496, 1236)
- Modify: `tests/unit/test_engine.py` (line 1 docstring)

- [ ] **Step 1: Correct the backfill module docstring**

In `javdb/migrations/tools/backfill_movie_metadata.py`, replace the "Execution model" paragraph (lines 6-14) so it no longer claims FetchEngine has no public draining API and reflects the new parallel-with-sequential-fallback model:

```python
Execution model: **parallel-per-proxy via ``FetchEngine.simple`` when a proxy
pool is configured, single-threaded sequential fallback otherwise** (``--no-proxy``
or no ``PROXY_POOL``).  In parallel mode a failed fetch (banned proxy, CF wall,
empty body) is re-queued to a different proxy by the engine; login walls trigger
``LoginCoordinator`` auto-login.  Detail pages sit behind Cloudflare, so the proxy
path enables CF bypass (bypass→direct fallback) — a plain direct fetch returns an
empty body.  Writes go through ``MetadataRepo`` in the main result loop (never on
a worker thread) and are OUTSIDE the Pending→Commit session flow — failures are
logged and retriable on the next run.
```

- [ ] **Step 2: Fix the `scripts.*` references in `fetch_engine.py`**

Run: `grep -n 'scripts\.spider\.fetch' javdb/spider/fetch/fetch_engine.py`
For **each** match (docstrings at lines 182, 196, 258, 496, 1236), replace the substring `scripts.spider.fetch` with `javdb.spider.fetch`. These are docstring cross-references only — no executable code changes.

- [ ] **Step 3: Fix the test module docstring**

In `tests/unit/test_engine.py` line 1, change:

```python
"""Tests for scripts.spider.fetch.fetch_engine — FetchEngine, EngineWorker, WorkerContext."""
```

to:

```python
"""Tests for javdb.spider.fetch.fetch_engine — FetchEngine, EngineWorker, WorkerContext."""
```

- [ ] **Step 4: Verify no stale `scripts.spider.fetch` references remain**

Run: `grep -rn 'scripts\.spider\.fetch' javdb/spider/fetch/fetch_engine.py tests/unit/test_engine.py javdb/migrations/tools/backfill_movie_metadata.py`
Expected: **no output**.

- [ ] **Step 5: Run the engine test suite to confirm nothing regressed**

Run: `pytest tests/unit/test_engine.py -q`
Expected: PASS (all pre-existing tests + the new `TestDrainRemaining` / `TestRunLifecycle`).

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/fetch/fetch_engine.py javdb/migrations/tools/backfill_movie_metadata.py tests/unit/test_engine.py
git commit -m "docs(spider): correct stale FetchEngine comment and scripts.* path refs"
```

---

## Task 6: Full verification gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite for the touched areas**

Run: `pytest tests/unit/test_engine.py tests/unit/test_backfill_movie_metadata_fetch.py -q`
Expected: PASS (no failures, no errors).

- [ ] **Step 2: Run any migration/align/backfill tests + the migrate smoke test**

Run: `pytest tests/unit -k "engine or backfill or migrate or align" -q && pytest tests/smoke/test_migrate_to_current.py -q`
Expected: PASS. (The smoke test guards the `migrate_to_current` → `run_backfill_metadata` wiring; the `-k` filter matching zero extra modules is fine.)

- [ ] **Step 3: Smoke-import every modified module**

Run:
```bash
python3 -c "import javdb.spider.fetch.fetch_engine, javdb.migrations.tools.backfill_movie_metadata, javdb.migrations.tools.migrate_v7_to_v8, javdb.migrations.tools.align_inventory_with_moviehistory; print('imports ok')"
```
Expected: `imports ok`.

- [ ] **Step 4: Confirm the private-queue leak is gone**

Run: `grep -rn '\._result_queue' javdb/migrations/`
Expected: **no output**.

- [ ] **Step 5: Record the docs decision (no handbook change)**

No `docs/handbook/**` edit is required: the CLI surface is unchanged and `--backfill-metadata` was never documented in `cli-reference.md`; the behaviour change (auto-login, parallel execution) is captured in [ADR-045](ADR-045-fetch-engine-public-api.md). State this explicitly in the PR description so the reviewer can disagree.

- [ ] **Step 6: Update ADR-045 status (closeout)**

When the PR is opened, append to ADR-045's Status Log (both `.md` and `.zh.md`): `- 2026-06-01: Phase 1 implemented (IMP-ADR045-01)`. Leave the ADR `Status:` as `Proposed` until merged; flip to `Accepted`/`Completed` during closeout per the repo's ADR lifecycle.

---

## Self-Review

**Spec coverage (against ADR-045 Design Decisions):**

- D1 `drain_remaining()` → Task 1 ✓
- D2 thin `run()` → Task 2 ✓
- D3 backfill → `FetchEngine.simple` + auto-login → Task 4 (Step 6 uses `FetchEngine.simple(use_cookie=True)`; login walls auto-route — no `login_required` branch in the parallel path) ✓
- D4 DB writes in the main results loop → Task 4 (`_apply_metadata_result`) ✓
- D5 preserve D1-aware write path → Task 4 keeps `MetadataRepo()` / `get_db`; no `sqlite3.connect`/`use_sqlite()` guard added ✓
- D6 `--limit-per-worker` → engine `per_worker_task_limit` → Task 4 Steps 5-6 ✓
- D7 blast radius = migration tools only → Tasks 3-4 touch only `migrations/tools/*`; `runner.py`/`index_parallel.py` untouched ✓
- D8 stale comment + `scripts.*` refs → Task 5 ✓

**Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Every code step shows complete code; every run step shows the command + expected output.

**Type/name consistency:** `drain_remaining()` and `run(tasks)` names match across backend, facade, and all call sites (Tasks 1-4). `_backfill_metadata_parse(html, task)` and `_apply_metadata_result(result, *, dry_run)` are defined in Task 4 Step 3 and used in Task 4 Step 6 + the tests with the same signatures. `_apply_metadata_result` returns `(ok, failed)` everywhere it is used. `per_worker_task_limit` is the real `ParallelFetchBackend.__init__` keyword (verified against `fetch_engine.py`). `parallel` flag is defined in Step 5 and consumed in Step 6.
