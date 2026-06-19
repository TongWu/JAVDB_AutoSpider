# IMP-ADR024-10: ADR-024 Phase 2 prerequisite — Remote `quality_probe` endpoint + Top-K runner-up collection

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Completed — implemented & verified 2026-06-19 (see Completion note). Refined from the ADR-024 Phase 2 outline (IMP-ADR024-08) during the 2026-06-19 scope grill.

## Completion note (2026-06-19)

Implemented across 11 commits on `claude/musing-benz-1718ba`, task-by-task via
`superpowers:subagent-driven-development` (fresh implementer per task + review).
All 9 tasks landed; 40 new unit tests pass; broad regression over the touched
areas (qb / quality / probe / spider-detail / magnet / pipeline / config) is
**1016 passed, 0 failures** (only the 2 pre-existing proxy test files error on
collection because the Rust extension is not built in this worktree).

Two corrections surfaced during execution and were folded back into this plan:

1. **Runner-up extraction kept production untouched.** The draft proposed
   refactoring `_python_categorize` to consume a shared `_bucket_magnets`. To
   guarantee byte-identical production selection (and because the draft's hacked
   / 4K predicates were wrong), the implementer left `_python_categorize`
   unchanged and added `_bucket_magnets` as a *parallel* pure-Python pass that
   mirrors its real predicates. A drift-guard test
   (`test_bucket_selection_matches_*`) pins `_bucket_magnets[cat][0]` to
   `_python_categorize`'s selection so the two cannot diverge silently.
2. **Probe must add ACTIVE, not paused.** The draft's `paused=True` +
   `stopCondition=MetadataReceived` is contradictory — a paused torrent never
   fetches metadata, so the stop condition never fires. Corrected to
   `paused=False` (active) so qB fetches metadata then auto-stops; pinned by a
   test asserting `paused is False` (commit `7f1971d1`).

Also added (not in the original draft) an additive `QBittorrentClient.get_torrent_files`
(wrapping `javdb/integrations/qb/readonly.get_torrent_files`) so the probe client
can fetch its own file lists — the production client had no such method.

**Outstanding operator action (not code):** apply the Task 2 D1 migration to
remote `javdb-reports`, then re-align the SQLite mirror
(`python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`).
Everything ships gated OFF (`TORRENT_QUALITY_EVIDENCE_ENABLED` /
`QUALITY_PROBE_ENABLED` default False, no probe endpoint configured), so a fresh
deploy captures nothing and probes nothing until the operator opts in.

**Goal:** Build the deferred remote `quality_probe` endpoint and bounded Top-K runner-up collection so the quality ranker can — in IMP-08 (assist) — recommend a *better alternative candidate* per category. This IMP only **collects** runner-up file-list evidence into the existing `TorrentQualityEvidence` table (`target_role='quality_probe'`); it does **not** score across candidates, change the API, or touch the production download decision.

**Architecture:** Two decoupled halves joined by a D1-first queue:
1. **Capture (during ingestion):** at magnet-categorisation time the spider already picks the best magnet per category and discards the rest. A new *additive*, pure-Python pass (`collect_runner_ups`) re-derives the same per-category buckets and returns the discarded runner-ups. The pipeline enqueues a bounded Top-K of them (per-category K + global cap) into a new `TorrentProbeCandidate` queue table. **The production `categorize` result is byte-identical — runner-up capture never alters selection.**
2. **Probe (separate step, remote qB):** a new probe runner dequeues `pending` candidates, adds each magnet to a *dedicated remote* qBittorrent under category `JavDB Quality Shadow` with `stopCondition=MetadataReceived` (capability-detected, fail-closed), polls until metadata arrives or a bounded timeout, extracts file features (reusing `javdb/quality/features.py`), UPSERTs evidence with `target_role='quality_probe'`, then removes the torrent with `deleteFiles=false`. Everything is gated off by default.

**Tech Stack:** Python 3.11, `requests`, argparse, pytest, SQL (D1 + SQLite mirror), GitHub Actions YAML, `config_generator`.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md) D4 (`quality_probe` role), D5 (dedicated shadow category, downstream ignores it), D6 (short-lived, `deleteFiles=false`), D7 (metadata-only capability detection, fail-closed), D8 (bounded Top-K). Refines [IMP-ADR024-08](IMP-ADR024-08-phase2-assist.md) "Task D" and its three prerequisites.

**Related:** [IMP-ADR024-02](IMP-ADR024-02-models-repo.md) (evidence models/repo) · [IMP-ADR024-03](IMP-ADR024-03-feature-extraction-scoring.md) (features) · [IMP-ADR024-05](IMP-ADR024-05-evidence-collection.md) (production-download collector — the sibling `production_download` role).

**Depends on:** Phase 1 (IMP-ADR024-01..07) — schema, models/repo, features all shipped.

**Blocks:** [IMP-ADR024-08](IMP-ADR024-08-phase2-assist.md) (assist scores production vs. these runner-up rows).

---

## Design decisions locked during the grill

- **Runner-up capture is additive and Rust-safe.** Production selection keeps its Rust-first `categorize` path untouched. Runner-up extraction is a separate pure-Python pass sharing the *same* bucketing rules via a refactored `_bucket_magnets` helper, so runner-ups are categorised identically to production. If production selected via Rust, runner-ups are still best-effort Python-categorised — acceptable because they are shadow-only.
- **A persistent queue is required.** Runner-up magnets are not stored anywhere today (CSV/`AcquisitionOutcome` keep only the selected torrent). `TorrentProbeCandidate` is the minimal D1-first store that decouples capture (ingestion) from probe (a later remote-qB step).
- **`info_hash` is known before adding.** A magnet URI carries its btih; the probe derives `info_hash` from the magnet so it can track and delete the exact torrent without guessing.
- **Capability detection is version-based + fail-closed.** `stopCondition=MetadataReceived` requires qB Web API ≥ 2.8.3 (qB ≥ 4.4.0). The probe checks `/api/v2/app/webapiVersion`; if below threshold, unreachable, or login fails, it records `probe_capability_unsupported` and exits without touching production.
- **Everything is off by default.** Capture is gated by `TORRENT_QUALITY_EVIDENCE_ENABLED`; probing adds a second gate `QUALITY_PROBE_ENABLED` plus a configured remote endpoint. A fresh deploy captures nothing and probes nothing.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `javdb/parsing/magnet_categorize.py` | Extract a shared `_bucket_magnets` helper; add pure-Python `collect_runner_ups(magnets, index, k)`. Production `categorize` output unchanged. |
| Create | `javdb/migrations/d1/2026_06_19_add_torrent_probe_candidate.sql` | D1-first `TorrentProbeCandidate` queue table. |
| Create | `javdb/storage/repos/torrent_probe_repo.py` | `TorrentProbeRepo`: enqueue / list pending / mark status (conn-injected). |
| Create | `javdb/quality/probe_queue.py` | `enqueue_runner_ups(...)` (bounded Top-K → queue rows) + `maybe_capture_runner_ups(...)` (gated wrapper) — pure, injectable. |
| Modify | `javdb/spider/detail/runner.py` | At the per-parsed-result persistence point, when capture enabled, read `result['movie_detail'].get_magnets_as_legacy()` + href/video_code and enqueue runner-ups (gated, additive, single-threaded, never alters selection). |
| Modify | `javdb/integrations/qb/client.py` | Add `stop_condition` kwarg to `add_torrent`; add `get_webapi_version()` + `supports_metadata_only_probe()`. |
| Create | `javdb/quality/probe_client.py` | `build_probe_client()` factory for the remote `quality_probe` qB role from config. |
| Create | `javdb/quality/probe_runner.py` | Probe lifecycle: dequeue → capability check → add (metadata-only) → poll → features → UPSERT evidence `quality_probe` → delete `deleteFiles=false`. Fail-closed. |
| Create | `apps/cli/qb/quality_probe.py` | CLI entrypoint, double-gated, exit code. |
| Modify | `javdb/infra/config_generator.py` | Add `QUALITY_PROBE_*` + capture-bound config keys. |
| Modify | `config.py.example` | Document the new config block. |
| Modify | `.github/workflows/QBFileFilter.yml` | Add a probe step after evidence collection; GitHub Variable gate (defense in depth). |
| Create | `tests/unit/test_magnet_runner_ups.py` | Runner-up extraction + production-parity tests. |
| Create | `tests/unit/test_torrent_probe_repo.py` | Queue repo tests. |
| Create | `tests/unit/test_probe_queue.py` | Bounded Top-K enqueue + gated `maybe_capture_runner_ups` tests. |
| Create | `tests/unit/test_qb_client_probe.py` | `stop_condition` + capability-detection tests. |
| Create | `tests/unit/test_probe_runner.py` | Probe lifecycle tests (add/poll/delete/fail-closed) with fakes. |
| Create | `tests/unit/test_runner_up_capture.py` | Spider-seam capture adapter tests (reads `movie_detail`, gated). |
| Create | `tests/unit/test_quality_probe_cli.py` | CLI double-gate + exit-code tests. |

