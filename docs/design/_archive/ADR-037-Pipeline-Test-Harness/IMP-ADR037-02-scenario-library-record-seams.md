# IMP-ADR037-02: Scenario Library + Record Mode + Seams (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-037](ADR-037-deterministic-pipeline-test-harness.md) (umbrella) — this is **Phase 2** of three. Builds directly on the shipped [IMP-ADR037-01](IMP-ADR037-01-harness-core.md).

**Goal:** Grow the in-process `tests/harness/` from one golden scenario into a **scenario library** that drives the pipeline's load-bearing behaviours end to end — closed-loop completion (ADR-033), site-contract drift gating (ADR-035), failure rollback — plus an **env-gated record mode** that refreshes cassettes from live javdb, and **fakes for the remaining side-effecting seams** (SMTP; pikpak/rclone neuter building blocks).

**Architecture:** Everything stays test-support only (ADR-037 D2) — this IMP touches `tests/harness/**`, `CONTEXT.md`, and the handbook; **no production code changes**. New scenarios reuse the existing `golden_daily()` cassette and vary one axis each: completion runs the real reconciler against a `FakeQB` whose torrents are then `complete()`d; drift seeds a critical `ParseRunFieldFill` row through a new `run_daily(before_commit=...)` hook so the real `commit_session` gate fires; failure reuses the IMP-01 `fail_adds=True` path and then drives the production `db_rollback_session`. The closed-loop and sentinel writes are visible to the harness's assertions because [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md) made the ops persistence modules resolve their DB-path constants at call time (the planned `_install` repoint is obsolete — see the ✅ fact below). Record mode wires the long-reserved `FixtureHTTP.record_miss` flag to a `live_fetch` callback (deterministically testable in CI with an injected fake; real network is dev-only behind `JAVDB_HARNESS_RECORD`).

**Tech Stack:** Python 3, `pytest` (`monkeypatch`, `tmp_path`), `sqlite3`, the existing `tests/conftest.py` autouse fixtures (`_isolate_sqlite`, `_disable_git_side_effects`), and the shipped `tests/harness/` package.

---

## Confirmed seams & facts (verified against the code at plan time)

These were read directly from the tree; copy them verbatim — do not re-derive.

- **`init_db` builds every table the scenarios touch** in the single collapsed temp DB that `_isolate_sqlite` creates: `PipelineEvent` (`javdb/storage/db/_db_migrations.py:410`), `ParseRunFieldFill` (`:443`), `AcquisitionOutcome` (`:599`), `OpsIncidents` (`:372`), `MovieHistory`, `TorrentHistory`, `PendingMovieHistoryWrites`, `PendingTorrentHistoryWrites`. So every read/seed below works under the autouse fixture with **no extra schema setup**.
- **Closed-loop is already wired into the uploader.** `javdb/integrations/qb/uploader/service.py:741` calls `_record_queued_acquisition(torrent, options.session_id)` after each successful add (unconditional — no backend guard), which calls `javdb.ops.reconcile.service.record_queued`. So after `run_daily(golden_daily())` there are **2 `AcquisitionOutcome` rows in state `queued`** (hashes `"a"*40`, `"b"*40`), independent of the pending→commit path.
- **The reconciler.** `javdb.ops.reconcile.service.run(options: ReconcileOptions, *, repo=None, qb_client=None) -> ReconcileResult`. `ReconcileOptions(sources=("qb",), categories=("JavDB","Ad Hoc"), stalled_after_days=7, dry_run=False, infer_absent=True)`. `ReconcileResult` has `observed, outcomes_updated, marked_downloading, marked_completed, marked_stalled, marked_failed, errors`. It reads qB via `client.get_torrents_multiple_categories(list(categories), torrent_filter="all")` when the client has no `get_torrents` attr (FakeQB does not), filtering by category. `QbCollector.collect` reads `t["hash"]`, `t["progress"]`, `t["state"]`; `completed = progress == 1.0 or state in {"uploading","seeding","stalledUP","pausedUP","queuedUP","forcedUP","checkingUP"}`. **`FakeQB.complete(h)` sets `progress=1.0, state="uploading"`** → collector yields `state="completed"`. `run()` then flips each active `queued` outcome to `completed` (`marked_completed += 1`).
- **✅ Import-time DB-path binding — RESOLVED by [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md); no harness fix needed.** At plan-authoring time `javdb/ops/reconcile/persistence.py` and `javdb/ops/sentinel/persistence.py` bound `OPERATIONS_DB_PATH`/`REPORTS_DB_PATH` **at module load**, so `_isolate_sqlite`'s later repath never reached them — `record_queued`/`reconcile` and the sentinel wrote/read a **non-test** DB and `acquisition_outcomes()` saw nothing. **BFR-016 (committed 2026-06-04, ~30 min after this plan was written) rewrote both modules to resolve the path at *call time*** (`with get_db(_db.OPERATIONS_DB_PATH)` / `_db.REPORTS_DB_PATH`), so the autouse `_isolate_sqlite` monkeypatch now reaches them. **A fresh probe (2026-06-07) confirmed `acquisition_outcomes()` returns exactly 2 `queued` rows after `run_daily(golden_daily())` with NO harness repoint.** Task 4 Step 5's planned repoint is therefore obsolete and is kept as a documented no-op — a `monkeypatch.setattr` on the now-removed module attribute would itself raise `AttributeError`. (The broader test-isolation latent bug this stemmed from is exactly what BFR-016 records.)
- **The drift gate.** `javdb.storage.sessions.commit.commit_session(CommitRequest(session_id=...))` calls `_gate_site_contract_drift` → `javdb.ops.sentinel.service.evaluate_session(session_id)` (no options → `min_sample = cfg("SENTINEL_MIN_SAMPLE", 30)`). A **critical** verdict raises `javdb.storage.sessions.commit.SiteContractDriftError` **before** any drain, and `evaluate_session` writes an `OpsIncidents` row with `incident_type='site_drift'`. `javdb.ops.sentinel.detectors.evaluate` marks a fill critical when `sample_count >= min_sample` **and** `fill_rate < spec["min_fill"]`; `index.video_code` is `critical, min_fill=0.99` (`javdb/spider/parse_contract.py`). So `FieldFill("index","video_code",0.1,50)` → critical.
- **Seed fills via the service seam.** `javdb.ops.sentinel.service.persist_run(fills, *, session_id=None, repo=None) -> int`; `FieldFill(page_type, field, fill_rate, sample_count)` from `javdb.ops.sentinel.models`. It opens the reports DB (`open_fill_repo`) → collapsed temp DB.
- **Rollback (verified 2026-06-07 during impl).** `db_rollback_session` is re-exported from `javdb.storage.db` (verified importable). For an `in_progress` pending session it transitions `in_progress→failed`, deletes the session's rows from `PendingMovieHistoryWrites`/`PendingTorrentHistoryWrites` (pending column is `SessionId`), **and `_rollback_reports` then DELETEs the `ReportSessions` row itself** (only when not committed — committed sessions are preserved; see `javdb/storage/db/_db_rollback.py:305` and the NOTE at `:537`). So after a full rollback `get_state(session_id).status` is **`None`** (the row is gone), NOT `'failed'` — the `failed` transition is transient. (The original plan asserted `== "failed"`; that was wrong. Test asserts `is None`.) Session state: `javdb.storage.sessions.lifecycle.get_state(session_id).status`.
- **Notify (verified 2026-06-07; survived the ADR-039 refactor).** `javdb.integrations.notify.email.service.run_email_notification(options, *, deliver: bool = True) -> EmailNotificationResult` (`service.py:147`; `deliver` defaults True = email-active, so omitting it is correct). The SMTP seam is `send_email` **imported into that module's namespace** (`service.py:92`, called `service.py:612`) — patch `javdb.integrations.notify.email.service.send_email`. **⚠ The real `send_email(subject, body, attachments=None, dry_run=False, session_id=None)` takes a 5th `session_id` arg (ADR-046 P5; defined in `delivery.py`), and the call site passes `session_id=options.session_id`** — so the `FakeSMTP.send_email` drop-in MUST absorb the extra kwarg (`**kwargs`, or an explicit `session_id=None`) or the patched call raises `TypeError`. `EmailNotificationOptions(csv_path, mode="daily", dry_run=False, from_pipeline=False, session_id=None, ...)` from `javdb.integrations.notify.email.options`; result has `.email_sent`, `.subject`, `.exit_code`. Git side-effects are already neutered by the autouse `_disable_git_side_effects` fixture.
- **Event boundary (important — do not over-assert).** `RunStarted` is emitted inside `run_spider` (`javdb/spider/app/run_service.py:601`) so it fires in the harness. **`SessionCommitted`/`SessionFailed` are emitted only by the CLI commit path** (`apps/cli/db/commit_session.py:483/405/...`), **not** by the API `commit_session` the harness calls. So a clean harness run's `events()` is `["RunStarted"]`. Assert that; do **not** assert `SessionCommitted` (it would require driving the CLI, a non-goal here).

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `tests/harness/cassette.py` | Create | On-disk cassette `save_cassette`/`load_cassette` (manifest.json + `.html` bodies) |
| `tests/harness/fixture_http.py` | Modify | Wire `record_miss` → live-fetch-on-miss + `recorded` capture; add `record_enabled()` env gate |
| `tests/harness/recording.py` | Create | Dev-only `record_pages(urls)` live-fetch helper (real `RequestHandler`) |
| `tests/harness/fake_qb.py` | Modify | Add `categories()` accessor |
| `tests/harness/fake_smtp.py` | Create | `FakeSMTP` capture (drop-in for `send_email`) |
| `tests/harness/fake_external.py` | Create | `neuter_rclone(monkeypatch)` + `assert_pikpak_neutered()` building blocks |
| `tests/harness/pipeline_harness.py` | Modify | `run_daily(before_commit=...)`; `HarnessResult.commit_error`; `reconcile(...)`; `run_notify(...)`; `self.smtp` (the planned `_install` ops-persistence repoint is obsolete — BFR-016, see the ✅ fact above) |
| `tests/harness/test_cassette.py` | Create | Cassette round-trip tests |
| `tests/harness/test_record_mode.py` | Create | Record-on-miss tests (fake `live_fetch`, no network) |
| `tests/harness/test_scenario_completion.py` | Create | Completion → closed-loop scenario |
| `tests/harness/test_scenario_drift.py` | Create | Drift → commit-gate scenario |
| `tests/harness/test_scenario_failure_rollback.py` | Create | Failure → rollback scenario |
| `tests/harness/test_fake_smtp.py` | Create | FakeSMTP unit tests |
| `tests/harness/test_scenario_notify.py` | Create | Daily notify email capture (capstone) |
| `tests/harness/test_fake_external.py` | Create | rclone/pikpak neuter smoke tests |
| `tests/harness/__init__.py` | Modify | Re-export the new public surface |
| `CONTEXT.md` | Modify | Add Phase-2 domain terms |
| `docs/handbook/en/developer/pipeline-test-harness.md` | Modify | Document record mode + the scenario library |
| `docs/handbook/zh/developer/pipeline-test-harness.md` | Modify | Paired zh translation |

