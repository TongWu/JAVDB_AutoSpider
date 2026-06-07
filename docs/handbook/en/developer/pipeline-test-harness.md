# Deterministic Pipeline Test Harness

The pipeline harness (`tests/harness/`) runs the full daily pipeline —
**spider → qB uploader → session commit** — entirely in-process against fakes,
so the whole flow can be verified in CI with **zero network and zero live
services**. See [ADR-037](../../../design/_archive/ADR-037-Pipeline-Test-Harness/ADR-037-deterministic-pipeline-test-harness.md)
for the design rationale.

## What it composes

| Seam | Real code | Fake |
| --- | --- | --- |
| HTTP (javdb) | `RequestHandler.get_page` | `FixtureHTTP` — replays a cassette by URL |
| qB | `_wrap_session_as_client` (+ connection/login probes) | `FakeQB` — in-memory, controllable torrent state |
| DB | `get_db()` → SQLite | autouse `_isolate_sqlite` (one seeded temp DB) |

Side-effecting seams not under test (SMTP, proxy coordinator, PikPak, rclone,
git) are neutered: PikPak is globally mocked in `tests/conftest.py`, git side
effects are disabled there, and the harness forces `STORAGE_MODE=duo` plus a
`tmp_path` working directory so the spider's CSV + report artifacts never touch
the real `reports/` tree.

## Writing a scenario

A scenario declares the javdb pages (cassette) and the FakeQB config:

```python
from tests.harness.pipeline_harness import PipelineScenario, FakeQBConfig

scenario = PipelineScenario(
    pages={
        "https://javdb.com?page=1": INDEX_HTML,   # what get_page_url(1) computes
        "https://javdb.com/v/AAA111": DETAIL_HTML_1,
        "https://javdb.com/v/BBB222": DETAIL_HTML_2,
    },
    qb=FakeQBConfig(),
)
```

Cassette keys must be exactly what the spider requests: the index URL is the
value `get_page_url(1)` computes (`https://javdb.com?page=1`); detail URLs are
`urljoin("https://javdb.com", href)`. Author minimal HTML that carries the
selectors the parser reads (`div.movie-list` → `div.item` → `a.box`;
`div#magnets-content` → `a[href^=magnet]`); the proven fixtures in
`tests/conftest.py` (`sample_index_html` / `sample_detail_html`) are the
reference. Magnets should be 40-hex so `FakeQB` computes a stable hash.

## Running it

The `pipeline_harness` fixture (registered in `tests/harness/conftest.py`)
yields a `PipelineHarness`. Call `run_daily(scenario)` and assert on outcomes:

```python
from tests.harness.scenarios.golden_daily import golden_daily

def test_golden_daily_run_writes_two_movies(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    assert all("page=" not in m for m in result.http.misses)  # index hit the cassette
    assert pipeline_harness.history().count() == 2            # 2 movies committed
    assert len(result.qb.all_hashes()) == 2                   # 2 torrents queued
```