## Scope Boundaries

- **No scoring across candidates.** This IMP writes `quality_probe` evidence rows only. `shadow_rank` / `would_replace_current_choice` ranking is IMP-08.
- **No API/Web change.** Reads of these rows are IMP-08.
- **No production-path change.** Production selection, the production qB, and the production download decision are byte-identical. Runner-up capture is additive; probing runs on a *separate* remote qB.
- **Probe qB is mutated only transiently.** Add → metadata → delete (`deleteFiles=false`). No file priority changes; no production category ever touched.
- **Off by default.** Capture: `TORRENT_QUALITY_EVIDENCE_ENABLED=False`. Probe: `QUALITY_PROBE_ENABLED=False` and no `QUALITY_PROBE_QB_URL`.
- **Fail-closed.** Missing endpoint, login failure, unsupported capability, or timeout records an evidence status and continues; it never raises into ingestion.

---

## Task 1 — Runner-up extraction (pure Python, production-safe)

**Files:**
- Modify: `javdb/parsing/magnet_categorize.py`
- Test: `tests/unit/test_magnet_runner_ups.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_magnet_runner_ups.py`:

```python
"""ADR-024 IMP-10: Top-K runner-up extraction must not change production selection."""

from __future__ import annotations

from javdb.parsing.magnet_categorize import _python_categorize, collect_runner_ups


def _m(name, tags, size, ts, href):
    return {"name": name, "tags": tags, "size": size, "timestamp": ts,
            "href": href, "file_count": 1}


def _subtitle_set():
    # Two subtitle candidates; the newer/larger one is the production pick.
    return [
        _m("ABC-123-C big", ["中文字幕"], "8GB", "2026-06-10", "magnet:?xt=urn:btih:AAA"),
        _m("ABC-123-C small", ["中文字幕"], "3GB", "2026-06-09", "magnet:?xt=urn:btih:BBB"),
        _m("ABC-123-C old", ["中文字幕"], "5GB", "2026-06-01", "magnet:?xt=urn:btih:CCC"),
    ]


def test_production_selection_unchanged_by_runner_up_pass():
    magnets = _subtitle_set()
    before = _python_categorize([dict(m) for m in magnets])
    collect_runner_ups([dict(m) for m in magnets], k=2)  # must not mutate / matter
    after = _python_categorize([dict(m) for m in magnets])
    assert before == after
    # the production subtitle pick is the newest (sort: timestamp desc, size desc)
    assert after["subtitle"] == "magnet:?xt=urn:btih:AAA"


def test_runner_ups_exclude_the_selected_best_per_category():
    runner_ups = collect_runner_ups(_subtitle_set(), k=2)
    subs = runner_ups["subtitle"]
    hrefs = [m["href"] for m in subs]
    assert "magnet:?xt=urn:btih:AAA" not in hrefs  # AAA is the production pick
    assert hrefs == ["magnet:?xt=urn:btih:BBB", "magnet:?xt=urn:btih:CCC"]  # ranked order


def test_runner_ups_bounded_by_k():
    runner_ups = collect_runner_ups(_subtitle_set(), k=1)
    assert [m["href"] for m in runner_ups["subtitle"]] == ["magnet:?xt=urn:btih:BBB"]


def test_no_runner_ups_when_single_candidate():
    one = [_m("solo", ["中文字幕"], "4GB", "2026-06-10", "magnet:?xt=urn:btih:ZZZ")]
    runner_ups = collect_runner_ups(one, k=2)
    assert runner_ups["subtitle"] == []


def test_categories_present_even_when_empty():
    runner_ups = collect_runner_ups([], k=2)
    assert set(runner_ups) == {
        "hacked_subtitle", "hacked_no_subtitle", "subtitle", "no_subtitle",
    }
    assert all(v == [] for v in runner_ups.values())
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_magnet_runner_ups.py -v
```

Expected: FAIL with `ImportError: cannot import name 'collect_runner_ups'`.

- [ ] **Step 3: Refactor a shared bucketer and add `collect_runner_ups`**

In `javdb/parsing/magnet_categorize.py`, factor the four bucketing rules out of
`_python_categorize` into a shared helper, then have both the production
categoriser and the new runner-up pass consume it. **The bucketing logic
(filters + sort) must be moved verbatim so production output is unchanged.**

Add, after `_sort_key` (around line 108):

```python
# ADR-024 IMP-10: the four production categories, in a stable order.
_QUALITY_CATEGORIES = (
    "hacked_subtitle",
    "hacked_no_subtitle",
    "subtitle",
    "no_subtitle",
)


def _bucket_magnets(magnets, index=None):
    """Return per-category candidate lists, each sorted best-first.

    This is the single source of truth for *which* magnets fall into each
    production category and *in what order*. ``_python_categorize`` consumes
    ``bucket[cat][0]`` (the production pick); ``collect_runner_ups`` consumes
    ``bucket[cat][1:]`` (the discarded runner-ups). Keep the filter predicates
    and the ``_sort_key`` ordering identical between the two consumers — that
    identity is what guarantees runner-ups are categorised exactly as
    production would categorise them.
    """
    subtitle = [
        m for m in magnets
        if any('字幕' in tag or 'Subtitle' in tag for tag in m['tags'])
        and '.无码破解' not in m['name']
    ]
    subtitle.sort(key=_sort_key, reverse=True)

    hacked_subtitle = []
    hacked_no_subtitle = []
    for m in magnets:
        if '.无码破解' not in m['name']:
            continue
        if any('字幕' in tag or 'Subtitle' in tag for tag in m['tags']):
            hacked_subtitle.append(m)
        else:
            hacked_no_subtitle.append(m)
    hacked_subtitle.sort(key=_sort_key, reverse=True)
    hacked_no_subtitle.sort(key=_sort_key, reverse=True)

    # no_subtitle: neither subtitled nor hacked. 4K candidates rank ahead of the
    # rest (production preference), each group still ordered by _sort_key.
    plain = [
        m for m in magnets
        if not any('字幕' in tag or 'Subtitle' in tag for tag in m['tags'])
        and '.无码破解' not in m['name']
    ]
    k4 = [m for m in plain if infer_resolution(m['name'], m.get('tags', [])) == 2160]
    normal = [m for m in plain if m not in k4]
    k4.sort(key=_sort_key, reverse=True)
    normal.sort(key=_sort_key, reverse=True)
    no_subtitle = k4 + normal

    return {
        "subtitle": subtitle,
        "hacked_subtitle": hacked_subtitle,
        "hacked_no_subtitle": hacked_no_subtitle,
        "no_subtitle": no_subtitle,
    }


def collect_runner_ups(magnets, index=None, k=2):
    """Return up to ``k`` runner-up magnets per production category.

    Additive and read-only: this never alters production selection. Runner-ups
    are ``_bucket_magnets(...)[cat][1:k+1]`` — every candidate except the one
    production would pick (``[0]``), capped at ``k``. Returns a dict keyed by all
    four categories (empty lists when there are no runner-ups).
    """
    buckets = _bucket_magnets(magnets, index)
    return {cat: buckets[cat][1:k + 1] for cat in _QUALITY_CATEGORIES}
```

