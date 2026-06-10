# IMP-ADR046-01: ADR-046 Phase 1 — History Write Seam (session-bound writes) Implementation Plan

> **Status: ✅ Implemented 2026-06-02** on branch `adr046-p1-history-write-seam` (subagent-driven execution). All tasks landed; full unit suite 3658 passed (2 pre-existing unrelated failures). The checkboxes below were the execution plan; completion is recorded in the ADR-046 Status Log.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-046](ADR-046-retire-db-facade.md) — this is **Phase 1** (History write seam only).

**Goal:** Make `HistoryRepo`'s two bulk-write methods resolve `session_id` explicitly (constructor-bound), never from the process-global, so a write can no longer silently target the wrong session.

**Architecture:** `HistoryRepo` gains an optional constructor `session_id` and a private `_require_session()` resolver with the order **explicit arg → bound session → raise**. The only two methods that today read `get_active_session_id()` (`batch_update_last_visited`, `batch_update_movie_actors`) switch to `_require_session()`. The explicit session is threaded from `process_detail_entries` (which already has `_session_id_str`) down through `finalize_detail_phase` → `history_manager.batch_update_last_visited`. Reads are untouched; the methods that already take an explicit `session_id` (`commit_session`, `resume_finalizing_session`, `stage_*`, `pending_session_stats`) are untouched. The process-global machinery in `_db_session.py` is **not** removed in this phase (Operations/Stats still use it — ADR-046 D4, Phase 2).

**Tech Stack:** Python 3, `pytest`, `monkeypatch`. No new dependencies.

**Verification posture:** Unit tests assert the resolution order and the write-guard directly (no DB needed for the guard) and assert — via `inspect.getsource` — that the global read is gone. Threading is verified by monkeypatching `HistoryRepo` at the two boundary modules and asserting the bound `session_id`. Production behaviour is unchanged on the happy path (a real run always has a session); the change converts the *missing-session* case from "silent global read" to "loud `RuntimeError`".

---

## File Structure

| Path | Create/Modify/Delete | Responsibility |
| --- | --- | --- |
| `javdb/storage/repos/history_repo.py` | Modify | Add `session_id` to `__init__`; add `_require_session()`; switch `batch_update_last_visited` + `batch_update_movie_actors` off `get_active_session_id()` |
| `javdb/spider/detail/runner.py` | Modify | Add `session_id` kwarg to `finalize_detail_phase`; bind it on the actor-update repo; thread it from `process_detail_entries` (`_session_id_str`) |
| `javdb/storage/history_manager.py` | Modify | Add `session_id` kwarg to module-level `batch_update_last_visited`; bind it on the repo |
| `javdb/legacy/_spider_legacy.py` | Modify | Preserve legacy (rollback-only) behaviour: bind `session_id=get_active_session_id()` at its `batch_update_movie_actors` call sites so they don't raise under the new contract |
| `tests/unit/test_history_repo_session_binding.py` | **Create** | Resolution order + write-guard + "no global read" + reads-without-session |
| `tests/unit/test_adr046_session_threading.py` | **Create** | `finalize_detail_phase` and `history_manager.batch_update_last_visited` bind the session onto the repo |
| `CONTEXT.md` | Modify | Add **Session-Bound Repo** + **Deep Storage Seam** terms (ADR-046 Domain Language) |
| `docs/design/_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md` + `.zh.md` | Modify | Status Log back-reference to ADR-046 (both languages, same commit) |
| `docs/design/ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.md` + `.zh.md` | Modify | Mark Phase 1 status (both languages) once green |

---

## Task 0: Baseline & enumerate (do before any edit)

- [ ] **Step 0.1 — Green baseline.** Confirm the touched areas pass before changes:

```bash
pytest tests/unit/test_history_repo.py tests/unit/test_history_manager.py tests/unit/test_mandatory_session_id.py -q
```
Expected: PASS (record the count).

