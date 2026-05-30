# Deterministic Pipeline Test Harness

The pipeline harness (`tests/harness/`) runs the full daily pipeline —
**spider → qB uploader → session commit** — entirely in-process against fakes,
so the whole flow can be verified in CI with **zero network and zero live
services**. See [ADR-037](../../../design/ADR-037-Pipeline-Test-Harness/ADR-037-deterministic-pipeline-test-harness.md)
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
`finally` block before returning). Real-time throttles (the spider's per-movie /
phase-transition cooldowns and the uploader's inter-add delay) are neutered, so
the whole run finishes in well under a second.

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

## Scope (Phase 1)

In-process domain logic only. The subprocess orchestration (`step_runner`
process management, CLI arg parsing) is **not** exercised here — it is covered
by separate light smoke tests. Record mode, the scenario library
(drift / completion / failure), and the remaining seams are Phase 2.