Then **rewrite `_python_categorize` to delegate bucketing to `_bucket_magnets`**,
keeping its existing `result` dict shape and per-category `best = bucket[cat][0]`
assignments. Replace the inline filter/sort blocks (the `subtitle_magnets = [...]`,
`hacked_*`, `k4_magnets`/`normal_magnets` sections) with reads from
`buckets = _bucket_magnets(magnets, index)`; the `result[...] = best[...]`
assignments and debug logs stay as-is. Export the new name:

```python
__all__ = [
    'extract_magnets', 'infer_resolution', '_parse_size', '_sort_key',
    '_python_extract_magnets', 'RUST_MAGNET_AVAILABLE', 'collect_runner_ups',
]
```

Also add `collect_runner_ups` to the re-export list in
`javdb/spider/magnet_extractor.py` so existing callers can import it from the
canonical shim.

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_magnet_runner_ups.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Run the existing magnet/categorise tests to prove no production regression**

```bash
pytest tests/unit -k "magnet or categor" -q
```

Expected: PASS — the production `categorize` output is unchanged.

- [ ] **Step 6: Commit**

```bash
git add javdb/parsing/magnet_categorize.py javdb/spider/magnet_extractor.py tests/unit/test_magnet_runner_ups.py
git commit -m "feat(quality): extract Top-K magnet runner-ups without changing selection (ADR-024)"
```

---

## Task 2 — `TorrentProbeCandidate` queue table + repo (D1-first)

**Files:**
- Create: `javdb/migrations/d1/2026_06_19_add_torrent_probe_candidate.sql`
- Create: `javdb/storage/repos/torrent_probe_repo.py`
- Test: `tests/unit/test_torrent_probe_repo.py`

- [ ] **Step 1: Write the D1 migration**

Create `javdb/migrations/d1/2026_06_19_add_torrent_probe_candidate.sql`:

```sql
-- ADR-024 IMP-10: queue of runner-up magnet candidates awaiting a metadata-only
-- probe on the remote quality_probe qBittorrent endpoint. D1 is the source of
-- truth (the SQLite mirror is rebuilt from this DDL).
--
-- One row per (info_hash, movie_href): the same runner-up under the same movie
-- page is enqueued once; re-capture UPSERTs (idempotent).
CREATE TABLE IF NOT EXISTS TorrentProbeCandidate (
    info_hash        TEXT NOT NULL,
    movie_href       TEXT NOT NULL,
    video_code       TEXT,
    javdb_category   TEXT,
    magnet_uri       TEXT NOT NULL,
    magnet_name      TEXT,
    javdb_tags_json  TEXT,
    javdb_size_text  TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending | probed | failed
    enqueued_at      TEXT NOT NULL,
    probed_at        TEXT,
    PRIMARY KEY (info_hash, movie_href)
);

CREATE INDEX IF NOT EXISTS idx_torrent_probe_candidate_status
    ON TorrentProbeCandidate(status);
```

- [ ] **Step 2: Write the failing repo test**

Create `tests/unit/test_torrent_probe_repo.py`:

```python
"""ADR-024 IMP-10: probe-queue repository."""

from __future__ import annotations

import sqlite3

import pytest

from javdb.storage.repos.torrent_probe_repo import (
    ProbeCandidate,
    TorrentProbeRepo,
)

_DDL = """
CREATE TABLE TorrentProbeCandidate (
    info_hash TEXT NOT NULL, movie_href TEXT NOT NULL, video_code TEXT,
    javdb_category TEXT, magnet_uri TEXT NOT NULL, magnet_name TEXT,
    javdb_tags_json TEXT, javdb_size_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending', enqueued_at TEXT NOT NULL, probed_at TEXT,
    PRIMARY KEY (info_hash, movie_href)
);
"""


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return TorrentProbeRepo(conn)


def _cand(h="HASH1", href="/v/abc"):
    return ProbeCandidate(
        info_hash=h, movie_href=href, video_code="ABC-123",
        javdb_category="subtitle", magnet_uri=f"magnet:?xt=urn:btih:{h}",
        magnet_name="ABC-123-C", javdb_tags=["中文字幕"], javdb_size_text="8GB",
    )


def test_enqueue_then_list_pending(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].info_hash == "HASH1"
    assert pending[0].magnet_uri == "magnet:?xt=urn:btih:HASH1"


def test_enqueue_is_idempotent_upsert(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    repo.enqueue(_cand(), enqueued_at="2026-06-19T01:00:00Z")  # same PK
    assert len(repo.list_pending()) == 1


def test_mark_probed_removes_from_pending(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    repo.mark_status("HASH1", "/v/abc", "probed", probed_at="2026-06-19T02:00:00Z")
    assert repo.list_pending() == []


def test_mark_failed_also_leaves_pending_empty(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    repo.mark_status("HASH1", "/v/abc", "failed", probed_at="2026-06-19T02:00:00Z")
    assert repo.list_pending() == []


def test_list_pending_respects_limit(repo):
    for i in range(5):
        repo.enqueue(_cand(h=f"H{i}"), enqueued_at="2026-06-19T00:00:00Z")
    assert len(repo.list_pending(limit=3)) == 3
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
pytest tests/unit/test_torrent_probe_repo.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.storage.repos.torrent_probe_repo`.

- [ ] **Step 4: Implement the repo**

Create `javdb/storage/repos/torrent_probe_repo.py`:

```python
"""ADR-024 IMP-10: TorrentProbeCandidate queue repository (conn-injected).

D1 is the source of truth; this repo is the read/write port used by both the
capture step (enqueue) and the remote probe runner (list_pending / mark_status).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ProbeCandidate:
    info_hash: str
    movie_href: str
    magnet_uri: str
    video_code: Optional[str] = None
    javdb_category: Optional[str] = None
    magnet_name: Optional[str] = None
    javdb_tags: List[str] = field(default_factory=list)
    javdb_size_text: Optional[str] = None


class TorrentProbeRepo:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def enqueue(self, cand: ProbeCandidate, *, enqueued_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO TorrentProbeCandidate (
                info_hash, movie_href, video_code, javdb_category, magnet_uri,
                magnet_name, javdb_tags_json, javdb_size_text, status, enqueued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(info_hash, movie_href) DO UPDATE SET
                video_code=excluded.video_code,
                javdb_category=excluded.javdb_category,
                magnet_uri=excluded.magnet_uri,
                magnet_name=excluded.magnet_name,
                javdb_tags_json=excluded.javdb_tags_json,
                javdb_size_text=excluded.javdb_size_text
            """,
            (
                cand.info_hash, cand.movie_href, cand.video_code,
                cand.javdb_category, cand.magnet_uri, cand.magnet_name,
                json.dumps(cand.javdb_tags or [], ensure_ascii=False),
                cand.javdb_size_text, enqueued_at,
            ),
        )

    def list_pending(self, *, limit: Optional[int] = None) -> List[ProbeCandidate]:
        sql = (
            "SELECT info_hash, movie_href, video_code, javdb_category, magnet_uri, "
            "magnet_name, javdb_tags_json, javdb_size_text "
            "FROM TorrentProbeCandidate WHERE status='pending' ORDER BY enqueued_at"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            ProbeCandidate(
                info_hash=r[0], movie_href=r[1], video_code=r[2],
                javdb_category=r[3], magnet_uri=r[4], magnet_name=r[5],
                javdb_tags=json.loads(r[6]) if r[6] else [], javdb_size_text=r[7],
            )
            for r in rows
        ]

    def mark_status(
        self, info_hash: str, movie_href: str, status: str, *, probed_at: str
    ) -> None:
        self.conn.execute(
            "UPDATE TorrentProbeCandidate SET status=?, probed_at=? "
            "WHERE info_hash=? AND movie_href=?",
            (status, probed_at, info_hash, movie_href),
        )
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
pytest tests/unit/test_torrent_probe_repo.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add javdb/migrations/d1/2026_06_19_add_torrent_probe_candidate.sql javdb/storage/repos/torrent_probe_repo.py tests/unit/test_torrent_probe_repo.py
git commit -m "feat(db): add TorrentProbeCandidate queue table + repo (ADR-024)"
```