`run_daily` drives the real three-step flow: `run_spider(options)` →
`run_uploader(QbUploaderOptions(...))` → `commit_session(CommitRequest(...))`.
The session id and CSV path are taken from the spider's returned
`SpiderRunResult` (because `run_spider` clears the active-session context in its
`finally` block before returning). The commit is **gated on spider AND uploader
success** — mirroring `DailyIngestion.yml`'s "Mark sessions as committed" step
(`if: ${{ success() }}`). When the uploader fails (e.g.
`FakeQBConfig(fail_adds=True)` makes every add return `False`, yielding a nonzero
`QbUploaderResult.exit_code`), `run_daily` leaves the session uncommitted —
production would hand it to the cleanup-on-failure rollback — so
`result.commit_result is None` and the pending rows never reach `MovieHistory`.
Real-time throttles (the spider's per-movie / phase-transition cooldowns and the
uploader's inter-add delay) are neutered, so the whole run finishes in well under
a second.

### Assertion surface

| Helper | Returns |
| --- | --- |
| `result.http.misses` / `result.http.requests` | URLs the spider requested (and which missed the cassette) |
| `result.qb.all_hashes()` | torrent hashes queued into FakeQB |
| `pipeline_harness.history().count()` | rows in `MovieHistory` after commit |
| `pipeline_harness.events()` | `PipelineEvent` rows — `[]` until ADR-036 lands |
| `pipeline_harness.acquisition_outcomes()` | `AcquisitionOutcome` rows — `[]` until ADR-033 lands |

The `events()` / `acquisition_outcomes()` helpers degrade to `[]` when those
tables do not yet exist, so the harness lands cleanly before those features and
gains assertions for free once they do.

## Recording cassettes

> **Dev-only. Never run in CI.**

When javdb's HTML changes, refresh a cassette by fetching the real pages with
`record_pages` and writing the result to disk with `save_cassette`. Three
conditions must all be true before a live fetch happens: (1) the scenario is
constructed with `record_miss=True`, (2) a `live_fetch` callback is supplied,
AND (3) the `JAVDB_HARNESS_RECORD` env var is truthy. In normal test runs —
including all CI runs — none of these conditions are met, so `FixtureHTTP`
never dials the network.

Typical dev shell recipe:

```bash
# Arm record mode, then fetch and persist
JAVDB_HARNESS_RECORD=1 python3 -c "
from tests.harness.recording import record_pages
from tests.harness.cassette import save_cassette

urls = [
    'https://javdb.com?page=1',
    'https://javdb.com/v/<code>',
]
# record_pages raises RuntimeError if JAVDB_HARNESS_RECORD is not set
pages = record_pages(urls, use_proxy=True, use_cookie=True)
save_cassette('tests/harness/scenarios/cassettes/daily', pages)
"
```

`record_pages` uses the real `RequestHandler` (full proxy / CF-bypass / retry
machinery), so the recorded bodies match what production scrapes. `save_cassette`
writes a `manifest.json` + one `NNNN.html` per page; `load_cassette` reads it
back into a `{url: body}` dict for use as `PipelineScenario(pages=...)`.

Both helpers are re-exported from `tests.harness` for convenience:

```python
from tests.harness import load_cassette, save_cassette, record_enabled
```

## Scenario library

The harness ships four scenario patterns beyond the golden run. Each exercises
a distinct gate or seam in the pipeline.

### Completion (closed-loop)

Verifies the full ADR-033 acquisition loop: spider queues torrents → `complete()`
advances them in `FakeQB` → the real reconciler promotes them to `completed`.

```python
def test_completion_closes_the_loop(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    # Simulate both downloads finishing in qB.
    for qb_hash in result.qb.all_hashes():
        result.qb.complete(qb_hash)  # sets progress=1.0, state=uploading

    # The real ADR-033 reconciler closes the loop.
    rec = pipeline_harness.reconcile()
    assert rec.marked_completed == 2
    assert rec.errors == []

    after = {o["qb_hash"]: o["state"] for o in pipeline_harness.acquisition_outcomes()}
    assert set(after.values()) == {"completed"}
```

### Drift (commit gate)

Seeds a critical `ParseRunFieldFill` via the `before_commit` hook so the
ADR-035 sentinel refuses the commit. The session is left uncommitted and
`MovieHistory` stays empty.

```python
from javdb.ops.sentinel.models import FieldFill
from javdb.ops.sentinel.service import persist_run
from javdb.storage.sessions.commit import SiteContractDriftError

def _seed_critical_drift(session_id: str) -> None:
    # index.video_code is critical with min_fill=0.99; inject 0.10 over 50 samples
    persist_run([FieldFill("index", "video_code", 0.10, 50)], session_id=session_id)

def test_drift_gate_refuses_commit(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily(), before_commit=_seed_critical_drift)

    assert isinstance(result.commit_error, SiteContractDriftError)
    assert result.commit_result is None
    assert pipeline_harness.history().count() == 0
```

### Failure rollback

Makes every qB add fail via `FakeQBConfig(fail_adds=True)`. The uploader exits
non-zero, `run_daily` skips the commit, and the production rollback path is
driven explicitly to verify pending rows are cleaned up.

```python
from javdb.storage.db import db_rollback_session
from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario

def test_failure_rolls_back_pending(pipeline_harness):
    scenario = PipelineScenario(pages=golden_daily().pages, qb=FakeQBConfig(fail_adds=True))
    result = pipeline_harness.run_daily(scenario)

    assert result.uploader_result.exit_code != 0
    assert result.commit_result is None

    session_id = result.spider_result.session_id
    db_rollback_session(session_id, dry_run=False)

    assert pipeline_harness.history().count() == 0
```

### Notify (FakeSMTP)

Runs the daily notify step through a faked SMTP seam so the email is
captured in memory rather than sent.

```python
from tests.harness import FakeSMTP, SentEmail

def test_daily_notify_email_is_captured(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    notify_result = pipeline_harness.run_notify(
        result.spider_result.csv_path,
        result.spider_result.session_id,
    )

    # harness.smtp is populated after run_notify is called.
    assert pipeline_harness.smtp is not None
    assert len(pipeline_harness.smtp.sent) == 1
    assert pipeline_harness.smtp.sent[0].subject
    assert notify_result.email_sent is True
```

`FakeSMTP` and `SentEmail` are re-exported from `tests.harness`.

## Golden-run diff

The committed snapshot at `tests/harness/scenarios/golden_runs/daily/snapshot.json`
captures the authoritative, deterministic outputs of the canonical daily scenario.
The diff test (`tests/harness/test_golden_run.py::test_golden_daily_run_matches_snapshot`)
runs the golden scenario live and fails if any output has drifted from the committed
snapshot.

### What the snapshot captures

| Key | Contents |
| --- | --- |
| `movies` | `{video_code, href}` for every row in `MovieHistory` (ordered by `href`) |
| `torrents` | `MagnetUri` strings from `TorrentHistory` (sorted) |
| `qb_hashes` | Torrent hashes queued into `FakeQB` (sorted) |
| `acquisition` | `{qb_hash, state}` from `AcquisitionOutcome` rows (sorted by `qb_hash`) |
| `events` | `PipelineEvent` names in sequence order |

### What it excludes

The snapshot deliberately omits every nondeterministic field so diffs are
meaningful rather than noisy:

- **Session id** — regenerated on every run
- **All timestamps** (`CreatedAt`, `UpdatedAt`, `DateTime`, etc.)
- **Autoincrement ids** (`rowid`, `Id`, etc.)
- **Event sequence numbers** (`seq`)

Only identity-stable, deterministic fields are included.

### Guarding against drift

The diff test is registered in the regular test suite and runs on every CI
push. It:

1. Runs the golden daily scenario through `PipelineHarness.run_daily`.
2. Calls `capture_snapshot` to project the live outputs into the normalized
   snapshot dict.
3. Calls `diff_snapshots(expected, actual)` where `expected` is loaded from
   `tests/harness/scenarios/golden_runs/daily/snapshot.json`.
4. Fails with a readable diff if any key differs.

### Intentionally regenerating the snapshot (bless)

When a behaviour change is **intentional** — a new field, a changed sort order,
a new event — regenerate the committed snapshot with the bless command:

```bash
JAVDB_HARNESS_BLESS=1 pytest tests/harness/test_golden_run.py -k golden_daily_run_matches
```

`JAVDB_HARNESS_BLESS=1` makes the test write the live snapshot back to disk and
then skip (so the run is green). Stage and commit the updated
`snapshot.json` alongside the code change.

> **Review the snapshot diff before re-blessing — a changed snapshot is a
> behaviour change, not a chore.**

The golden-run helpers are re-exported from `tests.harness` for convenience:

```python
from tests.harness import capture_snapshot, diff_snapshots
from tests.harness import golden_path, load_golden, save_golden
```

## Scope

In-process domain logic only. The subprocess orchestration (`step_runner`
process management, CLI arg parsing) is **not** exercised here — it is covered
by separate light smoke tests.