- [ ] **Step 0.2 — Enumerate the writers that rely on the global.** These must all be migrated or preserved:

```bash
grep -rn "get_active_session_id" javdb/storage/repos/history_repo.py
grep -rn "HistoryRepo()\.batch_update" javdb apps --include="*.py"
grep -rn "finalize_detail_phase(" javdb apps tests --include="*.py" | grep -v "def finalize_detail_phase"
grep -rn "batch_update_last_visited(" javdb apps tests --include="*.py" | grep -v "def \|HistoryRepo\|db_batch_update\|_csv_"
```
Expected set: `history_repo.py` lines ~439 + ~452 read the global; `HistoryRepo().batch_update_*` at `history_manager.py:228`, `spider/detail/runner.py:1165`, `legacy/_spider_legacy.py:1585` + `:1761`; `finalize_detail_phase` called only at `spider/detail/runner.py:859`; module-level `batch_update_last_visited(history_file, …)` called only at `spider/detail/runner.py:1166`. If any caller falls outside this set (esp. in `tests/`), note it — Tasks 2/3 must update it too.

---

## Task 1: `HistoryRepo` — explicit session resolution + write-guard

**Files:**
- Modify: `javdb/storage/repos/history_repo.py`
- Test: `tests/unit/test_history_repo_session_binding.py` (create)

- [ ] **Step 1.1 — Write the failing tests.**

Create `tests/unit/test_history_repo_session_binding.py`:

```python
"""ADR-046 Phase 1: HistoryRepo resolves session explicitly (arg > bound >
raise) and the two bulk-write methods never read the process-global."""
import inspect

import pytest

from javdb.storage.repos.history_repo import HistoryRepo

_SID = "20260602T000000.000000Z-aaaa-0000"


def test_require_session_resolution_order():
    assert HistoryRepo(session_id=_SID)._require_session() == _SID
    # explicit arg wins over the bound session
    assert HistoryRepo(session_id=_SID)._require_session("OTHER") == "OTHER"
    # explicit arg works with no bound session
    assert HistoryRepo()._require_session("ONLY") == "ONLY"


def test_require_session_raises_when_unbound():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        HistoryRepo()._require_session()


def test_batch_update_last_visited_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        HistoryRepo().batch_update_last_visited(["https://javdb.com/v/ABC"])


def test_batch_update_movie_actors_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        HistoryRepo().batch_update_movie_actors(
            [("https://javdb.com/v/ABC", "Actor", "female", "/actors/x", "")]
        )


def test_bulk_writes_no_longer_read_the_global():
    """Regression guard for the ambient-session footgun (ADR-046 D2)."""
    src = inspect.getsource(HistoryRepo.batch_update_last_visited)
    src += inspect.getsource(HistoryRepo.batch_update_movie_actors)
    assert "get_active_session_id" not in src


def test_reads_do_not_require_a_session():
    # A session-less repo must still read.
    assert isinstance(HistoryRepo().load_history(), dict)
```

- [ ] **Step 1.2 — Run the tests, verify they fail.**

Run: `pytest tests/unit/test_history_repo_session_binding.py -q`
Expected: FAIL — `_require_session` does not exist; the bulk methods don't raise and still contain `get_active_session_id`.

- [ ] **Step 1.3 — Add the constructor param + resolver.**

In `javdb/storage/repos/history_repo.py`, replace the constructor:

```python
    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path
```
with:
```python
    def __init__(
        self, *, db_path: Optional[str] = None, session_id: Optional[str] = None,
    ) -> None:
        self._db_path = db_path
        self._session_id = session_id

    def _require_session(self, explicit: Optional[str] = None) -> str:
        """Resolve the session for a write: explicit arg > bound session > raise.

        The process-global ``get_active_session_id()`` is never consulted
        (ADR-046 D2).
        """
        sid = explicit if explicit is not None else self._session_id
        if not sid:
            raise RuntimeError(
                "HistoryRepo write requires a session_id "
                "(pass it, or bind via HistoryRepo(session_id=...))"
            )
        return sid
```