> **Operator note (out of band, not code):** apply the migration to remote D1, then
> re-align the SQLite mirror per CLAUDE.md (`python3 -m apps.cli.db.sync_d1_to_sqlite
> --apply --force-overwrite-all`). Record this in the IMP completion note.

---

## Task 3 — Bounded Top-K enqueue helper

**Files:**
- Create: `javdb/quality/probe_queue.py`
- Test: `tests/unit/test_probe_queue.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_probe_queue.py`:

```python
"""ADR-024 IMP-10: bounded Top-K runner-up enqueue."""

from __future__ import annotations

from javdb.quality.probe_queue import enqueue_runner_ups, maybe_capture_runner_ups


class _FakeRepo:
    def __init__(self):
        self.rows = []

    def enqueue(self, cand, *, enqueued_at):
        self.rows.append(cand)


def _m(name, href):
    return {"name": name, "tags": ["中文字幕"], "size": "5GB",
            "timestamp": "2026-06-10", "href": href, "file_count": 1}


def _two_subtitle_candidates():
    return [
        _m("best", "magnet:?xt=urn:btih:" + "a" * 40),
        _m("runner", "magnet:?xt=urn:btih:" + "b" * 40),
    ]


def test_maybe_capture_disabled_is_noop():
    repo = _FakeRepo()
    n = maybe_capture_runner_ups(
        _two_subtitle_candidates(), context={"movie_href": "/v/abc"},
        repo=repo, enabled=False, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []


def test_maybe_capture_enabled_enqueues_runner_up_only():
    repo = _FakeRepo()
    n = maybe_capture_runner_ups(
        _two_subtitle_candidates(), context={"movie_href": "/v/abc"},
        repo=repo, enabled=True, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1  # 2 candidates → 1 runner-up
    assert repo.rows[0].info_hash == "b" * 40


def test_enqueues_runner_ups_with_movie_context():
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("rb", "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567")],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo,
        context={"movie_href": "/v/abc", "video_code": "ABC-123"},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1
    row = repo.rows[0]
    assert row.movie_href == "/v/abc"
    assert row.javdb_category == "subtitle"
    assert row.info_hash == "0123456789abcdef0123456789abcdef01234567"


def test_global_cap_limits_total_enqueued():
    repo = _FakeRepo()
    many = [_m(f"r{i}", f"magnet:?xt=urn:btih:{i:040x}") for i in range(8)]
    runner_ups = {"subtitle": many, "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": "/v/abc"},
        global_cap=3, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 3
    assert len(repo.rows) == 3


def test_skips_magnet_without_btih():
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("bad", "magnet:?dn=no-hash")],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": "/v/abc"},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0


def test_skips_when_no_movie_href():
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("r", "magnet:?xt=urn:btih:" + "a" * 40)],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": ""},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0  # without a movie context the candidate can't be scored later
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_probe_queue.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality.probe_queue`.

- [ ] **Step 3: Implement the helper**

Create `javdb/quality/probe_queue.py`:

```python
"""ADR-024 IMP-10: turn Top-K runner-up magnets into bounded probe-queue rows.

Pure and injectable: ``repo`` is any object with ``enqueue(ProbeCandidate, *,
enqueued_at)``. Capture is additive and never affects production selection.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from javdb.parsing.magnet_categorize import collect_runner_ups
from javdb.storage.repos.torrent_probe_repo import ProbeCandidate

_BTIH = re.compile(r"xt=urn:btih:([0-9a-zA-Z]+)", re.IGNORECASE)


def _info_hash_from_magnet(magnet_uri: str) -> str:
    m = _BTIH.search(magnet_uri or "")
    return m.group(1).lower() if m else ""


def maybe_capture_runner_ups(
    magnets,
    *,
    context: dict,
    repo: Any,
    enabled: bool,
    k: int,
    global_cap: int,
    enqueued_at: str,
) -> int:
    """Gated, additive Top-K capture: extract runner-ups then enqueue them.

    No-op (returns 0) when ``enabled`` is False. Reads the raw ``magnets`` list
    only — never the production selection result — so selection stays identical.
    """
    if not enabled:
        return 0
    runner_ups = collect_runner_ups(magnets, k=k)
    return enqueue_runner_ups(
        runner_ups, repo=repo, context=context,
        global_cap=global_cap, enqueued_at=enqueued_at,
    )


def enqueue_runner_ups(
    runner_ups: Dict[str, List[dict]],
    *,
    repo: Any,
    context: dict,
    global_cap: int,
    enqueued_at: str,
) -> int:
    """Enqueue runner-up candidates (bounded by ``global_cap``). Returns count.

    Skips candidates whose magnet has no btih (can't be tracked/deleted) and
    skips entirely when ``movie_href`` is empty (assist scoring needs the movie
    context, and the queue PK requires a movie_href).
    """
    movie_href = (context.get("movie_href") or "").strip()
    if not movie_href:
        return 0

    enqueued = 0
    for category, magnets in runner_ups.items():
        for magnet in magnets:
            if enqueued >= global_cap:
                return enqueued
            magnet_uri = magnet.get("href", "")
            info_hash = _info_hash_from_magnet(magnet_uri)
            if not info_hash:
                continue
            repo.enqueue(
                ProbeCandidate(
                    info_hash=info_hash,
                    movie_href=movie_href,
                    magnet_uri=magnet_uri,
                    video_code=context.get("video_code"),
                    javdb_category=category,
                    magnet_name=magnet.get("name"),
                    javdb_tags=list(magnet.get("tags", []) or []),
                    javdb_size_text=magnet.get("size"),
                ),
                enqueued_at=enqueued_at,
            )
            enqueued += 1
    return enqueued
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_probe_queue.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/quality/probe_queue.py tests/unit/test_probe_queue.py
git commit -m "feat(quality): bounded Top-K runner-up enqueue + gated capture (ADR-024)"
```

---

## Task 4 — qB client: `stop_condition` + metadata-only capability detection

**Files:**
- Modify: `javdb/integrations/qb/client.py`
- Test: `tests/unit/test_qb_client_probe.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_qb_client_probe.py`:

```python
"""ADR-024 IMP-10: qB client metadata-only probe support."""

from __future__ import annotations

from javdb.integrations.qb.client import (
    WEBAPI_MIN_FOR_STOP_CONDITION,
    webapi_supports_metadata_only,
)


def test_version_at_threshold_supports():
    assert webapi_supports_metadata_only("2.8.3") is True


def test_newer_version_supports():
    assert webapi_supports_metadata_only("2.11.0") is True


def test_older_version_unsupported():
    assert webapi_supports_metadata_only("2.8.2") is False


def test_blank_or_garbage_is_unsupported_fail_closed():
    assert webapi_supports_metadata_only("") is False
    assert webapi_supports_metadata_only("not-a-version") is False


def test_threshold_constant_is_283():
    assert WEBAPI_MIN_FOR_STOP_CONDITION == (2, 8, 3)
```

Add an `add_torrent` stop-condition test in the same file:

```python
class _FakeResp:
    status_code = 200


def test_add_torrent_sends_stop_condition(monkeypatch):
    from javdb.integrations.qb import client as qbmod

    captured = {}

    class _C(qbmod.QBittorrentClient):
        def __init__(self):  # bypass real login/network
            self.base_url = "http://probe"
            self.session = type("S", (), {"post": self._post})()
            self.proxies = None
            self.request_timeout = None

        def _post(self, url, data=None, **kw):
            captured["data"] = data
            return _FakeResp()

    ok = _C().add_torrent(
        "magnet:?xt=urn:btih:abc", category="JavDB Quality Shadow",
        paused=True, stop_condition="MetadataReceived",
    )
    assert ok is True
    assert captured["data"]["stopCondition"] == "MetadataReceived"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_qb_client_probe.py -v
```

