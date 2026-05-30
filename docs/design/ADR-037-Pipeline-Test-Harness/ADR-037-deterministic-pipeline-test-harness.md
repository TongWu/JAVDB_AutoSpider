# ADR-037: Deterministic Pipeline Test Harness

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; execution delegated to per-phase IMPs            |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions
> (Direction 5 — a deterministic simulation test-bed).

## Context

The pipeline can only be exercised end to end against **live external services**:
real javdb.com (HTTP via `javdb/infra/request.py` `requests`/`curl_cffi`), a real
qBittorrent (`javdb/integrations/qb/client.py` over `requests`), and a real DB.
The 2026-05-29 architecture review flagged that commit "is only testable against a
real DB". Today's tests paper over this **ad hoc**:

- The DB seam is **already largely solved** — `tests/conftest.py`'s
  `_isolate_sqlite(tmp_path)` (autouse) points all three logical DBs at one temp
  SQLite and runs `init_db` to build the real schema.
- But **HTTP is hand-mocked per test** (e.g. `responses = iter(...)` +
  `monkeypatch` in `test_spider_backends.py`); there is **no shared record/replay**.
- There is **no reusable fake qB**; qB is mocked per test.

The cost compounds now: the three Phase-1 designs from this same session
([ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) closed-loop,
[ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
sentinel, [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
event spine) all depend on **pipeline behaviour** — qB state transitions, the
commit gate, emitted events — that no current test can drive end to end.

This ADR builds a **deterministic, in-process, end-to-end pipeline harness** that
runs spider → uploader → commit against fakes, so the whole pipeline (and the
three new features) can be verified in CI with zero network and zero live
services.

## Decision

Build `tests/harness/`: an in-process pipeline harness composing a **FixtureHTTP**
transport (replays javdb from cassettes), a **FakeQB** (in-memory, controllable
torrent states), and the existing seeded temp DB. A pytest fixture wires the fakes
in via monkeypatch, drives the service layer in-process, and exposes a
scenario + assertion surface.

### Design Decisions

**D1. In-process service composition — not subprocess.** The harness calls the
**service layer** directly (`run_spider` via the ADR-012 `InProcessSpiderStepRunner`,
the uploader service, `commit_session`) in one process, so fakes can be injected
by monkeypatch (which cannot cross a subprocess boundary). This tests the pipeline's
**domain logic** end to end; the subprocess orchestration (`step_runner` process
management, CLI arg parsing) is infrastructure covered separately by light smoke
tests, not by this harness.

**D2. A `tests/harness/` test-support package, composed by a pytest fixture.** Not
production code. A `pipeline_harness` fixture/context manager sets up all fakes,
reuses `_isolate_sqlite`, drives a run, and yields a control + assertion handle.

```
tests/harness/
  fixture_http.py     # FixtureHTTP transport (replay; optional record)
  fake_qb.py          # FakeQB (in-memory, controllable states)
  pipeline_harness.py # composition + in-process drive + control/assert surface
  scenarios/          # cassettes (HTML) + scenario definitions
```

**D3. `FixtureHTTP` replays javdb from cassettes; record mode is optional and
gated.** A cassette is a directory mapping request URL → response (status, headers,
body), replayed at the `request.py` transport seam. **Default fixtures are curated,
minimal index/detail HTML** (extending `tests/fixtures/parser/`). An **optional,
env-gated record mode** performs the real request on a cassette miss and saves it —
for refreshing cassettes when javdb changes. Curated-minimal is the default because
javdb is adult content and full pages are large/sensitive; recording is a dev-only
refresh tool, never run in CI. (This "golden page" capture is the same idea as the
[ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
sentinel's golden anchors.)

**D4. `FakeQB` is in-memory with controllable state.** It implements the
`QBittorrentClient` surface the code actually uses
(`add_torrent`, `get_torrents_multiple_categories`, `delete_torrents`,
`get_existing_hashes`) over an in-memory torrent dict, plus control methods
(`complete(hash)`, `stall(hash)`) so a scenario can simulate a download finishing —
exactly what ADR-033 / ADR-035 / ADR-036 need to assert against.

**D5. Reuse the seeded temp DB; neuter the remaining side-effecting seams.** The
DB uses the existing `_isolate_sqlite`. SMTP, the proxy coordinator, PikPak, and
rclone are mocked/neutered (PikPak is already globally MagicMock'd in conftest).
Phase 1 fakes the three load-bearing seams (HTTP, qB, DB); the rest are stubs.

**D6. A scenario + assertion API.** A test author declares a `PipelineScenario`
(javdb pages + FakeQB config) and asserts on outcomes through helpers:

```python
scenario = PipelineScenario(pages={index_url: INDEX_HTML, detail_url: DETAIL_HTML}, qb=FakeQBConfig())
result = harness.run_daily(scenario)        # spider -> uploader -> commit, in-process
assert harness.history().count() == 2
assert "TorrentQueued" in harness.events()  # when ADR-036 is built
```

**D7. Phased: prove it, then grow it.** Phase 1 ships the harness core + FixtureHTTP
(replay) + FakeQB + **one golden scenario** (a clean daily run) asserting the
authoritative outcomes. Phase 2 adds the record mode, a scenario library
(drift → gate, completion → closed-loop, failure → rollback), and the remaining
seams. Phase 3 (optional) layers golden-run record/replay diffing on top.

## Consequences

### Positive

- **The whole pipeline is testable in CI** — spider → uploader → commit runs
  deterministically with zero network/live services.
- **De-risks this session's three IMPs** — closed-loop, sentinel, and event-spine
  behaviour gets real end-to-end coverage.
- **One shared HTTP/qB fake** — replaces the per-test hand-mocking that exists today.
- **Refreshable** — the gated record mode keeps cassettes current as javdb evolves.
- **Builds on what exists** — reuses `_isolate_sqlite` and the ADR-012 in-process
  spider runner.

### Negative

- **In-process ≠ full production fidelity** — the subprocess orchestration
  (`step_runner`, CLI wiring) is not exercised by this harness (covered by separate
  smoke tests).
- **Fakes must track real contracts** — `FakeQB` and `FixtureHTTP` must stay aligned
  with qB's API and javdb's HTML as they change (the record mode and the ADR-035
  sentinel both help catch drift).
- **Curated fixtures can hide real-page quirks** — mitigated by the record mode for
  periodic refresh.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Harness core + golden scenario | [IMP-ADR037-01](IMP-ADR037-01-harness-core.md) | `tests/harness/` (FixtureHTTP replay, FakeQB, `pipeline_harness` fixture, scenario+assert API); one golden daily scenario (index → details → queued → commit) asserting history (+ events if ADR-036 built) | record mode; scenario library; SMTP/pikpak/rclone seams |
| Phase 2 — Scenario library + record + seams | IMP-ADR037-02 (stub) | record mode; drift/completion/failure scenarios; SMTP/pikpak/rclone fakes | — |
| Phase 3 — Golden-run diff (optional) | IMP-ADR037-03 (stub) | record a real run's inputs+outputs; replay + diff in CI | — |

Phase 1 stands alone and adds only test-support code. Phases 2/3 grow coverage.

### Explicit non-goals (YAGNI)

- **No subprocess-orchestration testing** — in-process per D1; subprocess wiring is
  separate light smoke.
- **No live javdb in CI** — the record mode is dev-only and env-gated.
- **Not a performance/load harness** — determinism and correctness only.
- **Not all seams in Phase 1** — HTTP + qB + DB first; SMTP/pikpak/rclone in Phase 2.

## Domain Language (additions for CONTEXT.md)

- **Pipeline harness** — the in-process `tests/harness/` rig that runs
  spider → uploader → commit against fakes.
- **Cassette** — a directory of recorded javdb request→response pairs replayed by
  `FixtureHTTP`.
- **FixtureHTTP / FakeQB** — the replay HTTP transport and in-memory qB fake.
- **Scenario** — a declared set of javdb pages + qB config driving one harness run.
- **Golden scenario** — the canonical clean-daily-run scenario asserted in CI.

## Alternatives Considered

- **Subprocess harness with env-injected fakes** — rejected (D1): highest fidelity
  but requires cross-process-reachable fakes (a real fake-qB HTTP server, env-based
  fixture HTTP), much more complexity for the orchestration layer that smoke tests
  already cover.
- **Record-everything (full real-page cassettes by default)** — rejected (D3):
  javdb is adult content, pages are large/sensitive; curated-minimal fixtures keep
  tests legible and safe, with record mode for refresh.
- **Golden-run record/replay only** — rejected as the primary form: a brittle
  regression net without a composable fake layer underneath; kept as optional
  Phase 3 on top of the harness.

## References

- [ADR-012 — Pipeline Run Structured Boundary](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)
- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

## Status Log

- 2026-05-29: Proposed (umbrella; three phases scoped, IMPs pending).