- [ ] **Step 1.4 — Switch the two bulk-write methods off the global.**

Replace `batch_update_last_visited`:

```python
    def batch_update_last_visited(self, hrefs: List[str]) -> int:
        """Bump LastVisited on each href; staging-aware under pending mode."""
        from javdb.storage.db import get_active_session_id
        from javdb.storage.db._db_history_write import db_batch_update_last_visited
        return db_batch_update_last_visited(
            hrefs,
            db_path=self._db_path,
            session_id=get_active_session_id(),
        )
```
with:
```python
    def batch_update_last_visited(self, hrefs: List[str]) -> int:
        """Bump LastVisited on each href; staging-aware under pending mode."""
        from javdb.storage.db._db_history_write import db_batch_update_last_visited
        return db_batch_update_last_visited(
            hrefs,
            db_path=self._db_path,
            session_id=self._require_session(),
        )
```

Replace `batch_update_movie_actors`:

```python
    def batch_update_movie_actors(
        self, updates: List[Tuple[str, str, str, str, str]],
    ) -> int:
        """Bulk overwrite actor fields, preserving pending-mode staging."""
        # The db.py facade owns pending-mode staging for actor-only writes.
        from javdb.storage.db import get_active_session_id
        from javdb.storage.db._db_history_write import db_batch_update_movie_actors
        return db_batch_update_movie_actors(
            updates,
            db_path=self._db_path,
            session_id=get_active_session_id(),
        )
```
with:
```python
    def batch_update_movie_actors(
        self, updates: List[Tuple[str, str, str, str, str]],
    ) -> int:
        """Bulk overwrite actor fields, preserving pending-mode staging."""
        # The db.py facade owns pending-mode staging for actor-only writes.
        from javdb.storage.db._db_history_write import db_batch_update_movie_actors
        return db_batch_update_movie_actors(
            updates,
            db_path=self._db_path,
            session_id=self._require_session(),
        )
```

- [ ] **Step 1.5 — Update the class docstring** so it stops promising per-call-only session. Replace the constructor paragraph of the `HistoryRepo` docstring:

```python
    Construction takes only an optional ``db_path`` override (used in
    tests / smoke runs against a fresh DB). Methods that mutate state
    take ``session_id`` per call so a single Repo instance can service
    multiple sessions (e.g. a sweep over stale runs) without rebuild.
```
with:
```python
    Construction takes an optional ``db_path`` override and an optional
    ``session_id``. Writes resolve their session as **explicit arg >
    constructor-bound session > raise** (ADR-046 D2) — the process-global
    is never read. Methods that take an explicit ``session_id``
    (``commit_session``, ``resume_finalizing_session``, ``stage_*``,
    ``pending_session_stats``) still let one Repo service a sweep over many
    sessions; the bulk ``batch_update_*`` writes use the bound session.
```

- [ ] **Step 1.6 — Run the tests, verify they pass.**

Run: `pytest tests/unit/test_history_repo_session_binding.py -q`
Expected: PASS (6 tests).

- [ ] **Step 1.7 — Commit.**

```bash
git add javdb/storage/repos/history_repo.py tests/unit/test_history_repo_session_binding.py
git commit -m "refactor(storage): HistoryRepo resolves session explicitly, drops global read (ADR-046 P1)"
```

---

## Task 2: Thread `session_id` through `finalize_detail_phase`

**Files:**
- Modify: `javdb/spider/detail/runner.py`
- Test: `tests/unit/test_adr046_session_threading.py` (create)

- [ ] **Step 2.1 — Write the failing test.**

Create `tests/unit/test_adr046_session_threading.py`:

```python
"""ADR-046 Phase 1: the detail-phase + history-manager boundaries bind the
explicit session onto the HistoryRepo they construct."""


def test_finalize_detail_phase_binds_session_to_repo(monkeypatch):
    import javdb.spider.detail.runner as runner

    captured = {}

    class FakeRepo:
        def __init__(self, *, db_path=None, session_id=None):
            captured["actors_session"] = session_id

        def batch_update_movie_actors(self, updates):
            return len(updates)

    monkeypatch.setattr(runner, "HistoryRepo", FakeRepo)
    monkeypatch.setattr(runner, "use_sqlite", lambda: True)
    # Capture the session forwarded to the last-visited path too.
    monkeypatch.setattr(
        runner, "batch_update_last_visited",
        lambda history_file, visited, *, session_id: captured.update(
            visited_session=session_id
        ),
    )

    runner.finalize_detail_phase(
        use_history_for_saving=True,
        dry_run=False,
        history_file="x.csv",
        visited_hrefs={"https://javdb.com/v/A"},
        actor_updates=[("https://javdb.com/v/A", "Actor", "female", "/a/x", "")],
        session_id="SID-RUNNER",
    )

    assert captured["actors_session"] == "SID-RUNNER"
    assert captured["visited_session"] == "SID-RUNNER"
```

- [ ] **Step 2.2 — Run it, verify it fails.**

Run: `pytest tests/unit/test_adr046_session_threading.py::test_finalize_detail_phase_binds_session_to_repo -q`
Expected: FAIL — `finalize_detail_phase()` has no `session_id` parameter (TypeError).

- [ ] **Step 2.3 — Add `session_id` to `finalize_detail_phase` and use it.**

In `javdb/spider/detail/runner.py`, replace the function (currently lines ~1153-1166):

```python
def finalize_detail_phase(
    *,
    use_history_for_saving: bool,
    dry_run: bool,
    history_file: str,
    visited_hrefs: set,
    actor_updates: list,
) -> None:
    """Flush shared per-phase side effects after detail processing completes."""

    if use_history_for_saving and not dry_run and visited_hrefs:
        if use_sqlite() and actor_updates:
            HistoryRepo().batch_update_movie_actors(actor_updates)
        batch_update_last_visited(history_file, visited_hrefs)
```
with:
```python
def finalize_detail_phase(
    *,
    use_history_for_saving: bool,
    dry_run: bool,
    history_file: str,
    visited_hrefs: set,
    actor_updates: list,
    session_id: Optional[str],
) -> None:
    """Flush shared per-phase side effects after detail processing completes.

    ``session_id`` is the explicit run session (ADR-046 D2); it is bound onto
    the write repo rather than read from a process-global.
    """

    if use_history_for_saving and not dry_run and visited_hrefs:
        if use_sqlite() and actor_updates:
            HistoryRepo(session_id=session_id).batch_update_movie_actors(actor_updates)
        batch_update_last_visited(history_file, visited_hrefs, session_id=session_id)
```

(If `Optional` is not already imported in `runner.py`, add `from typing import Optional` to the imports — verify with `grep -n "from typing import" javdb/spider/detail/runner.py`.)

- [ ] **Step 2.4 — Thread the session at the call site (line ~859).**

Replace:
```python
    finalize_detail_phase(
        use_history_for_saving=use_history_for_saving,
        dry_run=dry_run,
        history_file=history_file,
        visited_hrefs=visited_hrefs,
        actor_updates=actor_updates,
    )
```
with:
```python
    finalize_detail_phase(
        use_history_for_saving=use_history_for_saving,
        dry_run=dry_run,
        history_file=history_file,
        visited_hrefs=visited_hrefs,
        actor_updates=actor_updates,
        session_id=_session_id_str or None,
    )
```
(`_session_id_str` is already in scope — assigned at `runner.py:491`: `_session_id_str = str(_active_session_id) if _active_session_id is not None else ""`. The `or None` maps the empty-string "no session" sentinel to a clean `None`, so the guard in Task 1 raises loudly rather than writing under `""`.)

- [ ] **Step 2.5 — Run the test, verify it passes.**