Expected: FAIL with `ImportError` (names not defined).

- [ ] **Step 3: Implement in `client.py`**

Add near the top of `javdb/integrations/qb/client.py` (after imports):

```python
# ADR-024 IMP-10: stopCondition=MetadataReceived requires qB Web API >= 2.8.3
# (qBittorrent >= 4.4.0).
WEBAPI_MIN_FOR_STOP_CONDITION = (2, 8, 3)


def webapi_supports_metadata_only(webapi_version: str) -> bool:
    """True iff the qB Web API version supports stopCondition. Fail-closed."""
    parts = (webapi_version or "").strip().split(".")
    try:
        parsed = tuple(int(p) for p in parts[:3])
    except (TypeError, ValueError):
        return False
    if len(parsed) < 3:
        parsed = parsed + (0,) * (3 - len(parsed))
    return parsed >= WEBAPI_MIN_FOR_STOP_CONDITION
```

In `add_torrent`, add the keyword and wire it into `data` (insert
`stop_condition: Optional[str] = None` into the signature, before `paused`):

```python
        if stop_condition is not None:
            # qB /api/v2/torrents/add: "stopCondition" (MetadataReceived | None).
            data["stopCondition"] = stop_condition
```

Add a method on `QBittorrentClient` to read the Web API version:

```python
    def get_webapi_version(self) -> str:
        """Return the qB Web API version string, or '' on failure (fail-closed)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v2/app/webapiVersion",
                **self._request_kwargs(),
            )
            if resp.status_code == 200:
                return (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001 - capability probe is best-effort
            logger.warning("qB webapiVersion probe failed: %s", exc)
        return ""

    def supports_metadata_only_probe(self) -> bool:
        return webapi_supports_metadata_only(self.get_webapi_version())
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_qb_client_probe.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/qb/client.py tests/unit/test_qb_client_probe.py
git commit -m "feat(qb): add stopCondition + metadata-only capability detection (ADR-024)"
```

---

## Task 5 — Remote `quality_probe` client factory + config plumbing

**Files:**
- Create: `javdb/quality/probe_client.py`
- Test: `tests/unit/test_probe_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_probe_client.py`:

```python
"""ADR-024 IMP-10: remote quality_probe client factory."""

from __future__ import annotations

from javdb.quality import probe_client as pc


def test_returns_none_when_url_unconfigured(monkeypatch):
    monkeypatch.setattr(pc, "cfg", lambda k, d=None: "" if k == "QUALITY_PROBE_QB_URL" else d)
    assert pc.build_probe_client() is None


def test_builds_client_with_probe_credentials(monkeypatch):
    values = {
        "QUALITY_PROBE_QB_URL": "https://probe:8080",
        "QUALITY_PROBE_QB_USERNAME": "u",
        "QUALITY_PROBE_QB_PASSWORD": "p",
    }
    monkeypatch.setattr(pc, "cfg", lambda k, d=None: values.get(k, d))

    captured = {}

    def _fake_client(base_urls, username, password, **kw):
        captured.update(base_urls=base_urls, username=username, password=password)
        return object()

    monkeypatch.setattr(pc, "QBittorrentClient", _fake_client)
    client = pc.build_probe_client()
    assert client is not None
    assert captured["base_urls"] == "https://probe:8080"
    assert captured["username"] == "u"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_probe_client.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality.probe_client`.

- [ ] **Step 3: Implement the factory**

Create `javdb/quality/probe_client.py`:

```python
"""ADR-024 IMP-10: build the remote quality_probe qBittorrent client from config.

Returns None (fail-closed) when no probe endpoint is configured, so callers skip
probing cleanly on a fresh deploy.
"""

from __future__ import annotations

import logging
from typing import Optional

from javdb.infra.config import cfg
from javdb.integrations.qb.client import QBittorrentClient

logger = logging.getLogger(__name__)

PROBE_CATEGORY = "JavDB Quality Shadow"


def build_probe_client(use_proxy: bool = False, proxies_getter=None) -> Optional[QBittorrentClient]:
    url = (cfg("QUALITY_PROBE_QB_URL", "") or "").strip()
    if not url:
        logger.info("quality_probe endpoint not configured; skipping probe")
        return None
    username = cfg("QUALITY_PROBE_QB_USERNAME", "") or cfg("QB_USERNAME", "")
    password = cfg("QUALITY_PROBE_QB_PASSWORD", "") or cfg("QB_PASSWORD", "")
    try:
        return QBittorrentClient(
            url, username, password,
            use_proxy=use_proxy, proxies_getter=proxies_getter,
            request_timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001 - login/network failure must fail closed
        logger.warning("quality_probe login failed (%s); skipping probe", exc)
        return None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_probe_client.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/quality/probe_client.py tests/unit/test_probe_client.py
git commit -m "feat(quality): remote quality_probe client factory (ADR-024)"
```

---

## Task 6 — Probe runner (add → poll → features → evidence → delete, fail-closed)

**Files:**
- Create: `javdb/quality/probe_runner.py`
- Modify: `javdb/integrations/qb/client.py` — add an additive `get_torrent_files(info_hash)` method (the runner + Task 8 wiring call `client.get_torrent_files`; the production client lacks one today — the file-list fetch currently lives in `javdb/integrations/qb/readonly.py:get_torrent_files(session, base_url, hash, ...)`). Wrap that helper so the probe client can fetch its own files.
- Test: `tests/unit/test_probe_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_probe_runner.py`:

```python
"""ADR-024 IMP-10: remote probe lifecycle orchestration."""

from __future__ import annotations

from javdb.quality.probe_runner import probe_candidates
from javdb.storage.repos.torrent_probe_repo import ProbeCandidate


class _Repo:
    def __init__(self, pending):
        self._pending = pending
        self.status = {}

    def list_pending(self, *, limit=None):
        return self._pending[: limit or len(self._pending)]

    def mark_status(self, info_hash, movie_href, status, *, probed_at):
        self.status[(info_hash, movie_href)] = status


class _EvidenceRepo:
    def __init__(self):
        self.rows = []

    def upsert_evidence(self, rec):
        self.rows.append(rec)


class _Client:
    """Fake qB probe client. ``files_by_hash`` drives poll outcomes."""

    def __init__(self, files_by_hash, supports=True):
        self._files = files_by_hash
        self._supports = supports
        self.added = []
        self.deleted = []

    def supports_metadata_only_probe(self):
        return self._supports

    def add_torrent(self, magnet, name=None, category=None, paused=False, stop_condition=None):
        self.added.append((magnet, category, stop_condition))
        return True

    def get_torrent_files(self, info_hash):
        return self._files.get(info_hash)

    def delete_torrents(self, hashes, delete_files=True):
        self.deleted.append((tuple(hashes), delete_files))
        return True


def _cand(h):
    return ProbeCandidate(info_hash=h, movie_href="/v/abc",
                          magnet_uri=f"magnet:?xt=urn:btih:{h}", javdb_category="subtitle")


def test_happy_path_collects_evidence_and_deletes_keep_files():
    files = {"HASH1": [{"name": "ABC.mkv", "size": 5_000_000_000, "priority": 1}]}
    client = _Client(files)
    qrepo = _Repo([_cand("HASH1")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )

    assert summary["probed"] == 1
    assert client.added[0][1] == "JavDB Quality Shadow"
    assert client.added[0][2] == "MetadataReceived"
    assert client.deleted == [(("HASH1",), False)]  # deleteFiles=false
    ev = erepo.rows[0]
    assert ev.target_role == "quality_probe"
    assert ev.metadata_status == "metadata_received"
    assert qrepo.status[("HASH1", "/v/abc")] == "probed"


def test_capability_unsupported_fails_closed():
    client = _Client({}, supports=False)
    qrepo = _Repo([_cand("HASH1")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )

    assert summary["capability_unsupported"] == 1
    assert client.added == []  # never added a torrent
    assert erepo.rows[0].metadata_status == "probe_capability_unsupported"


def test_timeout_records_pending_timeout_and_deletes():
    client = _Client({})  # files never arrive
    qrepo = _Repo([_cand("HASH1")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=2,
    )

    assert summary["timeout"] == 1
    assert client.deleted == [(("HASH1",), False)]  # cleaned up even on timeout
    assert erepo.rows[0].metadata_status == "pending_timeout"
    assert qrepo.status[("HASH1", "/v/abc")] == "failed"


def test_no_pending_is_a_noop():
    client = _Client({})
    summary = probe_candidates(
        client=client, queue_repo=_Repo([]), evidence_repo=_EvidenceRepo(),
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )
    assert summary == {"probed": 0, "timeout": 0, "capability_unsupported": 0, "scanned": 0}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_probe_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality.probe_runner`.