**Naming contract (verbatim across tasks):**
`save_cassette(cassette_dir: str, pages: dict[str,str]) -> None`; `load_cassette(cassette_dir: str) -> dict[str,str]`; `record_enabled() -> bool` (env `JAVDB_HARNESS_RECORD`); `FixtureHTTP(pages, *, record_miss=False, live_fetch=None)` with new attr `recorded: dict`; `record_pages(urls, *, use_proxy=False, use_cookie=False) -> dict`; `FakeQB.categories() -> set`; `FakeSMTP(*, succeed=True)` with `.send_email(subject, body, attachments=None, dry_run=False, **kwargs) -> bool` (the `**kwargs` absorbs the real `send_email`'s `session_id`) and `.sent: list[SentEmail]`, `SentEmail(subject, body, attachments=(), dry_run=False)`; `neuter_rclone(monkeypatch) -> None`; `assert_pikpak_neutered() -> None`; `PipelineHarness.run_daily(scenario, *, before_commit=None)`; `HarnessResult(qb, http, spider_result, uploader_result, commit_result, commit_error=None)`; `PipelineHarness.reconcile(*, qb_client=None, categories=None, infer_absent=False) -> ReconcileResult`; `PipelineHarness.run_notify(csv_path, session_id, *, dry_run=False) -> EmailNotificationResult` with `self.smtp: FakeSMTP | None`.

> **Phase-3-gated (NOT in this plan):** golden-run record/replay snapshot diffing → [IMP-ADR037-03](IMP-ADR037-03-golden-run-diff.md).

---

## Task 1: On-disk cassette load/save

**Files:**
- Create: `tests/harness/cassette.py`
- Test: `tests/harness/test_cassette.py`

A cassette is a directory mapping request URL → response **body** (ADR-037 D3). `RequestHandler.get_page`'s contract is `str | None` (HTML body), so only the body is stored at this seam — status/headers are N/A here.

- [ ] **Step 1: Write the failing test**

```python
# tests/harness/test_cassette.py
import os

from tests.harness.cassette import load_cassette, save_cassette


def test_cassette_round_trip(tmp_path):
    pages = {
        "https://javdb.com?page=1": "<html>index</html>",
        "https://javdb.com/v/AAA111": "<html>detail A</html>",
    }
    cassette_dir = str(tmp_path / "cass")
    save_cassette(cassette_dir, pages)
    assert load_cassette(cassette_dir) == pages


def test_cassette_writes_manifest_and_bodies(tmp_path):
    cassette_dir = str(tmp_path / "cass")
    save_cassette(cassette_dir, {"https://javdb.com/v/AAA111": "<html>a</html>"})
    assert os.path.exists(os.path.join(cassette_dir, "manifest.json"))
    assert os.path.exists(os.path.join(cassette_dir, "0001.html"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness/test_cassette.py -v`
Expected: FAIL — `ModuleNotFoundError: tests.harness.cassette`

- [ ] **Step 3: Write the cassette module**

```python
# tests/harness/cassette.py
"""On-disk cassette load/save for the pipeline harness (ADR-037 D3, Phase 2).

A cassette is a directory: ``manifest.json`` maps each request URL to a body
file, with one ``NNNN.html`` per page. Only the response *body* is stored —
``RequestHandler.get_page``'s contract is ``str | None`` (the HTML body), so
status/headers are not part of this seam."""

from __future__ import annotations

import json
import os

_MANIFEST = "manifest.json"


def save_cassette(cassette_dir: str, pages: dict[str, str]) -> None:
    """Write ``pages`` (URL -> HTML body) to ``cassette_dir`` as a cassette.

    Pages are sorted for a stable, diff-friendly manifest/body layout."""
    os.makedirs(cassette_dir, exist_ok=True)
    entries = []
    for i, (url, body) in enumerate(sorted(pages.items()), start=1):
        fname = f"{i:04d}.html"
        with open(os.path.join(cassette_dir, fname), "w", encoding="utf-8") as f:
            f.write(body)
        entries.append({"url": url, "file": fname})
    with open(os.path.join(cassette_dir, _MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"pages": entries}, f, ensure_ascii=False, indent=2)


def load_cassette(cassette_dir: str) -> dict[str, str]:
    """Read a cassette directory back into a ``{url: body}`` dict."""
    with open(os.path.join(cassette_dir, _MANIFEST), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    pages: dict[str, str] = {}
    for entry in manifest["pages"]:
        with open(os.path.join(cassette_dir, entry["file"]), "r", encoding="utf-8") as bf:
            pages[entry["url"]] = bf.read()
    return pages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/harness/test_cassette.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/harness/cassette.py tests/harness/test_cassette.py
git commit -m "test(harness): add on-disk cassette load/save (ADR-037 Phase 2)"
```

---

## Task 2: `FixtureHTTP` record-on-miss + env gate

**Files:**
- Modify: `tests/harness/fixture_http.py`
- Test: `tests/harness/test_record_mode.py`

Wire the long-reserved `record_miss` stub: when armed (`record_miss=True` **and** env `JAVDB_HARNESS_RECORD` truthy **and** a `live_fetch` callback is supplied), a cassette miss performs the live fetch, remembers the body in `self.recorded`, and serves it. The replay path (no `live_fetch`, env off) is byte-for-byte the current behaviour, so IMP-01 tests are unaffected. The CI test injects a **fake** `live_fetch` so this is deterministic and never hits the network.

- [ ] **Step 1: Write the failing test**

```python
# tests/harness/test_record_mode.py
from tests.harness.fixture_http import FixtureHTTP, record_enabled


def test_record_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "1")
    assert record_enabled() is True
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "off")
    assert record_enabled() is False
    monkeypatch.delenv("JAVDB_HARNESS_RECORD", raising=False)
    assert record_enabled() is False


def test_record_on_miss_fetches_and_remembers(monkeypatch):
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "1")
    calls = []

    def fake_live(url, *a, **k):
        calls.append(url)
        return "<html>live</html>"

    http = FixtureHTTP({}, record_miss=True, live_fetch=fake_live)
    assert http.get_page("https://javdb.com/v/NEW") == "<html>live</html>"
    assert calls == ["https://javdb.com/v/NEW"]
    assert http.recorded == {"https://javdb.com/v/NEW": "<html>live</html>"}
    # A recorded page is now a replay hit, not a tracked miss.
    assert http.misses == []
    assert http.get_page("https://javdb.com/v/NEW") == "<html>live</html>"
    assert calls == ["https://javdb.com/v/NEW"]  # second read served from cassette


def test_record_disarmed_without_env_stays_replay_only(monkeypatch):
    monkeypatch.delenv("JAVDB_HARNESS_RECORD", raising=False)
    http = FixtureHTTP({}, record_miss=True, live_fetch=lambda *a, **k: "x")
    assert http.get_page("https://javdb.com/v/NEW") is None
    assert "https://javdb.com/v/NEW" in http.misses
    assert http.recorded == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness/test_record_mode.py -v`
Expected: FAIL — `ImportError: cannot import name 'record_enabled'`

- [ ] **Step 3: Rewrite `fixture_http.py`**

```python
# tests/harness/fixture_http.py
"""Replay javdb responses from a cassette; optionally record live on a miss
(ADR-037 D3).

Drop-in for ``RequestHandler.get_page``: same ``(url, **kwargs) -> str | None``
shape. Replay is the default and the only behaviour CI ever uses. Record mode
is dev-only and triple-gated: the scenario must pass ``record_miss=True``, a
``live_fetch`` callback must be supplied, AND the ``JAVDB_HARNESS_RECORD`` env
var must be truthy. On a miss under those conditions the live body is fetched,
remembered in ``recorded`` (for ``save_cassette``), and served."""

from __future__ import annotations

import os
from typing import Callable, Optional

_RECORD_ENV = "JAVDB_HARNESS_RECORD"


def record_enabled() -> bool:
    """True when dev-only record mode is armed via env (read at call time)."""
    raw = os.environ.get(_RECORD_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _norm(url: str) -> str:
    return url.rstrip("/")


class FixtureHTTP:
    def __init__(
        self,
        pages: dict,
        *,
        record_miss: bool = False,
        live_fetch: Optional[Callable[..., Optional[str]]] = None,
    ) -> None:
        """Replay ``pages`` (URL -> HTML) at the get_page seam.

        ``record_miss`` + ``live_fetch`` + ``record_enabled()`` together arm
        record mode (ADR-037 D3); otherwise misses are merely tracked and
        ``None`` is returned (replay-only, the CI default)."""
        self._pages = {_norm(k): v for k, v in pages.items()}
        self.record_miss = record_miss
        self.live_fetch = live_fetch
        self.misses: list[str] = []
        self.requests: list[str] = []
        self.recorded: dict[str, str] = {}

    def get_page(self, url: str, *args, **kwargs) -> Optional[str]:
        self.requests.append(url)
        key = _norm(url)
        hit = self._pages.get(key)
        if hit is not None:
            return hit
        if self.record_miss and self.live_fetch is not None and record_enabled():
            body = self.live_fetch(url, *args, **kwargs)
            if body is not None:
                self._pages[key] = body
                self.recorded[key] = body
                return body
            # Live fetch itself failed — fall through to a tracked miss.
        self.misses.append(url)
        return None
```

- [ ] **Step 4: Run the new + existing FixtureHTTP tests**

Run: `pytest tests/harness/test_record_mode.py tests/harness/test_fixture_http.py -v`
Expected: PASS (all). The IMP-01 `test_fixture_http.py` must stay green — the new params are keyword-only with defaults.

- [ ] **Step 5: Commit**

```bash
git add tests/harness/fixture_http.py tests/harness/test_record_mode.py
git commit -m "test(harness): wire FixtureHTTP record-on-miss behind JAVDB_HARNESS_RECORD (ADR-037)"
```

---

## Task 3: Dev-only live recording helper

**Files:**
- Create: `tests/harness/recording.py`
- Test: `tests/harness/test_record_mode.py` (append one guarded test)

`record_pages(urls)` fetches each URL through a **real** `RequestHandler` (reusing the full proxy/CF-bypass/retry stack) and returns a `{url: body}` dict ready for `save_cassette`. It is dev-only: the test is skipped unless `JAVDB_HARNESS_RECORD` is set, so CI never dials javdb.

- [ ] **Step 1: Write the recording helper**

```python
# tests/harness/recording.py
"""Dev-only cassette recording for the pipeline harness (ADR-037 D3, Phase 2).

NEVER runs in CI. Arm with ``JAVDB_HARNESS_RECORD=1``. ``record_pages`` fetches
each URL through a real ``RequestHandler`` (the full proxy / CF-bypass / retry
machinery), so the recorded bodies match what production would scrape. Pair with
``save_cassette`` to refresh a cassette when javdb's HTML changes.

Example (dev shell, with config.py present)::

    JAVDB_HARNESS_RECORD=1 python3 -c "
    from tests.harness.recording import record_pages
    from tests.harness.cassette import save_cassette
    urls = ['https://javdb.com?page=1', 'https://javdb.com/v/<code>']
    save_cassette('tests/harness/scenarios/cassettes/daily', record_pages(urls))
    "
"""

from __future__ import annotations

from tests.harness.fixture_http import record_enabled


def record_pages(urls, *, use_proxy: bool = False, use_cookie: bool = False) -> dict:
    """Fetch each URL live via a real RequestHandler. Dev-only.

    Raises ``RuntimeError`` if record mode is not armed, so this can never
    accidentally dial javdb from a normal test run."""
    if not record_enabled():
        raise RuntimeError(
            "record_pages requires JAVDB_HARNESS_RECORD=1 (dev-only live fetch)."
        )
    from javdb.infra.request import RequestHandler

    handler = RequestHandler()
    pages: dict[str, str] = {}
    for url in urls:
        body = handler.get_page(
            url, use_proxy=use_proxy, use_cookie=use_cookie, module_name="spider"
        )
        if body is not None:
            pages[url] = body
    return pages
```

- [ ] **Step 2: Append the guarded test**

```python
# tests/harness/test_record_mode.py  (append)
import pytest

from tests.harness.fixture_http import record_enabled


def test_record_pages_refuses_without_env(monkeypatch):
    monkeypatch.delenv("JAVDB_HARNESS_RECORD", raising=False)
    from tests.harness.recording import record_pages
    with pytest.raises(RuntimeError):
        record_pages(["https://javdb.com?page=1"])


@pytest.mark.skipif(not record_enabled(), reason="dev-only live network record")
def test_record_pages_live_smoke():
    from tests.harness.recording import record_pages
    pages = record_pages(["https://javdb.com?page=1"])
    assert pages  # at least the index recorded
```

- [ ] **Step 3: Run (the live test is skipped)**

Run: `pytest tests/harness/test_record_mode.py -v`
Expected: PASS; `test_record_pages_live_smoke` shows `SKIPPED`.

- [ ] **Step 4: Commit**

```bash
git add tests/harness/recording.py tests/harness/test_record_mode.py
git commit -m "test(harness): add dev-only record_pages live cassette helper (ADR-037)"
```

---

## Task 4: `FakeQB.categories()` + `PipelineHarness.reconcile()`

**Files:**
- Modify: `tests/harness/fake_qb.py`
- Modify: `tests/harness/pipeline_harness.py`
- Test: `tests/harness/test_fake_qb.py` (append)

The reconciler filters qB by category. `categories()` lets a scenario drive it with the exact categories the uploader assigned, independent of `config.py`'s `TORRENT_CATEGORY`. `reconcile()` runs the real `javdb.ops.reconcile.service.run` in-process against `FakeQB`.

- [ ] **Step 1: Write the failing test for `categories()`**

```python
# tests/harness/test_fake_qb.py  (append)
def test_categories_reflects_added_torrents():
    from tests.harness.fake_qb import FakeQB
    qb = FakeQB()
    qb.add_torrent("magnet:?xt=urn:btih:" + "a" * 40, category="JavDB")
    qb.add_torrent("magnet:?xt=urn:btih:" + "b" * 40, category="Ad Hoc")
    assert qb.categories() == {"JavDB", "Ad Hoc"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/harness/test_fake_qb.py::test_categories_reflects_added_torrents -v`
Expected: FAIL — `AttributeError: 'FakeQB' object has no attribute 'categories'`

- [ ] **Step 3: Add `categories()` to `FakeQB`**

Append inside the `FakeQB` class in `tests/harness/fake_qb.py`, after `all_hashes`:

```python
    def categories(self) -> set:
        """The set of qB categories across current torrents (ADR-037 Phase 2).

        Lets a closed-loop scenario drive the reconciler with the exact
        categories the uploader actually assigned, independent of config."""
        return {t["category"] for t in self._torrents.values()}
```

- [ ] **Step 4: Add `reconcile()` to `PipelineHarness`**

Add this method to the `PipelineHarness` class in `tests/harness/pipeline_harness.py`, after `acquisition_outcomes`:

```python
    def reconcile(self, *, qb_client=None, categories=None,
                  infer_absent: bool = False):
        """Run the real ADR-033 closed-loop reconciler in-process.

        Defaults to reconciling against this harness's FakeQB, scoped to the
        categories the uploader actually used (so it is config-independent).
        ``infer_absent`` defaults to False for determinism: a torrent absent
        from the (fake) qB read must not be inferred stalled/failed here."""
        from javdb.ops.reconcile.models import ReconcileOptions
        from javdb.ops.reconcile.service import run as reconcile_run

        client = qb_client if qb_client is not None else self.qb
        cats = tuple(categories) if categories is not None else tuple(client.categories())
        return reconcile_run(
            ReconcileOptions(sources=("qb",), categories=cats, infer_absent=infer_absent),
            qb_client=client,
        )
```

- [ ] **Step 5: (NO-OP) ops-persistence repoint — obsoleted by [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md)**

This step originally appended a repoint of `javdb.ops.reconcile.persistence.OPERATIONS_DB_PATH` / `javdb.ops.sentinel.persistence.REPORTS_DB_PATH` to `_install`, because those modules bound the paths at import time. **BFR-016 has since fixed this in production code** — both modules now resolve the path at call time (`get_db(_db.OPERATIONS_DB_PATH)` / `get_db(_db.REPORTS_DB_PATH)`), so `_isolate_sqlite`'s monkeypatch reaches them and the closed-loop/sentinel writes land in the test DB unaided. A 2026-06-07 probe confirmed `acquisition_outcomes()` returns 2 `queued` rows after `run_daily` with **no** repoint.

**Do not add the repoint.** `monkeypatch.setattr(_recon_persistence, "OPERATIONS_DB_PATH", ...)` would raise `AttributeError` — the module-level name no longer exists after BFR-016. No `_install` change is required for this step; it remains only to preserve task numbering.

- [ ] **Step 6: Run + import-smoke**

Run:
```bash
pytest tests/harness/test_fake_qb.py tests/harness/test_golden_scenario.py -v
python3 -c "import tests.harness.pipeline_harness; print('import ok')"
```
Expected: tests PASS; `import ok`.

- [ ] **Step 7: Commit**

```bash
git add tests/harness/fake_qb.py tests/harness/pipeline_harness.py tests/harness/test_fake_qb.py
git commit -m "test(harness): FakeQB.categories() + reconcile() (ADR-037)"
```

---

## Task 5: Scenario — completion closes the loop (ADR-033)

**Files:**
- Test: `tests/harness/test_scenario_completion.py`

End to end: the daily run queues two torrents (uploader's `record_queued` hook → 2 `queued` outcomes); the scenario marks both `complete()` in `FakeQB`; the real reconciler transitions `queued → completed`.

- [ ] **Step 1: Write the scenario test**

```python
# tests/harness/test_scenario_completion.py
"""ADR-037 Phase 2: completion closes the ADR-033 acquisition loop."""

from tests.harness.scenarios.golden_daily import golden_daily


def test_completion_closes_the_loop(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    # The uploader's record_queued hook staged two queued outcomes.
    queued = {o["qb_hash"]: o["state"] for o in pipeline_harness.acquisition_outcomes()}
    assert len(queued) == 2
    assert set(queued.values()) == {"queued"}

    # Simulate both downloads finishing in qB (progress=1.0, state=uploading).
    for qb_hash in result.qb.all_hashes():
        result.qb.complete(qb_hash)

    # The real reconciler closes the loop: queued -> completed.
    rec = pipeline_harness.reconcile()
    assert rec.marked_completed == 2
    assert rec.errors == []

    after = {o["qb_hash"]: o["state"] for o in pipeline_harness.acquisition_outcomes()}
    assert set(after.values()) == {"completed"}
```

- [ ] **Step 2: Run + iterate to green**

Run: `pytest tests/harness/test_scenario_completion.py -v`
Expected (final): PASS — a probe confirmed exactly 2 `queued` rows after `run_daily` and 2 `completed` after `reconcile()` (BFR-016's call-time path resolution makes this work with **no** harness repoint). If `acquisition_outcomes()` is `[]`, BFR-016 may have regressed — confirm `javdb/ops/reconcile/persistence.py` resolves `_db.OPERATIONS_DB_PATH` at call time (not bound at import). If `marked_completed == 0`, confirm the category match: `result.qb.categories()` must be non-empty and equal to what `reconcile()` passes (it derives `categories` from the FakeQB by default).

- [ ] **Step 3: Commit**

```bash
git add tests/harness/test_scenario_completion.py
git commit -m "test(harness): completion -> closed-loop scenario (ADR-037 Phase 2, ADR-033)"
```

---

## Task 6: `run_daily(before_commit=...)` + `HarnessResult.commit_error`

**Files:**
- Modify: `tests/harness/pipeline_harness.py`
- Test: `tests/harness/test_golden_scenario.py` (append a regression guard)

Add a hook to mutate state after staging but before the commit gate (the drift scenario seeds fills here), and capture a drift refusal instead of letting it crash `run_daily`. Production's CLI commit routes a critical-drift `SiteContractDriftError` to a failed session; the harness records it so a scenario can assert the gate fired **and** that pending rows were not promoted. Existing callers pass no `before_commit` and ignore `commit_error`, so IMP-01 tests are unaffected.

- [ ] **Step 1: Edit `HarnessResult` to carry `commit_error`**

Replace the `HarnessResult.__init__` in `tests/harness/pipeline_harness.py` with:

```python
class HarnessResult:
    def __init__(self, fake_qb, http, spider_result, uploader_result, commit_result,
                 commit_error=None):
        self.qb = fake_qb
        self.http = http
        self.spider_result = spider_result
        self.uploader_result = uploader_result
        self.commit_result = commit_result
        # Set when the ADR-035 commit gate refused the commit (critical drift).
        self.commit_error = commit_error
```

- [ ] **Step 2: Edit `run_daily` to accept the hook and catch drift**

In `tests/harness/pipeline_harness.py`, change the `run_daily` import line and signature, and wrap the commit. The full replacement method:

```python
    def run_daily(self, scenario: PipelineScenario, *, before_commit=None) -> HarnessResult:
        from javdb.spider.app.run_service import run_spider
        from javdb.integrations.qb.uploader.options import QbUploaderOptions
        from javdb.integrations.qb.uploader.service import run_uploader
        from javdb.storage.sessions.commit import (
            CommitRequest, SiteContractDriftError, commit_session,
        )

        self._install(scenario)

        # 1) Spider — fetches the cassette, stages pending history, returns the
        #    session id + CSV path (the active-session context is cleared on exit).
        spider_result = run_spider(self._daily_options())
        session_id = spider_result.session_id
        csv_path = spider_result.csv_path

        # 2) Uploader — reads the spider CSV, queues magnets into FakeQB.
        uploader_result = run_uploader(QbUploaderOptions(
            mode="daily", input_file=csv_path, proxy_override=False,
            from_pipeline=True, session_id=session_id,
        ))

        # ADR-037 Phase 2: optional hook to mutate state after staging but before
        # the commit gate (e.g. seed sentinel drift fills for the drift scenario).
        if before_commit is not None and session_id:
            before_commit(session_id)

        # 3) Commit — drains pending writes into MovieHistory / TorrentHistory.
        #    Gate on spider AND uploader success, mirroring DailyIngestion.yml's
        #    "Mark sessions as committed" step (if: ${{ success() }}). A critical
        #    site-contract drift verdict raises SiteContractDriftError before any
        #    drain; production's CLI commit routes that to a failed session, so the
        #    harness records it (commit_error) and leaves pending rows un-promoted.
        commit_result = None
        commit_error = None
        spider_ok = spider_result.exit_code == 0
        uploader_ok = uploader_result.exit_code == 0
        if session_id and spider_ok and uploader_ok:
            try:
                commit_result = commit_session(CommitRequest(session_id=session_id))
            except SiteContractDriftError as exc:
                commit_error = exc

        return HarnessResult(self.qb, self.http, spider_result, uploader_result,
                             commit_result, commit_error)
```

- [ ] **Step 3: Append a regression guard to the golden test**

```python
# tests/harness/test_golden_scenario.py  (append)
def test_clean_run_has_no_commit_error(pipeline_harness):
    from tests.harness.scenarios.golden_daily import golden_daily
    result = pipeline_harness.run_daily(golden_daily())
    assert result.commit_error is None
    assert result.commit_result is not None
```

- [ ] **Step 4: Run the golden + completion tests (no regressions)**

Run: `pytest tests/harness/test_golden_scenario.py tests/harness/test_scenario_completion.py -v`
Expected: PASS (all, including the IMP-01 cases).

- [ ] **Step 5: Commit**

```bash
git add tests/harness/pipeline_harness.py tests/harness/test_golden_scenario.py
git commit -m "test(harness): run_daily before_commit hook + commit_error capture (ADR-037)"
```

---

## Task 7: Scenario — site-contract drift gates the commit (ADR-035)

**Files:**
- Test: `tests/harness/test_scenario_drift.py`

Seed a critical `index.video_code` fill (far below the 0.99 contract floor, sample above the 30-row guard) via the `before_commit` hook, then assert the real `commit_session` gate refuses to promote pending writes and records a `site_drift` incident.

- [ ] **Step 1: Write the scenario test**

```python
# tests/harness/test_scenario_drift.py
"""ADR-037 Phase 2: critical site-contract drift gates the commit (ADR-035)."""

from javdb.ops.sentinel.models import FieldFill
from javdb.ops.sentinel.service import persist_run
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.sessions.commit import SiteContractDriftError

from tests.harness.scenarios.golden_daily import golden_daily


def _seed_critical_drift(session_id: str) -> None:
    # index.video_code is critical with min_fill=0.99; 0.10 fill over 50 samples
    # (>= the 30-row min_sample gate) is unambiguous critical drift.
    persist_run([FieldFill("index", "video_code", 0.10, 50)], session_id=session_id)


def test_drift_gate_refuses_commit(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily(), before_commit=_seed_critical_drift)

    # The ADR-035 commit gate raised, so nothing was promoted.
    assert isinstance(result.commit_error, SiteContractDriftError)
    assert result.commit_result is None
    assert pipeline_harness.history().count() == 0

    # The sentinel recorded a site_drift incident as a side effect of the gate.
    with get_db(REPORTS_DB_PATH) as conn:
        incidents = conn.execute(
            "SELECT COUNT(*) FROM OpsIncidents WHERE incident_type = 'site_drift'"
        ).fetchone()[0]
    assert incidents >= 1
```

- [ ] **Step 2: Run + iterate to green**

Run: `pytest tests/harness/test_scenario_drift.py -v`
Expected (final): PASS — a probe confirmed `SiteContractDriftError`, `history().count() == 0`, and one `site_drift` incident (BFR-016's call-time resolution covers this with **no** harness repoint). If `commit_error` is `None`, the verdict was not critical — confirm `cfg("SENTINEL_MIN_SAMPLE", 30)` is ≤ 50 in the test env (it defaults to 30; if a local `config.py` raises it above 50, bump the seeded `sample_count` to exceed it). If the incident count is 0, the sentinel wrote to a non-test DB — confirm `javdb/ops/sentinel/persistence.py` resolves `_db.REPORTS_DB_PATH` at call time (BFR-016); the seed `persist_run` and the gate's `evaluate_session` both route through it.

- [ ] **Step 3: Commit**

```bash
git add tests/harness/test_scenario_drift.py
git commit -m "test(harness): drift -> commit-gate scenario (ADR-037 Phase 2, ADR-035)"
```

---

## Task 8: Scenario — failure rolls back pending writes

**Files:**
- Test: `tests/harness/test_scenario_failure_rollback.py`

Reuse the IMP-01 `fail_adds=True` path (uploader fails → commit gated off → session left `in_progress` with staged pending rows), then drive the same `db_rollback_session` the cleanup-on-failure job uses and assert the session row is fully removed (rollback DELETEs it — `get_state().status is None`) with its pending rows deleted.

- [ ] **Step 1: Write the scenario test**

```python
# tests/harness/test_scenario_failure_rollback.py
"""ADR-037 Phase 2: a failed run rolls back its staged pending writes."""

from javdb.storage import db as _db
from javdb.storage.db import db_rollback_session, get_db
from javdb.storage.sessions.lifecycle import get_state

from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario
from tests.harness.scenarios.golden_daily import golden_daily


def _pending_movie_rows(session_id: str) -> int:
    # Resolve REPORTS_DB_PATH at call time (via _db) so the read honours the
    # autouse _isolate_sqlite monkeypatch; a direct import would bind the path
    # at import time and read the wrong DB.
    with get_db(_db.REPORTS_DB_PATH) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM PendingMovieHistoryWrites WHERE SessionId = ?",
            (session_id,),
        ).fetchone()[0]


def test_failure_rolls_back_pending(pipeline_harness):
    base = golden_daily()
    scenario = PipelineScenario(pages=base.pages, qb=FakeQBConfig(fail_adds=True))
    result = pipeline_harness.run_daily(scenario)

    # Uploader failed -> commit was gated off; the session is left for cleanup.
    assert result.uploader_result.exit_code != 0
    assert result.commit_result is None
    session_id = result.spider_result.session_id

    # The spider staged pending movie rows before the uploader failed.
    assert _pending_movie_rows(session_id) > 0

    # Drive the production rollback the cleanup-on-failure job uses.
    db_rollback_session(session_id, dry_run=False)

    # After a full rollback, _rollback_reports DELETEs the ReportSessions row
    # (see the NOTE in _db_rollback.py:_rollback_reports — the row is not left
    # with Status='failed'; it is removed entirely). get_state therefore returns
    # status=None for a successfully rolled-back session. Pending rows are gone
    # and nothing was promoted to history.
    assert get_state(session_id).status is None
    assert _pending_movie_rows(session_id) == 0
    assert pipeline_harness.history().count() == 0
```

- [ ] **Step 2: Run + iterate to green**

Run: `pytest tests/harness/test_scenario_failure_rollback.py -v`
Expected (final): PASS. If `_pending_movie_rows` is 0 before rollback, the spider may have produced 0 entries — confirm the golden fixtures still parse 2 movies (`pytest tests/harness/test_golden_scenario.py::test_golden_daily_run_writes_two_movies`) and that the read uses call-time `_db.REPORTS_DB_PATH` (a stale direct import reads the wrong DB). If `get_state(...).status` is not `None` after rollback, confirm the rollback ran and the session was `in_progress` not `committed` (`db_rollback_session` DELETEs the `ReportSessions` row only for a non-committed session) — a failed uploader must leave `commit_result is None`.

- [ ] **Step 3: Commit**

```bash
git add tests/harness/test_scenario_failure_rollback.py
git commit -m "test(harness): failure -> rollback scenario (ADR-037 Phase 2)"
```

---

## Task 9: `FakeSMTP` + `PipelineHarness.run_notify()`

**Files:**
- Create: `tests/harness/fake_smtp.py`
- Modify: `tests/harness/pipeline_harness.py`
- Test: `tests/harness/test_fake_smtp.py`, `tests/harness/test_scenario_notify.py`

`FakeSMTP` is a drop-in for `send_email` that captures every call. `run_notify` patches the notify module's `send_email` to the fake and runs the real `run_email_notification` against the spider's CSV — so a scenario can assert the pipeline's user-facing email without SMTP.

- [ ] **Step 1: Write the FakeSMTP unit test**

```python
# tests/harness/test_fake_smtp.py
from tests.harness.fake_smtp import FakeSMTP


def test_fake_smtp_captures_and_reports_success():
    smtp = FakeSMTP()
    assert smtp.send_email("Subject", "Body", ["report.txt"], False) is True
    assert len(smtp.sent) == 1
    assert smtp.sent[0].subject == "Subject"
    assert smtp.sent[0].attachments == ("report.txt",)
    assert smtp.sent[0].dry_run is False


def test_fake_smtp_can_simulate_failure():
    smtp = FakeSMTP(succeed=False)
    assert smtp.send_email("S", "B") is False
    assert smtp.sent[0].attachments == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/harness/test_fake_smtp.py -v`
Expected: FAIL — `ModuleNotFoundError: tests.harness.fake_smtp`

- [ ] **Step 3: Write `fake_smtp.py`**

```python
# tests/harness/fake_smtp.py
"""Capture emails instead of sending them (ADR-037 Phase 2).

Drop-in for ``javdb.integrations.notify.email.service.send_email``: same
``(subject, body, attachments=None, dry_run=False) -> bool`` shape. Records
every call so a scenario can assert what the pipeline would email — no SMTP."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SentEmail:
    subject: str
    body: str
    attachments: tuple = ()
    dry_run: bool = False


class FakeSMTP:
    def __init__(self, *, succeed: bool = True) -> None:
        self._succeed = succeed
        self.sent: list[SentEmail] = []

    def send_email(self, subject, body, attachments=None, dry_run=False, **kwargs) -> bool:
        # **kwargs absorbs the real send_email's extra args (e.g. session_id,
        # ADR-046 P5) so the patched seam never raises TypeError. Captured
        # SentEmail records only the user-facing fields a scenario asserts on.
        self.sent.append(SentEmail(subject, body, tuple(attachments or ()), bool(dry_run)))
        return self._succeed
```

- [ ] **Step 4: Add `run_notify` to `PipelineHarness`**

Add `self.smtp = None` to `PipelineHarness.__init__` (alongside `self.http`/`self.qb`), then add this method after `reconcile`:

```python
    def run_notify(self, csv_path, session_id, *, dry_run: bool = False):
        """Run the real daily email-notification flow with SMTP faked.

        Patches the notify module's send_email to a FakeSMTP (captured on
        ``self.smtp``) and returns the EmailNotificationResult. Git side-effects
        are already neutered by the autouse _disable_git_side_effects fixture."""
        import javdb.integrations.notify.email.service as notify_service
        from javdb.integrations.notify.email.options import EmailNotificationOptions
        from javdb.integrations.notify.email.service import run_email_notification
        from tests.harness.fake_smtp import FakeSMTP

        self.smtp = FakeSMTP()
        self._mp.setattr(
            notify_service, "send_email",
            lambda subject, body, attachments=None, dry_run=False, **kwargs:
                self.smtp.send_email(subject, body, attachments, dry_run),
        )
        return run_email_notification(EmailNotificationOptions(
            csv_path=csv_path, mode="daily", dry_run=dry_run,
            from_pipeline=True, session_id=session_id,
        ))
```

- [ ] **Step 5: Write the notify capstone test**

```python
# tests/harness/test_scenario_notify.py
"""ADR-037 Phase 2: the daily pipeline's email is produced through a faked SMTP."""

from tests.harness.scenarios.golden_daily import golden_daily


def test_daily_notify_email_is_captured(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    notify_result = pipeline_harness.run_notify(
        result.spider_result.csv_path, result.spider_result.session_id,
    )

    # Exactly one email went through the faked SMTP seam, with a real subject.
    assert pipeline_harness.smtp is not None
    assert len(pipeline_harness.smtp.sent) == 1
    assert pipeline_harness.smtp.sent[0].subject
    assert notify_result.email_sent is True
```

- [ ] **Step 6: Run + iterate to green**

Run: `pytest tests/harness/test_fake_smtp.py tests/harness/test_scenario_notify.py -v`
Expected (final): PASS. The notify capstone has integration surface — `run_email_notification` reads the CSV, queries DB stats, and analyses logs. If it sends 0 emails, inspect `notify_result.subject`/`.exit_code` and confirm `csv_path` exists (`result.spider_result.csv_path`); if it raises on a missing optional input, pass only the documented `EmailNotificationOptions` fields shown above (do not add `verify_jsonl`/`health_snapshot`). Treat green here as the integration goal, like the IMP-01 golden capstone.

- [ ] **Step 7: Commit**

```bash
git add tests/harness/fake_smtp.py tests/harness/pipeline_harness.py tests/harness/test_fake_smtp.py tests/harness/test_scenario_notify.py
git commit -m "test(harness): FakeSMTP + run_notify daily-email scenario (ADR-037 Phase 2)"
```

---

## Task 10: pikpak / rclone neuter building blocks

**Files:**
- Create: `tests/harness/fake_external.py`
- Test: `tests/harness/test_fake_external.py`

PikPak and rclone run as **subprocess steps outside** the harness's spider→uploader→commit core, so this IMP does not build full end-to-end pikpak/rclone scenarios (YAGNI — see Self-Review). It ships the neuter building blocks the ADR-037 D5 list calls for: `assert_pikpak_neutered()` documents/verifies the existing global mock, and `neuter_rclone(monkeypatch)` patches the rclone shell-out seam so a future in-process scenario can call the rclone manager without an `rclone` binary.

- [ ] **Step 1: Write the smoke test**

```python
# tests/harness/test_fake_external.py
def test_pikpak_is_globally_neutered():
    from tests.harness.fake_external import assert_pikpak_neutered
    assert_pikpak_neutered()  # raises if conftest's pikpakapi MagicMock is gone


def test_neuter_rclone_patches_install_probe(monkeypatch):
    import javdb.integrations.rclone.manager.service as rclone_service
    from tests.harness.fake_external import neuter_rclone
    neuter_rclone(monkeypatch)
    # The install probe now reports present without an rclone binary on PATH.
    # check_rclone_installed() -> Tuple[bool, str]; callers unpack the tuple.
    ok, _msg = rclone_service.check_rclone_installed()
    assert ok is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/harness/test_fake_external.py -v`
Expected: FAIL — `ModuleNotFoundError: tests.harness.fake_external`

- [ ] **Step 3: Write `fake_external.py`**

```python
# tests/harness/fake_external.py
"""Neuter building blocks for the non-core subprocess seams (ADR-037 D5, Phase 2).

PikPak and rclone are not on the daily spider->uploader->commit path the harness
drives, so these are deliberately minimal: PikPak is already globally mocked at
conftest import (the ``pikpakapi`` module is a MagicMock), and ``neuter_rclone``
stubs the rclone shell-out probe so a future in-process scenario can exercise the
rclone manager without an ``rclone`` binary."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def assert_pikpak_neutered() -> None:
    """Confirm conftest's global pikpakapi MagicMock is in place."""
    mod = sys.modules.get("pikpakapi")
    assert isinstance(mod, MagicMock), (
        "pikpakapi is not globally mocked — tests/conftest.py installs a MagicMock "
        "at import time; a real pikpakapi here would dial the network."
    )


def neuter_rclone(monkeypatch) -> None:
    """Stub the rclone install probe so the manager runs without a binary."""
    import javdb.integrations.rclone.manager.service as rclone_service
    # check_rclone_installed() -> Tuple[bool, str] (javdb/integrations/rclone/
    # helper.py:394); callers unpack ``ok, msg = ...`` (manager/service.py:1223,
    # :1415). The stub MUST return a 2-tuple, not a bare bool, or the manager
    # raises "cannot unpack non-iterable bool object".
    monkeypatch.setattr(rclone_service, "check_rclone_installed",
                        lambda: (True, "stubbed: harness neuter"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/harness/test_fake_external.py -v`
Expected: PASS (2 passed). If `check_rclone_installed` is not a module-level name on `rclone_service`, confirm its import site (`grep -n "check_rclone_installed" javdb/integrations/rclone/manager/service.py`) and patch the name as imported there.

- [ ] **Step 5: Commit**

```bash
git add tests/harness/fake_external.py tests/harness/test_fake_external.py
git commit -m "test(harness): pikpak/rclone neuter building blocks (ADR-037 Phase 2)"
```

---

## Task 11: Re-exports, CONTEXT.md, docs, full gate

**Files:**
- Modify: `tests/harness/__init__.py`, `CONTEXT.md`, `docs/handbook/en/developer/pipeline-test-harness.md`, `docs/handbook/zh/developer/pipeline-test-harness.md`

- [ ] **Step 1: Re-export the new public surface**

Append to `tests/harness/__init__.py`:

```python
from tests.harness.cassette import load_cassette, save_cassette  # noqa: E402,F401
from tests.harness.fixture_http import record_enabled  # noqa: E402,F401
from tests.harness.fake_smtp import FakeSMTP, SentEmail  # noqa: E402,F401
```

- [ ] **Step 2: Update CONTEXT.md** — under the ADR-037 terms added in IMP-01, append the Phase-2 vocabulary:
  - **Record mode** — the dev-only, `JAVDB_HARNESS_RECORD`-gated `FixtureHTTP` path that fetches a cassette miss live and remembers it for `save_cassette`.
  - **Closed-loop scenario** — a harness run whose queued torrents are `complete()`d and reconciled to `completed` via the real ADR-033 reconciler.
  - **Drift scenario** — a harness run that seeds a critical `ParseRunFieldFill` so the ADR-035 commit gate refuses the commit.

- [ ] **Step 3: Document record mode + the scenario library** in `docs/handbook/en/developer/pipeline-test-harness.md` (extend the IMP-01 page): a "Recording cassettes" section (the `JAVDB_HARNESS_RECORD` + `record_pages` + `save_cassette` recipe) and a "Scenario library" section (completion / drift / failure-rollback / notify — one short example each, e.g. `harness.reconcile()` after `complete()`). Mirror every change into the paired `docs/handbook/zh/developer/pipeline-test-harness.md` in the same commit (translation drift is a defect); keep code/paths/env-var names verbatim, translate prose + code comments.

- [ ] **Step 4: Full gate**

Run:
```bash
pytest tests/harness/ -v
```
Expected: all PASS (cassette, record mode, FakeQB incl. `categories`, completion, drift, failure-rollback, FakeSMTP, notify, fake_external, and the IMP-01 golden + seam + view-routing tests). The `record_pages` live smoke is `SKIPPED`.

- [ ] **Step 5: Commit**

```bash
git add tests/harness/__init__.py CONTEXT.md docs/handbook
git commit -m "test(harness): re-exports + CONTEXT/docs for ADR-037 Phase 2"
```

- [ ] **Step 6: Update the ADR roadmap + status log**

In `ADR-037-deterministic-pipeline-test-harness.md` (and its `.zh.md` in the same commit): change the Phase-2 roadmap row from "IMP-ADR037-02 (stub)" to a link to this file, and append a Status Log line dated to the implementation day summarising what shipped (record mode; completion/drift/failure scenarios; FakeSMTP + run_notify; pikpak/rclone neuter blocks) and any reconciliation notes if reality diverged from this plan.

```bash
git add docs/design/ADR-037-Pipeline-Test-Harness/ADR-037-deterministic-pipeline-test-harness.md docs/design/ADR-037-Pipeline-Test-Harness/ADR-037-deterministic-pipeline-test-harness.zh.md
git commit -m "docs(adr-037): mark Phase 2 implemented; link IMP-ADR037-02"
```

---

## Plan Self-Review

**Spec coverage (ADR-037 Phase-2 roadmap row):**
- Record mode (D3) → Tasks 1–3 (`record_miss` wired to `live_fetch`; env gate; cassette save/load; dev `record_pages`). ✓
- Scenario library — drift / completion / failure → Tasks 5, 7, 8 (completion via real reconciler; drift via real commit gate; failure via real `db_rollback_session`). ✓
- SMTP / pikpak / rclone fakes → Tasks 9 (FakeSMTP, full), 10 (pikpak/rclone neuter blocks). ✓
- Docs (CONTEXT.md + handbook) + ADR roadmap update → Task 11. ✓

**Probe-verified (throwaway test, since removed):** against the live tree post-[BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md), with **no** harness repoint: `run_daily(golden_daily())` → `acquisition_outcomes()` = 2 `queued`; `complete()` + the real reconciler → `marked_completed == 2` and 2 `completed`; seeding `FieldFill("index","video_code",0.10,50)` before commit → `commit_session` raised `SiteContractDriftError`, `history().count() == 0`, one `site_drift` `OpsIncidents` row; clean run `events()` = `["RunStarted"]`; MovieHistory codes `ABC-001`/`ABC-002`. The original plan-time probe found `acquisition_outcomes()` was `[]` without a repoint; **BFR-016's call-time path resolution made that repoint unnecessary** (re-probed 2026-06-07), so Task 4 Step 5 is now a documented no-op.

**Honest scoping (stated, not skipped silently):**
- **pikpak/rclone are minimal by design.** They are subprocess steps off the harness's spider→uploader→commit core; building full in-process pikpak/rclone *scenarios* (qB delete + PikPakApi upload + git, or `rclone lsjson` orchestration) adds large surface for little marginal coverage of the *pipeline*. Task 10 delivers the neuter **building blocks** the ADR D5 list names; full scenarios remain available to a future IMP if a concrete need appears (YAGNI).
- **Events under-asserted on purpose.** `SessionCommitted`/`SessionFailed` are emitted only by the CLI commit (`apps/cli/db/commit_session.py`), not the API `commit_session` the harness drives, so no scenario asserts them. Driving the CLI commit to test those emissions is a separate, larger change and a non-goal here. `RunStarted` (emitted in `run_spider`) is genuinely exercised by every run.
- **Drift fidelity.** The drift scenario seeds the fill directly (deterministic) rather than authoring drift-shaped HTML (fragile: it would need ≥`min_sample` parsed movies and a parser that keeps partial entries). This tests the load-bearing behaviour — the **commit gate** — which is exactly the pipeline seam ADR-037 exists to cover.

**Type consistency:** `FixtureHTTP(pages, *, record_miss=False, live_fetch=None)` + `.recorded`; `record_enabled()`; `save_cassette`/`load_cassette`; `FakeQB.categories()`; `FakeSMTP(*, succeed=True)`/`SentEmail`; `PipelineHarness.run_daily(scenario, *, before_commit=None)`, `.reconcile(*, qb_client=None, categories=None, infer_absent=False)`, `.run_notify(csv_path, session_id, *, dry_run=False)`, `.smtp`; `HarnessResult(..., commit_error=None)` — all used identically across Tasks 1–11.

**No production changes:** every code edit lands under `tests/harness/**` (plus docs/CONTEXT). The harness stays test-support only (ADR-037 D2).