Run: `pytest tests/unit/test_adr046_session_threading.py::test_finalize_detail_phase_binds_session_to_repo -q`
Expected: PASS.

- [ ] **Step 2.6 — Commit.**

```bash
git add javdb/spider/detail/runner.py tests/unit/test_adr046_session_threading.py
git commit -m "refactor(spider): thread explicit session_id into finalize_detail_phase (ADR-046 P1)"
```

---

## Task 3: Thread `session_id` through `history_manager.batch_update_last_visited`

**Files:**
- Modify: `javdb/storage/history_manager.py`
- Test: `tests/unit/test_adr046_session_threading.py` (extend)

- [ ] **Step 3.1 — Add the failing test.**

Append to `tests/unit/test_adr046_session_threading.py`:

```python
def test_history_manager_batch_update_last_visited_binds_session(monkeypatch):
    import javdb.storage.history_manager as hm

    captured = {}

    class FakeRepo:
        def __init__(self, *, db_path=None, session_id=None):
            captured["session_id"] = session_id

        def batch_update_last_visited(self, hrefs):
            return len(list(hrefs))

    monkeypatch.setattr(hm, "HistoryRepo", FakeRepo)
    monkeypatch.setattr(hm, "use_sqlite", lambda: True)
    monkeypatch.setattr(hm, "use_csv", lambda: False)
    monkeypatch.setattr(hm, "_ensure_db", lambda: None)

    hm.batch_update_last_visited(
        "x.csv", {"https://javdb.com/v/A"}, session_id="SID-HM",
    )
    assert captured["session_id"] == "SID-HM"
```

- [ ] **Step 3.2 — Run it, verify it fails.**

Run: `pytest tests/unit/test_adr046_session_threading.py::test_history_manager_batch_update_last_visited_binds_session -q`
Expected: FAIL — `batch_update_last_visited()` got an unexpected keyword `session_id`.

- [ ] **Step 3.3 — Add `session_id` and bind it.**

In `javdb/storage/history_manager.py`, replace (currently lines ~223-233):

```python
def batch_update_last_visited(history_file, visited_hrefs):
    """Update last_visited_datetime for a set of hrefs."""
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        updated = HistoryRepo().batch_update_last_visited(list(visited_hrefs))
        if updated:
            logger.debug(f"Updated last_visited_datetime for {updated} movies")

    if use_csv():
        _csv_batch_update_last_visited(history_file, visited_hrefs)
```
with:
```python
def batch_update_last_visited(history_file, visited_hrefs, *, session_id):
    """Update last_visited_datetime for a set of hrefs.

    ``session_id`` is the explicit run session (ADR-046 D2), bound onto the
    write repo instead of resolved from a process-global.
    """
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        updated = HistoryRepo(session_id=session_id).batch_update_last_visited(
            list(visited_hrefs)
        )
        if updated:
            logger.debug(f"Updated last_visited_datetime for {updated} movies")

    if use_csv():
        _csv_batch_update_last_visited(history_file, visited_hrefs)
```

- [ ] **Step 3.4 — Run the test, verify it passes.**

Run: `pytest tests/unit/test_adr046_session_threading.py -q`
Expected: PASS (both threading tests).

- [ ] **Step 3.5 — Commit.**

```bash
git add javdb/storage/history_manager.py tests/unit/test_adr046_session_threading.py
git commit -m "refactor(storage): bind explicit session in history_manager.batch_update_last_visited (ADR-046 P1)"
```

---

## Task 4: Preserve legacy (rollback-only) call sites

**Files:**
- Modify: `javdb/legacy/_spider_legacy.py`

`javdb/legacy/` is preserved for rollback only and is **not** imported by production (CLAUDE.md). Its `HistoryRepo().batch_update_movie_actors(...)` calls (lines ~1585 and ~1761) would now raise (no bound session). Preserve their current behaviour by binding the global explicitly — the global still exists in Phase 1 (ADR-046 D4).