- [ ] **Step 3: Implement the runner**

Create `javdb/quality/probe_runner.py`:

```python
"""ADR-024 IMP-10: remote quality_probe lifecycle (shadow-only, fail-closed).

For each pending runner-up candidate: confirm metadata-only capability once,
add the magnet to the dedicated shadow category with stopCondition, poll for the
file list (bounded), extract features, UPSERT a ``quality_probe`` evidence row,
then remove the torrent with ``deleteFiles=false``. Timeouts and failures are
recorded and cleaned up — nothing here ever raises into ingestion.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features
from javdb.quality.models import EvidenceRecord
from javdb.quality.probe_client import PROBE_CATEGORY

logger = logging.getLogger(__name__)

PROBE_TARGET_ROLE = "quality_probe"


def probe_candidates(
    *,
    client: Any,
    queue_repo: Any,
    evidence_repo: Any,
    now: str,
    poll: Callable[[], None],
    max_polls: int = 30,
    limit: Optional[int] = None,
) -> dict:
    """Probe pending candidates. ``poll`` is the inter-poll sleep (injected)."""
    summary = {"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}

    pending = queue_repo.list_pending(limit=limit)
    if not pending:
        return summary

    if not client.supports_metadata_only_probe():
        for cand in pending:
            summary["scanned"] += 1
            summary["capability_unsupported"] += 1
            evidence_repo.upsert_evidence(EvidenceRecord(
                info_hash=cand.info_hash,
                probe_schema_version=PROBE_SCHEMA_VERSION,
                target_role=PROBE_TARGET_ROLE,
                probe_target_name="quality_probe",
                metadata_status="probe_capability_unsupported",
                reasons=["probe_capability_unsupported"],
            ))
            queue_repo.mark_status(cand.info_hash, cand.movie_href, "failed", probed_at=now)
        return summary

    for cand in pending:
        summary["scanned"] += 1
        # D7: ACTIVE (paused=False) so qB fetches metadata; stopCondition auto-
        # stops it once metadata arrives. A paused torrent never fetches metadata
        # — the stop condition would never fire (corrected during execution).
        client.add_torrent(
            cand.magnet_uri, category=PROBE_CATEGORY,
            paused=False, stop_condition="MetadataReceived",
        )
        files = None
        for _ in range(max_polls):
            files = client.get_torrent_files(cand.info_hash)
            if files:
                break
            poll()

        if not files:
            evidence_repo.upsert_evidence(EvidenceRecord(
                info_hash=cand.info_hash,
                probe_schema_version=PROBE_SCHEMA_VERSION,
                target_role=PROBE_TARGET_ROLE,
                probe_target_name="quality_probe",
                metadata_status="pending_timeout",
                reasons=["pending_timeout"],
            ))
            queue_repo.mark_status(cand.info_hash, cand.movie_href, "failed", probed_at=now)
            summary["timeout"] += 1
        else:
            feats = extract_file_features(files)
            evidence_repo.upsert_evidence(EvidenceRecord(
                info_hash=cand.info_hash,
                probe_schema_version=PROBE_SCHEMA_VERSION,
                target_role=PROBE_TARGET_ROLE,
                probe_target_name="quality_probe",
                metadata_status="metadata_received",
                total_size_bytes=feats["total_size_bytes"],
                main_video_size_bytes=feats["main_video_size_bytes"],
                main_video_ratio=feats["main_video_ratio"],
                video_file_count=feats["video_file_count"],
                subtitle_file_count=feats["subtitle_file_count"],
                non_video_file_count=feats["non_video_file_count"],
                junk_size_bytes=feats["junk_size_bytes"],
                junk_size_ratio=feats["junk_size_ratio"],
                suspicious_file_count=feats["suspicious_file_count"],
                features={"main_video_name": feats["main_video_name"]},
            ))
            queue_repo.mark_status(cand.info_hash, cand.movie_href, "probed", probed_at=now)
            summary["probed"] += 1

        # D6: short-lived — remove the probe torrent, keep no files.
        client.delete_torrents([cand.info_hash], delete_files=False)

    logger.info(
        "quality_probe: scanned=%d probed=%d timeout=%d capability_unsupported=%d",
        summary["scanned"], summary["probed"], summary["timeout"],
        summary["capability_unsupported"],
    )
    return summary
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_probe_runner.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/quality/probe_runner.py tests/unit/test_probe_runner.py
git commit -m "feat(quality): remote metadata-only probe runner (ADR-024)"
```

---

## Task 7 — Spider-seam capture wiring (enqueue runner-ups during ingestion)

**Files:**
- Modify: `javdb/spider/detail/runner.py`
- Test: `tests/unit/test_runner_up_capture.py`

> **Seam (verified):** the raw magnet list is produced by
> `categorize(detail.get_magnets_as_legacy(), ...)` in `_spider_parse_fn`
> (`javdb/spider/detail/parallel_mode.py:52`), which runs in **parallel worker
> threads** and returns a parsed dict containing both `magnet_links` (the reduced
> selection) **and** `movie_detail` (the `detail` object — it still exposes
> `get_magnets_as_legacy()`). **Do NOT write to the DB inside `_spider_parse_fn`**
> (worker threads, no shared conn). Instead, capture in the **single-threaded
> persistence path** of `process_detail_entries` (`javdb/spider/detail/runner.py`),
> where each parsed result is saved via `save_parsed_movie_to_history` and the
> movie `href` / `video_code` are in scope. The raw magnets are recoverable there
> as `result['movie_detail'].get_magnets_as_legacy()`.
>
> This task adds a small, unit-testable adapter `_capture_runner_ups_for_result`
> and calls it at that persistence point behind the capture gate. The pure
> enqueue logic is already tested in Task 3; this test pins the adapter's read of
> `movie_detail` + gating. The wiring itself is guarded by the no-regression run.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_runner_up_capture.py`:

```python
"""ADR-024 IMP-10: spider-seam runner-up capture adapter."""

from __future__ import annotations

from javdb.spider.detail.runner import _capture_runner_ups_for_result


class _Repo:
    def __init__(self):
        self.rows = []

    def enqueue(self, cand, *, enqueued_at):
        self.rows.append(cand)


class _Detail:
    """Fake parsed detail exposing the legacy magnet list."""

    def __init__(self, magnets):
        self._magnets = magnets

    def get_magnets_as_legacy(self):
        return self._magnets


def _m(name, href):
    return {"name": name, "tags": ["中文字幕"], "size": "5GB",
            "timestamp": "2026-06-10", "href": href, "file_count": 1}


def _result():
    return {
        "movie_detail": _Detail([
            _m("best", "magnet:?xt=urn:btih:" + "a" * 40),
            _m("runner", "magnet:?xt=urn:btih:" + "b" * 40),
        ]),
    }


