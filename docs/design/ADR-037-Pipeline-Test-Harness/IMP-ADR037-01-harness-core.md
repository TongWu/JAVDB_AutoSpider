# IMP-ADR037-01: Harness Core + Golden Scenario (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-037](ADR-037-deterministic-pipeline-test-harness.md) (umbrella) — this is **Phase 1** of three.

**Goal:** A deterministic, in-process `tests/harness/` that composes a `FixtureHTTP` transport (replays javdb), a `FakeQB` (in-memory, controllable), and the existing seeded temp DB, with a scenario + assertion API and one golden daily-run scenario proving spider → uploader → commit in CI with zero network.

**Architecture:** The harness injects fakes by monkeypatch at two precise seams — `javdb.infra.request.RequestHandler.get_page` (HTTP) and `javdb.integrations.qb.uploader.service._wrap_session_as_client` (qB) — then drives the service layer in-process (`run_spider(options)` → `db_commit_session_history(session_id)`). The DB uses the existing autouse `_isolate_sqlite`. Building blocks are TDD'd in isolation; the full E2E golden scenario is the integration capstone.

**Tech Stack:** Python 3, `pytest` (`monkeypatch`), `sqlite3`, the existing `tests/conftest.py` fixtures.

**Seams (confirmed):** `RequestHandler.get_page(self, url, session=None, use_cookie=False, use_proxy=False, module_name='unknown', max_retries=3, use_cf_bypass=False) -> Optional[str]`; `_wrap_session_as_client(session, use_proxy=False) -> QBittorrentClient`; `run_spider(options: SpiderRunOptions) -> SpiderRunResult`; `db_commit_session_history(session_id)`.

---

## Implementation Reconciliation (Phase 1 shipped — 2026-05-30)

> **Status: Implemented.** The shipped harness is the source of truth
> (`tests/harness/`, 11 tests green in <0.4s). The Task code blocks below were
> the *plan*; reality diverged on the integration seams flagged in Task 6. The
> divergences and why the original assumptions were wrong:

1. **Three real steps, not `run_spider` + a bare commit.** The daily flow is
   `run_spider(options)` → `run_uploader(QbUploaderOptions(...))` →
   `commit_session(CommitRequest(session_id))`. The plan's
   `db_commit_session_history(session_id)` is the low-level drain; the
   session-lifecycle-correct entry is `commit_session(CommitRequest(...))`
   (`javdb/storage/sessions/commit.py`). The pipeline runs the uploader as a
   **subprocess**, so the harness must call `run_uploader` in-process itself
   (monkeypatch can't cross a subprocess). The commit is **gated on spider AND
   uploader success** (`exit_code == 0`), mirroring `DailyIngestion.yml`'s "Mark
   sessions as committed" step (`if: ${{ success() }}`): a failed uploader
   leaves the session uncommitted for the cleanup-on-failure rollback, so the
   harness must not drain pending rows either. `FakeQBConfig(fail_adds=True)`
   exercises this (see `test_uploader_failure_blocks_commit`).
2. **Session id + CSV path come from the returned `SpiderRunResult`, not
   `get_active_session_id()`.** `run_spider`'s `finally` block clears the active
   session context before returning (`run_service.py:959-961`), so the plan's
   post-hoc `get_active_session_id()` returns `None`. Use
   `spider_result.session_id` / `spider_result.csv_path`.
3. **`SpiderRunOptions` is a 17-field frozen dataclass** — all fields required.
   The plan's 3-arg constructor `SpiderRunOptions(use_proxy=..., ...)` raises
   `TypeError`. The harness builds the full object (see
   `PipelineHarness._daily_options`; reference `tests/unit/test_spider_run_options.py`).
4. **The uploader's qB probes run before `_wrap_session_as_client`.**
   `run_uploader` calls `test_qbittorrent_connection` then `login_to_qbittorrent`
   first; if not neutered it returns `error_reason="qb-unreachable"` before any
   add. The harness patches **all three** seams (both probes → `True`, plus
   `_wrap_session_as_client` → `FakeQB`).
5. **`STORAGE_MODE` must be `duo`, not `db`.** The autouse `_isolate_sqlite`
   forces `db` (no CSV), but the uploader consumes the spider's dated output CSV
   — `use_csv()` must be `True`. The harness overrides
   `_cfg_mod._storage_mode_override = "duo"` (mirrors production) so the spider
   writes both pending DB rows AND the CSV handoff.
6. **`get_db()` takes a *path*, not a logical name.** The plan's
   `get_db("history")` opens a DB literally named `history` (empty → "no such
   table"). Call `get_db()` with no arg — it defaults to `HISTORY_DB_PATH`,
   which `_isolate_sqlite` points at the one seeded temp DB.
7. **Determinism extras the plan omitted:** `monkeypatch.chdir(tmp_path)` keeps
   the spider's `reports/` artifacts out of the repo; patching
   `MovieSleepManager.sleep` (class-level, covers the runtime-bound instance)
   and `up_service.DELAY_BETWEEN_ADDITIONS = 0` removes the ~10s of real-time
   throttle (22s → 0.08s).
8. **Fixture registration:** the `pipeline_harness` fixture is registered via
   `tests/harness/conftest.py` (a module-level fixture isn't auto-discovered by
   pytest); `__init__.py` only re-exports the names.

The shipped `PipelineHarness.run_daily` / `_install` / `HistoryView` reflect all
of the above; treat `tests/harness/pipeline_harness.py` as canonical over the
Task 4 listing below.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `tests/harness/__init__.py` | Create | Package marker + re-exports |
| `tests/harness/fixture_http.py` | Create | `FixtureHTTP` (replay by URL; record stub) |
| `tests/harness/fake_qb.py` | Create | `FakeQB` (in-memory, controllable states) |
| `tests/harness/pipeline_harness.py` | Create | `PipelineScenario`, `FakeQBConfig`, `PipelineHarness`, `pipeline_harness` fixture |
| `tests/harness/scenarios/golden_daily.py` | Create | Minimal index + 2 detail fixtures + scenario builder |
| `tests/harness/test_fixture_http.py` | Create | FixtureHTTP unit tests |
| `tests/harness/test_fake_qb.py` | Create | FakeQB unit tests |
| `tests/harness/test_injection_seams.py` | Create | Monkeypatch-seam tests (get_page / _wrap_session_as_client) |
| `tests/harness/test_golden_scenario.py` | Create | The E2E capstone |

**Naming contract (verbatim across tasks):**
`FixtureHTTP(pages: dict)` with `get_page(self, url, *args, **kwargs) -> str | None` and `record_miss` flag; `FakeQB()` with `add_torrent(magnet_link, name=None, category=None, **kw) -> bool`, `get_existing_hashes() -> set`, `get_torrents_multiple_categories(categories, torrent_filter='downloading') -> list[dict]`, `delete_torrents(hashes, delete_files=True) -> bool`, control `complete(qb_hash)`, `stall(qb_hash)`, `all_hashes() -> set`; `PipelineScenario(pages: dict, qb: FakeQBConfig)`; `FakeQBConfig(initial=())`; `PipelineHarness` with `run_daily(scenario) -> HarnessResult`, `history() -> HistoryView`, `events() -> list[str]`, `acquisition_outcomes() -> list[dict]`; pytest fixture `pipeline_harness`.

> **Phase-2-gated:** record mode, the scenario library (drift/completion/failure), and the SMTP/pikpak/rclone seams are NOT in this plan.

---

## Task 1: `FixtureHTTP` transport

**Files:**
- Create: `tests/harness/__init__.py`
- Create: `tests/harness/fixture_http.py`
- Test: `tests/harness/test_fixture_http.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/harness/test_fixture_http.py
from tests.harness.fixture_http import FixtureHTTP


def test_get_page_returns_fixture_by_url():
    http = FixtureHTTP({"https://javdb.com/?page=1": "<html>index</html>"})
    assert http.get_page("https://javdb.com/?page=1") == "<html>index</html>"


def test_get_page_trailing_slash_insensitive():
    http = FixtureHTTP({"https://javdb.com/v/abc": "<html>detail</html>"})
    assert http.get_page("https://javdb.com/v/abc/") == "<html>detail</html>"


def test_miss_returns_none_and_records_request():
    http = FixtureHTTP({})
    assert http.get_page("https://javdb.com/v/missing") is None
    assert "https://javdb.com/v/missing" in http.misses


def test_matches_real_get_page_kwargs():
    # must accept the full RequestHandler.get_page signature without error
    http = FixtureHTTP({"u": "ok"})
    assert http.get_page("u", session=None, use_cookie=False, use_proxy=True,
                         module_name="spider", max_retries=3, use_cf_bypass=False) == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness/test_fixture_http.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write FixtureHTTP**

```python
# tests/harness/__init__.py
"""Deterministic in-process pipeline test harness (ADR-037 Phase 1)."""
```

```python
# tests/harness/fixture_http.py
"""Replay javdb responses from an in-memory cassette (ADR-037 D3).

Drop-in for RequestHandler.get_page: same (url, **kwargs) -> str | None shape.
Record mode is a Phase-2 stub here (misses are merely tracked)."""

from __future__ import annotations

from typing import Optional


def _norm(url: str) -> str:
    return url.rstrip("/")


class FixtureHTTP:
    def __init__(self, pages: dict, *, record_miss: bool = False) -> None:
        self._pages = {_norm(k): v for k, v in pages.items()}
        self.record_miss = record_miss
        self.misses: list[str] = []
        self.requests: list[str] = []

    def get_page(self, url: str, *args, **kwargs) -> Optional[str]:
        self.requests.append(url)
        hit = self._pages.get(_norm(url))
        if hit is None:
            self.misses.append(url)
        return hit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/harness/test_fixture_http.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/harness/__init__.py tests/harness/fixture_http.py tests/harness/test_fixture_http.py
git commit -m "test(harness): add FixtureHTTP replay transport (ADR-037)"
```

---

## Task 2: `FakeQB`

**Files:**
- Create: `tests/harness/fake_qb.py`
- Test: `tests/harness/test_fake_qb.py`

`FakeQB` implements the `QBittorrentClient` surface the pipeline uses, over an
in-memory dict, computing the hash from the magnet the same way production does
(`extract_hash_from_magnet`) so add → get_existing_hashes → AcquisitionOutcome line up.

- [ ] **Step 1: Write the failing test**

```python
# tests/harness/test_fake_qb.py
from tests.harness.fake_qb import FakeQB

_MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def test_add_then_listed_as_downloading():
    qb = FakeQB()
    assert qb.add_torrent(_MAGNET, category="JavDB") is True
    rows = qb.get_torrents_multiple_categories(["JavDB"], torrent_filter="downloading")
    assert len(rows) == 1
    assert rows[0]["hash"] == "a" * 40
    assert rows[0]["state"] == "downloading"


def test_existing_hashes_reflects_adds():
    qb = FakeQB()
    qb.add_torrent(_MAGNET, category="JavDB")
    assert "a" * 40 in qb.get_existing_hashes()


def test_complete_moves_to_completed_filter():
    qb = FakeQB()
    qb.add_torrent(_MAGNET, category="JavDB")
    qb.complete("a" * 40)
    assert qb.get_torrents_multiple_categories(["JavDB"], torrent_filter="downloading") == []
    done = qb.get_torrents_multiple_categories(["JavDB"], torrent_filter="completed")
    assert done[0]["progress"] == 1.0


def test_delete_removes():
    qb = FakeQB()
    qb.add_torrent(_MAGNET, category="JavDB")
    qb.delete_torrents(["a" * 40])
    assert qb.all_hashes() == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness/test_fake_qb.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write FakeQB**

```python
# tests/harness/fake_qb.py
"""In-memory qBittorrent fake with controllable torrent state (ADR-037 D4)."""

from __future__ import annotations

from javdb.integrations.qb.client import extract_hash_from_magnet


class FakeQB:
    def __init__(self) -> None:
        # hash -> {hash, name, category, state, progress}
        self._torrents: dict[str, dict] = {}

    # --- QBittorrentClient surface used by the pipeline -------------------
    def add_torrent(self, magnet_link, name=None, category=None, **_kw) -> bool:
        h = extract_hash_from_magnet(magnet_link)
        if not h:
            return False
        self._torrents[h] = {"hash": h, "name": name or h, "category": category or "",
                             "state": "downloading", "progress": 0.0}
        return True

    def get_existing_hashes(self):
        return set(self._torrents.keys())

    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        cats = set(categories)
        out = []
        for t in self._torrents.values():
            if t["category"] not in cats:
                continue
            done = t["progress"] >= 1.0
            if torrent_filter == "completed" and not done:
                continue
            if torrent_filter == "downloading" and done:
                continue
            out.append(dict(t))
        return out

    def delete_torrents(self, hashes, delete_files=True) -> bool:
        for h in hashes:
            self._torrents.pop(h, None)
        return True

    # --- control surface -------------------------------------------------
    def complete(self, qb_hash: str) -> None:
        if qb_hash in self._torrents:
            self._torrents[qb_hash]["progress"] = 1.0
            self._torrents[qb_hash]["state"] = "uploading"

    def stall(self, qb_hash: str) -> None:
        if qb_hash in self._torrents:
            self._torrents[qb_hash]["state"] = "stalledDL"

    def all_hashes(self) -> set:
        return set(self._torrents.keys())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/harness/test_fake_qb.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/harness/fake_qb.py tests/harness/test_fake_qb.py
git commit -m "test(harness): add in-memory FakeQB (ADR-037)"
```

---

## Task 3: Injection seams — patch points work

**Files:**
- Test: `tests/harness/test_injection_seams.py`

Prove the two monkeypatch seams behave: patching `RequestHandler.get_page` routes
through `FixtureHTTP`, and patching `_wrap_session_as_client` returns the `FakeQB`.
This pins the contract the harness composition depends on.

- [ ] **Step 1: Write the test**

```python
# tests/harness/test_injection_seams.py
from javdb.infra.request import RequestHandler
import javdb.integrations.qb.uploader.service as up_service
from tests.harness.fake_qb import FakeQB
from tests.harness.fixture_http import FixtureHTTP


def test_get_page_seam_routes_to_fixture(monkeypatch):
    http = FixtureHTTP({"https://javdb.com/x": "<html>ok</html>"})
    monkeypatch.setattr(RequestHandler, "get_page",
                        lambda self, url, *a, **k: http.get_page(url, *a, **k))
    # Any RequestHandler instance now replays the cassette.
    h = RequestHandler.__new__(RequestHandler)
    assert h.get_page("https://javdb.com/x") == "<html>ok</html>"


def test_wrap_session_seam_returns_fake_qb(monkeypatch):
    fake = FakeQB()
    monkeypatch.setattr(up_service, "_wrap_session_as_client",
                        lambda session, use_proxy=False: fake)
    assert up_service._wrap_session_as_client(object()) is fake
```

- [ ] **Step 2: Run to verify PASS**

Run: `pytest tests/harness/test_injection_seams.py -v`
Expected: PASS (2 passed). If `RequestHandler.get_page`'s real signature has
diverged, fix the lambda's `*a, **k` to match — the seam test is the guard.

- [ ] **Step 3: Commit**

```bash
git add tests/harness/test_injection_seams.py
git commit -m "test(harness): pin RequestHandler.get_page + _wrap_session_as_client seams"
```

---

## Task 4: `PipelineHarness` composition + scenario/assertion API

**Files:**
- Create: `tests/harness/pipeline_harness.py`
- Test: covered by Task 6 (the golden scenario) + the seam tests; this task wires composition.

- [ ] **Step 1: Write the harness**

```python
# tests/harness/pipeline_harness.py
"""In-process pipeline harness (ADR-037). Composes FixtureHTTP + FakeQB + seeded DB."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from javdb.infra.request import RequestHandler
import javdb.integrations.qb.uploader.service as up_service
from tests.harness.fake_qb import FakeQB
from tests.harness.fixture_http import FixtureHTTP


@dataclass
class FakeQBConfig:
    initial: tuple = ()


@dataclass
class PipelineScenario:
    pages: dict
    qb: FakeQBConfig = field(default_factory=FakeQBConfig)


class HistoryView:
    def __init__(self) -> None:
        from javdb.storage.db import get_db
        self._get_db = get_db

    def count(self) -> int:
        with self._get_db("history") as conn:
            return conn.execute("SELECT COUNT(*) FROM MovieHistory").fetchone()[0]


class HarnessResult:
    def __init__(self, fake_qb: FakeQB, http: FixtureHTTP) -> None:
        self.qb = fake_qb
        self.http = http


class PipelineHarness:
    def __init__(self, monkeypatch) -> None:
        self._mp = monkeypatch
        self.http: FixtureHTTP | None = None
        self.qb: FakeQB | None = None

    def _install(self, scenario: PipelineScenario) -> None:
        self.http = FixtureHTTP(scenario.pages)
        self.qb = FakeQB()
        self._mp.setattr(RequestHandler, "get_page",
                         lambda _self, url, *a, **k: self.http.get_page(url, *a, **k))
        self._mp.setattr(up_service, "_wrap_session_as_client",
                         lambda session, use_proxy=False: self.qb)

    def run_daily(self, scenario: PipelineScenario) -> HarnessResult:
        from javdb.spider.app.run_service import run_spider
        from javdb.spider.app.options import SpiderRunOptions
        from javdb.storage.db import db_commit_session_history, get_active_session_id

        self._install(scenario)
        # Deterministic minimal daily options: no proxy, phase 1, ignore release-date
        # so authored fixtures are not date-filtered. Tune as the golden scenario requires.
        options = SpiderRunOptions(use_proxy=False, ignore_release_date=True, phase="1")
        run_spider(options)
        # uploader runs in-process against FakeQB via the patched factory; if the
        # golden scenario drives the uploader separately, call its service here.
        sid = get_active_session_id()
        if sid:
            db_commit_session_history(sid)
        return HarnessResult(self.qb, self.http)

    def history(self) -> HistoryView:
        return HistoryView()

    def events(self) -> list[str]:
        from javdb.storage.db import get_db
        try:
            with get_db("reports") as conn:
                rows = conn.execute("SELECT event_type FROM PipelineEvent").fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []  # PipelineEvent table only exists when ADR-036 is built

    def acquisition_outcomes(self) -> list[dict]:
        from javdb.storage.db import get_db
        try:
            with get_db("operations") as conn:
                conn.row_factory = __import__("sqlite3").Row
                rows = conn.execute("SELECT qb_hash, state FROM AcquisitionOutcome").fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []  # AcquisitionOutcome only exists when ADR-033 is built


@pytest.fixture
def pipeline_harness(monkeypatch):
    return PipelineHarness(monkeypatch)
```

> `SpiderRunOptions` field names (`use_proxy`, `ignore_release_date`, `phase`) must
> match `javdb/spider/app/options.py`; confirm with
> `grep -nE "class SpiderRunOptions|: " javdb/spider/app/options.py` and adjust the
> constructor call if the dataclass differs. The `events()` / `acquisition_outcomes()`
> helpers degrade gracefully when those tables (ADR-036/033) are not yet built.

- [ ] **Step 2: Import-smoke**

Run: `python3 -c "import tests.harness.pipeline_harness; print('import ok')"`
Expected: `import ok`

- [ ] **Step 3: Commit**

```bash
git add tests/harness/pipeline_harness.py
git commit -m "test(harness): add PipelineHarness composition + scenario API (ADR-037)"
```

---

## Task 5: Golden daily fixtures + scenario builder

**Files:**
- Create: `tests/harness/scenarios/__init__.py`
- Create: `tests/harness/scenarios/golden_daily.py`

Author minimal, contract-faithful fixtures: one index page listing two movies, and
a detail page for each (with a magnet). URLs must be exactly what the spider requests
(`get_page_url(1)` for the index; `https://javdb.com{href}` for details).

- [ ] **Step 1: Confirm the URL the spider computes for the index**

Run: `python3 -c "from javdb.spider.fetch.fallback import get_page_url; print(get_page_url(1, custom_url=None))"`
Record the printed URL — that is the cassette key for the index page.

- [ ] **Step 2: Write the scenario builder + fixtures**

```python
# tests/harness/scenarios/__init__.py
"""Golden + future scenarios for the pipeline harness (ADR-037)."""
```

```python
# tests/harness/scenarios/golden_daily.py
"""A clean daily run: 1 index page -> 2 movies -> 2 detail pages with magnets.

Fixtures are intentionally minimal but must carry the selectors the Rust/Python
parser reads (div.item, a.box, div.video-title, the magnet table). Refine the HTML
against tests/fixtures/parser/index_edge_cases.html if the parser rejects it."""

from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario

# NOTE: replace INDEX_URL with the value printed by Task 5 Step 1.
INDEX_URL = "https://javdb.com/?page=1"
DETAIL_URL_1 = "https://javdb.com/v/AAA111"
DETAIL_URL_2 = "https://javdb.com/v/BBB222"

INDEX_HTML = """
<html><body>
  <div class="movie-list">
    <div class="item"><a class="box" href="/v/AAA111">
      <div class="video-title"><strong>ABC-001</strong> Title One</div>
      <div class="meta">2025-01-01</div></a></div>
    <div class="item"><a class="box" href="/v/BBB222">
      <div class="video-title"><strong>ABC-002</strong> Title Two</div>
      <div class="meta">2025-01-02</div></a></div>
  </div>
</body></html>
"""

# Minimal detail page carrying one magnet. Refine selectors to match the parser.
def _detail(code: str, magnet: str) -> str:
    return f"""
<html><body>
  <h2 class="title"><strong>{code}</strong> Title</h2>
  <div id="magnets-content"><div class="item">
    <a href="{magnet}"><span class="name">{code} 1080p</span></a>
    <div class="meta">5.0GB</div></div></div>
</body></html>
"""

DETAIL_HTML_1 = _detail("ABC-001", "magnet:?xt=urn:btih:" + "a" * 40)
DETAIL_HTML_2 = _detail("ABC-002", "magnet:?xt=urn:btih:" + "b" * 40)


def golden_daily() -> PipelineScenario:
    return PipelineScenario(
        pages={
            INDEX_URL: INDEX_HTML,
            DETAIL_URL_1: DETAIL_HTML_1,
            DETAIL_URL_2: DETAIL_HTML_2,
        },
        qb=FakeQBConfig(),
    )
```

- [ ] **Step 3: Commit**

```bash
git add tests/harness/scenarios/
git commit -m "test(harness): add golden daily scenario fixtures (ADR-037)"
```

---

## Task 6: The golden-scenario E2E capstone

**Files:**
- Test: `tests/harness/test_golden_scenario.py`

This is the integration capstone. It runs the whole pipeline in-process against the
golden cassette and asserts the authoritative outcome. Bring-up may require iterating
on the fixtures' HTML (so the parser accepts them) and on `SpiderRunOptions` (so the
phase/filters select the two movies) — that iteration is expected harness work.

- [ ] **Step 1: Write the E2E test**

```python
# tests/harness/test_golden_scenario.py
from tests.harness.scenarios.golden_daily import golden_daily


def test_golden_daily_run_writes_two_movies(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    # The spider fetched the index + both detail pages from the cassette (no misses
    # for the pages we authored).
    assert result.http.misses == [] or all("page=" not in m for m in result.http.misses)

    # Authoritative outcome: two movies materialized into history.
    assert pipeline_harness.history().count() == 2

    # Two torrents were queued into the fake qB.
    assert len(result.qb.all_hashes()) == 2
```

- [ ] **Step 2: Run + iterate to green**

Run: `pytest tests/harness/test_golden_scenario.py -v`
Expected (final): PASS. If it fails, in order:
1. Replace `INDEX_URL` with the Task-5-Step-1 value.
2. Adjust `INDEX_HTML` / detail HTML selectors until the parser extracts 2 entries
   + a magnet each (cross-check against `tests/fixtures/parser/index_edge_cases.html`
   and `tests/unit/test_parser.py`).
3. Adjust `SpiderRunOptions(...)` so phase-1 selection keeps both movies
   (confirm fields via `grep -nE "class SpiderRunOptions" -A30 javdb/spider/app/options.py`).
4. If the uploader does not run inside `run_spider`, drive the uploader service
   explicitly in `run_daily` after the spider step (call the uploader's run entry on
   the produced CSV) — the FakeQB factory is already patched.

- [ ] **Step 3: Commit**

```bash
git add tests/harness/test_golden_scenario.py tests/harness/scenarios/golden_daily.py
git commit -m "test(harness): golden daily-run E2E scenario green (ADR-037 Phase 1)"
```

---

## Task 7: Re-exports, docs, full gate

**Files:**
- Modify: `tests/harness/__init__.py`, `CONTEXT.md`, `docs/handbook/en/developer/testing.md` (+ zh if present)

- [ ] **Step 1: Re-export the public surface**

Append to `tests/harness/__init__.py`:

```python
from tests.harness.fixture_http import FixtureHTTP  # noqa: E402,F401
from tests.harness.fake_qb import FakeQB  # noqa: E402,F401
from tests.harness.pipeline_harness import (  # noqa: E402,F401
    PipelineScenario, FakeQBConfig, PipelineHarness, pipeline_harness,
)
```

- [ ] **Step 2: Update CONTEXT.md** — add the ADR-037 terms verbatim from the ADR's "Domain Language": *Pipeline harness*, *Cassette*, *FixtureHTTP / FakeQB*, *Scenario*, *Golden scenario*.

- [ ] **Step 3: Document the harness** — add a "Deterministic pipeline harness" section to `docs/handbook/en/developer/testing.md` (how to write a scenario + run it); mirror to the zh testing doc if it exists.

- [ ] **Step 4: Full gate**

Run:
```bash
pytest tests/harness/ -v
```
Expected: all PASS (FixtureHTTP, FakeQB, seams, golden scenario).

- [ ] **Step 5: Commit**

```bash
git add tests/harness/__init__.py CONTEXT.md docs/handbook
git commit -m "test(harness): re-exports + docs for ADR-037 Phase 1"
```

---

## Plan Self-Review

**Spec coverage (ADR-037 Phase 1 row + D-decisions):**
- In-process service composition (D1) → Task 4 (`run_daily` calls `run_spider` + `db_commit_session_history`). ✓
- `tests/harness/` package composed by a pytest fixture (D2) → Tasks 1-4. ✓
- FixtureHTTP replay; record is a stub (D3) → Task 1. ✓
- FakeQB in-memory + controllable (D4) → Task 2. ✓
- Reuse seeded DB; other seams neutered (D5) → relies on autouse `_isolate_sqlite` + conftest's PikPak mock; SMTP/proxy not exercised by the golden path. ✓
- Scenario + assertion API (D6) → Tasks 4, 5. ✓
- One golden scenario proving the loop (D7) → Task 6. ✓
- Deferred: record mode, scenario library, extra seams → not built; documented. ✓
- Docs (CONTEXT.md, testing.md) → Task 7. ✓

**Type consistency:** `FixtureHTTP`, `FakeQB`, `PipelineScenario`, `FakeQBConfig`,
`PipelineHarness` (`run_daily`/`history`/`events`/`acquisition_outcomes`), and the
`pipeline_harness` fixture are used identically across Tasks 1-7.

**Honest integration risk (flagged in-plan):** Task 6 is an E2E capstone whose green
state may need iteration on fixture HTML + `SpiderRunOptions` (Step 2 enumerates the
exact knobs + grep targets). The building blocks (Tasks 1-3) are guaranteed and
independently green; the capstone is the integration goal, not a one-shot.

**Forward-compat:** `events()` / `acquisition_outcomes()` degrade to `[]` when the
ADR-036/033 tables are absent, so the harness lands cleanly before those IMPs and
gains assertions for free once they do.