- [ ] **Step 4.1 — Locate every legacy site.**

```bash
grep -n "HistoryRepo()\.batch_update" javdb/legacy/_spider_legacy.py
```
Expected: two `HistoryRepo().batch_update_movie_actors(actor_updates)` lines (~1585, ~1761).

- [ ] **Step 4.2 — Bind the session explicitly at each.**

For each occurrence, replace:
```python
            HistoryRepo().batch_update_movie_actors(actor_updates)
```
with:
```python
            from javdb.storage.db import get_active_session_id
            HistoryRepo(
                session_id=get_active_session_id()
            ).batch_update_movie_actors(actor_updates)
```
(Legacy keeps its global-based behaviour, now expressed explicitly at the construction site. If `get_active_session_id` returns `None` here, the write raises — acceptable: legacy is rollback-only and the loud failure is preferable to a wrong-session write.)

- [ ] **Step 4.3 — Verify legacy still imports cleanly.**

Run: `python -c "import javdb.legacy._spider_legacy; print('import ok')"`
Expected: `import ok`.

- [ ] **Step 4.4 — Commit.**

```bash
git add javdb/legacy/_spider_legacy.py
git commit -m "refactor(legacy): bind session explicitly at batch_update_movie_actors sites (ADR-046 P1)"
```

---

## Task 5: Docs & domain language

**Files:**
- Modify: `CONTEXT.md`
- Modify: ADR-005 `.md` + `.zh.md` (Status Log back-reference)
- Modify: ADR-046 `.md` + `.zh.md` (Phase 1 status)

- [ ] **Step 5.1 — CONTEXT.md terms.** Add to the architectural-patterns/terminology section (verbatim from ADR-046 Domain Language):

```markdown
- **Session-Bound Repo** — a repository instance carrying its `session_id` from construction (`HistoryRepo(session_id=...)`). Writes resolve session as explicit arg > bound session > raise; the process-global `get_active_session_id()` is never read (ADR-046).
- **Deep Storage Seam** — the principle that the repository, not the module-level `db_*` function layer, is the single public way to read/write storage; `db_*` is retired to a private implementation detail in phases (ADR-046).
```
Verify both English and the paired Chinese glossary row are added in the same commit if CONTEXT.md is bilingual in that section (it carries a 术语对照表 — add a matching row).

- [ ] **Step 5.2 — ADR-005 back-reference (both languages, same commit).** Append a Status Log line to `docs/design/_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md` **and** `.zh.md`:

English:
```markdown
- 2026-06-02: Direction continued by [ADR-046](../ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.md) — the Repo becomes the deep storage seam and `db_*` is retired in phases; Phase 1 makes History writes session-bound (no process-global).
```
Chinese (`.zh.md`):
```markdown
- 2026-06-02：方向由 [ADR-046](../ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.zh.md) 延续——Repo 成为存储深接缝、`db_*` 分阶段退役；Phase 1 让 History 写绑定 session（不再读进程级全局）。
```

- [ ] **Step 5.3 — Verify the new links resolve.**

```bash
python3 - <<'PY'
import os, re
LINK = re.compile(r'\]\((\.{1,2}/[^)]+)\)')
for f in [
    "docs/design/_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md",
    "docs/design/_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md",
]:
    d = os.path.dirname(f)
    for m in LINK.finditer(open(f, encoding="utf-8").read()):
        t = m.group(1).split("#")[0]
        if t and not os.path.exists(os.path.normpath(os.path.join(d, t))):
            print("BROKEN:", f, m.group(1))
print("link check done")
PY
```
Expected: `link check done` with no `BROKEN` lines.

- [ ] **Step 5.4 — Commit.**

```bash
git add CONTEXT.md docs/design/_archive/ADR-005-Db-Py-Retirement/
git commit -m "docs(adr-046): add domain terms + ADR-005 back-reference (P1)"
```

---

## Task 6: Final verification gates