def test_disabled_captures_nothing():
    repo = _Repo()
    n = _capture_runner_ups_for_result(
        _result(), href="/v/abc", video_code="ABC-123",
        repo=repo, enabled=False, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []


def test_enabled_enqueues_runner_up_with_context():
    repo = _Repo()
    n = _capture_runner_ups_for_result(
        _result(), href="/v/abc", video_code="ABC-123",
        repo=repo, enabled=True, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1  # 2 candidates → 1 runner-up
    row = repo.rows[0]
    assert row.info_hash == "b" * 40
    assert row.movie_href == "/v/abc"
    assert row.video_code == "ABC-123"


def test_missing_movie_detail_is_safe_noop():
    repo = _Repo()
    n = _capture_runner_ups_for_result(
        {}, href="/v/abc", video_code="ABC-123",
        repo=repo, enabled=True, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_runner_up_capture.py -v
```

Expected: FAIL with `ImportError: cannot import name '_capture_runner_ups_for_result'`.

- [ ] **Step 3: Implement the adapter + wire it into the persistence path**

In `javdb/spider/detail/runner.py`, add the adapter (imports at top as needed):

```python
from javdb.quality.probe_queue import maybe_capture_runner_ups


def _capture_runner_ups_for_result(
    result: dict,
    *,
    href: str,
    video_code,
    repo,
    enabled: bool,
    k: int,
    global_cap: int,
    enqueued_at: str,
) -> int:
    """ADR-024 IMP-10: additively enqueue Top-K runner-ups for a parsed result.

    Reads the raw magnet list from ``result['movie_detail']`` (the production
    selection result is untouched). Safe no-op when capture is disabled or the
    detail object is missing/odd. Never raises into the persistence path.
    """
    if not enabled:
        return 0
    detail = result.get("movie_detail")
    getter = getattr(detail, "get_magnets_as_legacy", None)
    if getter is None:
        return 0
    try:
        magnets = getter() or []
        return maybe_capture_runner_ups(
            magnets,
            context={"movie_href": href, "video_code": video_code},
            repo=repo, enabled=True, k=k, global_cap=global_cap,
            enqueued_at=enqueued_at,
        )
    except Exception:  # noqa: BLE001 - shadow capture must never break ingestion
        logger.warning("Runner-up capture failed for %s", href, exc_info=True)
        return 0
```

Then call it at the single-threaded persistence point — alongside the existing
`save_parsed_movie_to_history(...)` call for each parsed result — behind the gate.
Resolve config + a per-run queue repo once (not per movie):

```python
# Resolve once near the top of process_detail_entries (gated; cheap when off):
#   from javdb.infra.config import cfg
#   from javdb.infra.config_generator import get_env_int  # or the repo's int reader
#   _capture_enabled = bool(cfg("TORRENT_QUALITY_EVIDENCE_ENABLED", False))
#   _topk = int(cfg("QUALITY_PROBE_TOPK", 2))
#   _cap  = int(cfg("QUALITY_PROBE_GLOBAL_CAP", 50))
# Open ONE TorrentProbeRepo for the run on REPORTS_DB_PATH (the ADR-024 evidence
# DB — see IMP-05 run_collection) and call the adapter where each result is saved:
#   from javdb.storage.db import REPORTS_DB_PATH, get_db
#   from javdb.storage.repos.torrent_probe_repo import TorrentProbeRepo
#   with get_db(REPORTS_DB_PATH) as _probe_conn:
#       _probe_repo = TorrentProbeRepo(_probe_conn)
#       ... for each saved result ...
#       _capture_runner_ups_for_result(
#           result, href=href, video_code=video_code, repo=_probe_repo,
#           enabled=_capture_enabled, k=_topk, global_cap=_cap,
#           enqueued_at=<run timestamp threaded in like other stamps>,
#       )
```

> **Implementer:** read `process_detail_entries` to find where each parsed
> `result` dict is saved (`save_parsed_movie_to_history`) and the loop's `href` /
> `video_code` are in scope; place the adapter call there. Keep the queue repo at
> run scope (one conn), gated so a disabled run opens nothing. `enqueued_at` must
> reuse the run's existing timestamp source — thread it in like other stamps; do
> not introduce a new ambient clock call inside a module that forbids it.

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_runner_up_capture.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Run the spider/pipeline tests to prove no regression**

```bash
pytest tests/unit -k "runner or detail or magnet or categor or pipeline" -q
```

Expected: PASS — selection + persistence behaviour unchanged.

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/detail/runner.py tests/unit/test_runner_up_capture.py
git commit -m "feat(spider): capture Top-K runner-ups for shadow probing (ADR-024)"
```

---

## Task 8 — CLI entrypoint (double-gated)

**Files:**
- Create: `apps/cli/qb/quality_probe.py`
- Test: `tests/unit/test_quality_probe_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_quality_probe_cli.py`:

```python
"""ADR-024 IMP-10: quality_probe CLI double-gate."""

from __future__ import annotations

from unittest.mock import patch

from apps.cli.qb import quality_probe as cli


def test_evidence_disabled_skips(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_probe") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_probe_disabled_skips(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: False)
    with patch.object(cli, "run_probe") as run:
        assert cli.main([]) == 0
    run.assert_not_called()


def test_both_gates_on_runs(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: True)
    with patch.object(cli, "run_probe", return_value={"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}) as run:
        assert cli.main([]) == 0
    run.assert_called_once()


def test_force_overrides_gates(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    monkeypatch.setattr(cli, "_probe_enabled", lambda: False)
    with patch.object(cli, "run_probe", return_value={"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}) as run:
        assert cli.main(["--force"]) == 0
    run.assert_called_once()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_quality_probe_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError: apps.cli.qb.quality_probe`.

- [ ] **Step 3: Implement the CLI**

Create `apps/cli/qb/quality_probe.py` (mirrors `apps/cli/qb/quality_evidence.py`):

```python
from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)

from javdb.infra.config import cfg
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override


def _evidence_enabled() -> bool:
    return bool(cfg("TORRENT_QUALITY_EVIDENCE_ENABLED", False))


def _probe_enabled() -> bool:
    return bool(cfg("QUALITY_PROBE_ENABLED", False))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Probe runner-up magnets on the remote quality_probe qB (ADR-024)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max candidates to probe this run")
    parser.add_argument("--force", action="store_true", help="Run even when gates are off")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qB API requests",
        no_help="Force-disable proxy for qB API requests",
    )
    return parser.parse_args(argv)


def run_probe(*, limit=None, use_proxy=None):
    """Production wiring: build the probe client, run the queue, write evidence."""
    import time
    from datetime import datetime, timezone

    from javdb.quality.probe_client import build_probe_client
    from javdb.quality.probe_runner import probe_candidates
    from javdb.storage.db import REPORTS_DB_PATH, get_db
    from javdb.storage.repos.torrent_probe_repo import TorrentProbeRepo
    from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

    client = build_probe_client(use_proxy=bool(use_proxy))
    if client is None:
        print("quality_probe endpoint unavailable; skipping.")
        return {"scanned": 0, "probed": 0, "timeout": 0, "capability_unsupported": 0}

    now = datetime.now(timezone.utc).isoformat()  # CLI boundary — plain UTC stamp
    with get_db(REPORTS_DB_PATH) as conn:
        return probe_candidates(
            client=client,
            queue_repo=TorrentProbeRepo(conn),
            evidence_repo=TorrentQualityRepo(conn),
            now=now,
            poll=lambda: time.sleep(2),
            limit=limit,
        )


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.force and not (_evidence_enabled() and _probe_enabled()):
        print("quality_probe disabled (needs TORRENT_QUALITY_EVIDENCE_ENABLED and QUALITY_PROBE_ENABLED); skipping.")
        return 0
    summary = run_probe(
        limit=args.limit,
        use_proxy=resolve_proxy_override(args.use_proxy, args.no_proxy),
    )
    print(
        "quality_probe summary: "
        f"scanned={summary['scanned']} probed={summary['probed']} "
        f"timeout={summary['timeout']} capability_unsupported={summary['capability_unsupported']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> **Implementer:** the `enqueued_at` stamp for capture (Task 7) lives inside the
> spider run, so reuse that run's existing timestamp source rather than a fresh
> clock call. The probe CLI here is a standalone entrypoint, so a plain
> `datetime.now(timezone.utc).isoformat()` at the boundary is fine. If a shared
> ISO-UTC helper already exists in the repo, prefer it for consistency.

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_quality_probe_cli.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/cli/qb/quality_probe.py tests/unit/test_quality_probe_cli.py
git commit -m "feat(cli): add remote quality_probe runner CLI (ADR-024)"
```

---

## Task 9 — Config keys + workflow wiring

**Files:**
- Modify: `javdb/infra/config_generator.py`
- Modify: `config.py.example`
- Modify: `.github/workflows/QBFileFilter.yml`

- [ ] **Step 1: Add config tuples**

In `javdb/infra/config_generator.py`, after the existing `TORRENT_QUALITY_*`
tuples (near line 424), add:

```python
        ('QUALITY_PROBE_ENABLED', 'QUALITY_PROBE_ENABLED', get_env_bool, False, 'TORRENT QUALITY EVIDENCE'),
        ('QUALITY_PROBE_QB_URL', 'QUALITY_PROBE_QB_URL', get_env, '', 'TORRENT QUALITY EVIDENCE'),
        ('QUALITY_PROBE_QB_USERNAME', 'QUALITY_PROBE_QB_USERNAME', get_env, '', 'TORRENT QUALITY EVIDENCE'),
        ('QUALITY_PROBE_QB_PASSWORD', 'QUALITY_PROBE_QB_PASSWORD', get_env, '', 'TORRENT QUALITY EVIDENCE'),
        ('QUALITY_PROBE_TOPK', 'QUALITY_PROBE_TOPK', get_env_int, 2, 'TORRENT QUALITY EVIDENCE'),
        ('QUALITY_PROBE_GLOBAL_CAP', 'QUALITY_PROBE_GLOBAL_CAP', get_env_int, 50, 'TORRENT QUALITY EVIDENCE'),
```

> Confirm `get_env_int` exists in `config_generator` (it is used elsewhere). If the
> helper has a different name, match the existing integer-coercing reader.

- [ ] **Step 2: Document in `config.py.example`**

In `config.py.example`, inside the `TORRENT QUALITY EVIDENCE` block (after
`TORRENT_QUALITY_CATEGORIES`, near line 562), add:

```python

# --- ADR-024 Phase 2 prerequisite: remote quality_probe endpoint (IMP-10) ---
# Runner-up shadow probing. Capture (Top-K runner-up enqueue) is gated by
# TORRENT_QUALITY_EVIDENCE_ENABLED above. Probing the runner-ups on a dedicated
# REMOTE qBittorrent requires QUALITY_PROBE_ENABLED *and* a configured endpoint.
# Both default off: a fresh deploy captures nothing and probes nothing.
QUALITY_PROBE_ENABLED = False
QUALITY_PROBE_QB_URL = ''          # Dedicated remote qB; never the production NAS qB
QUALITY_PROBE_QB_USERNAME = ''     # Falls back to QB_USERNAME when empty
QUALITY_PROBE_QB_PASSWORD = ''     # Falls back to QB_PASSWORD when empty
QUALITY_PROBE_TOPK = 2             # Runner-up magnets captured per category
QUALITY_PROBE_GLOBAL_CAP = 50      # Max runner-ups enqueued per ingestion run
```

- [ ] **Step 3: Verify config generation**

```bash
python3 -c "
from javdb.infra.config_generator import get_config_map
names = {t[0] for t in get_config_map()}
need = {'QUALITY_PROBE_ENABLED','QUALITY_PROBE_QB_URL','QUALITY_PROBE_QB_USERNAME','QUALITY_PROBE_QB_PASSWORD','QUALITY_PROBE_TOPK','QUALITY_PROBE_GLOBAL_CAP'}
assert need <= names, need - names
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 4: Wire the probe step into `QBFileFilter.yml`**

In `.github/workflows/QBFileFilter.yml`, after the evidence-collection step added
by IMP-05, add a probe step in the same `run-file-filter` job:

```yaml
      - name: Probe runner-up quality candidates (ADR-024, remote shadow)
        if: ${{ vars.TORRENT_QUALITY_EVIDENCE_ENABLED == 'true' && vars.QUALITY_PROBE_ENABLED == 'true' && github.event.inputs.dry_run != 'true' }}
        run: |
          set -e
          set -o pipefail
          echo "Running: python3 -m apps.cli.qb.quality_probe"
          python3 -m apps.cli.qb.quality_probe
```

And add the probe `VAR_*` entries to the `setup` job's `Generate config.py` `env:`
block, alongside the IMP-05 quality vars:

```yaml
          VAR_QUALITY_PROBE_ENABLED: ${{ vars.QUALITY_PROBE_ENABLED || 'false' }}
          VAR_QUALITY_PROBE_QB_URL: ${{ secrets.QUALITY_PROBE_QB_URL || '' }}
          VAR_QUALITY_PROBE_QB_USERNAME: ${{ secrets.QUALITY_PROBE_QB_USERNAME || '' }}
          VAR_QUALITY_PROBE_QB_PASSWORD: ${{ secrets.QUALITY_PROBE_QB_PASSWORD || '' }}
          VAR_QUALITY_PROBE_TOPK: ${{ vars.QUALITY_PROBE_TOPK || '2' }}
          VAR_QUALITY_PROBE_GLOBAL_CAP: ${{ vars.QUALITY_PROBE_GLOBAL_CAP || '50' }}
```

> Probe endpoint credentials are **secrets** (`secrets.QUALITY_PROBE_QB_*`), not
> plain `vars`, because they are qB login credentials.

- [ ] **Step 5: Lint the workflow + confirm wiring**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/QBFileFilter.yml')); print('yaml ok')"
grep -n "apps.cli.qb.quality_probe" .github/workflows/QBFileFilter.yml
```

Expected: `yaml ok` + one match.

- [ ] **Step 6: Commit**

```bash
git add javdb/infra/config_generator.py config.py.example .github/workflows/QBFileFilter.yml
git commit -m "feat(config,ci): wire remote quality_probe endpoint + gates (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Runner-up extraction, production unchanged | `pytest tests/unit/test_magnet_runner_ups.py -v` PASS **and** `pytest tests/unit -k "magnet or categor" -q` PASS |
| 2 | Queue repo | `pytest tests/unit/test_torrent_probe_repo.py -v` PASS |
| 3 | Bounded Top-K enqueue | `pytest tests/unit/test_probe_queue.py -v` PASS |
| 4 | qB stop_condition + capability | `pytest tests/unit/test_qb_client_probe.py -v` PASS |
| 5 | Probe client factory | `pytest tests/unit/test_probe_client.py -v` PASS |
| 6 | Probe lifecycle + fail-closed | `pytest tests/unit/test_probe_runner.py -v` PASS |
| 7 | Capture hook gated + additive | `pytest tests/unit/test_runner_up_capture.py -v` PASS **and** `pytest tests/unit -k "runner or detail or magnet or categor or pipeline" -q` PASS |
| 8 | CLI double-gate | `pytest tests/unit/test_quality_probe_cli.py -v` PASS |
| 9 | Config generates | Task 9 Step 3 prints `ok` |
| 10 | Workflow valid + wired | `yaml ok` + `grep` finds `apps.cli.qb.quality_probe` |
| 11 | No production qB mutation in capture | `grep -nE "add_torrent|delete_torrents|set_file_priority" javdb/spider/detail/runner.py javdb/quality/probe_queue.py` → no output (mutation lives only in `probe_runner.py`/`client.py`, against the probe endpoint) |
| 12 | Production selection byte-identical | runner-up capture never writes into the `categorize`/selection result (Task 1 parity test + Task 7 regression run) |
| 13 | D1 migration applied + SQLite re-aligned | Operator note in Task 2 done; record in completion note |

## Scope reminder for the next IMPs

- **IMP-08 (assist):** scores production vs. `quality_probe` rows, sets
  `shadow_rank` / `would_replace_current_choice`, surfaces `/api/quality`
  recommendations + a `needs_review` queue, and adds an **operator review-label**
  store (the labelled dataset Phase 3 tunes against). Web UI deferred.
- **IMP-09 (enforce machinery):** rollout gate, offline replay/threshold tool,
  backfill + reporting, guardrails + off-switch — enforce stays gated OFF.