- [ ] **Step 6.1 — The global read is gone from the History write path.**

```bash
grep -n "get_active_session_id" javdb/storage/repos/history_repo.py
```
Expected: **no matches** (the two bulk methods no longer import or call it).

- [ ] **Step 6.2 — Explicit-session methods + reads untouched.** Confirm the CLI sweep callers still type-check against the unchanged method signatures:

```bash
python -c "import apps.cli.db.commit_session, apps.cli.db.cleanup_stale_in_progress; print('cli import ok')"
grep -n "HistoryRepo().commit_session\|HistoryRepo().resume_finalizing_session\|HistoryRepo().pending_session_stats" apps/cli/db/*.py
```
Expected: `cli import ok`; the CLI calls still pass an explicit `sid`/`session_id` argument (resolution order accepts it — no change needed).

- [ ] **Step 6.3 — Targeted + new suites green.**

```bash
pytest tests/unit/test_history_repo_session_binding.py tests/unit/test_adr046_session_threading.py tests/unit/test_history_repo.py tests/unit/test_history_manager.py tests/unit/test_mandatory_session_id.py -q
```
Expected: PASS.

- [ ] **Step 6.4 — Full unit + smoke suite.**

```bash
pytest tests/unit tests/smoke -q
```
Expected: PASS (no regressions). If a pre-existing test constructed `HistoryRepo()` then called `batch_update_*` without a session, it now fails loudly — fix it to pass `session_id=` (this is the intended contract change; update the test, do not weaken the guard).

- [ ] **Step 6.5 — Lint.**

```bash
ruff check javdb/storage/repos/history_repo.py javdb/spider/detail/runner.py javdb/storage/history_manager.py javdb/legacy/_spider_legacy.py tests/unit/test_history_repo_session_binding.py tests/unit/test_adr046_session_threading.py
```
Expected: clean.

- [ ] **Step 6.6 — Mark ADR-046 Phase 1 done (both languages).** In `ADR-046-retire-db-facade.md` and `.zh.md`, update the Implementation Roadmap's Phase 1 row status and add a Status Log line noting Phase 1 landed. Commit:

```bash
git add docs/design/ADR-046-Retire-Db-Facade/
git commit -m "docs(adr-046): mark Phase 1 (History write seam) implemented"
```

---

## Rollback

Pure refactor scoped to the History write path. No schema, data, or D1 changes; the process-global machinery is untouched (Phase 2). Revert the commits to restore the prior (global-reading) behaviour. Production happy-path behaviour is unchanged; only the missing-session case differs (loud raise vs. silent global read).

## Out of Scope (ADR-046 later phases — do NOT do here)

- Privatizing the `db_*` functions / removing `__init__.py` re-exports (Phase 4).
- Deleting the process-global session machinery in `_db_session.py` (Phase 2 — Operations/Stats still use it).
- Binding sessions on `OperationsRepo` / `StatsRepo` (Phase 2).
- Rerouting `sessions/commit.py` / `rollback/core.py` off the direct `db_*` calls through the repo (Phase 3).
- Touching the read methods' shape.

## Self-Review

- **Spec coverage:** ADR-046 D2 (explicit resolution order + guard) → Task 1; D3 (reads session-agnostic) → Task 1 test `test_reads_do_not_require_a_session`; the "~6 write sites + 2 CLI sites" migration → Tasks 2/3 (the 2 global-reading sites + their threading) and Task 6.2 (the CLI sites need no change — already explicit); D4 (global retained) → honoured by Out of Scope; Domain Language → Task 5.1. No Phase-1 requirement is unmapped.
- **Placeholder scan:** every code step shows the exact before/after; no "TBD"/"handle errors"/"similar to".
- **Type consistency:** `_require_session(explicit=None) -> str`; `finalize_detail_phase(..., session_id: Optional[str])`; `batch_update_last_visited(history_file, visited_hrefs, *, session_id)` — names match across Tasks 1/2/3 and the tests.
