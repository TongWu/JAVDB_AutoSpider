# IMP-ADR024-08: ADR-024 Phase 2 — Assist Mode (CICD Python backend)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Completed — 2026-06-20.

## Completion note (2026-06-19)

All six implementation tasks landed on branch `claude/adr024-imp08-assist`:

- **Ranking** (`javdb/quality/assist.py`) — pure `rank_candidates()` assigns `shadow_rank` (1 = best score) and `would_replace_current_choice` on the production candidate when any probe outranks it. Ties keep production ahead for stability. 5 unit tests.
- **Review-label store** (`javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql` + `javdb/storage/repos/torrent_quality_review_repo.py` + local-DDL mirror in `_db_migrations.py`) — `TorrentQualityReviewLabel` table (D1-first) with `upsert_label` / `list_labels`. 3 repo tests + `test_rollback_full_fidelity.py` green.
- **Evidence join** (`javdb/storage/repos/torrent_quality_repo.py::list_evidence_for_movie`) — UNION query joins production evidence (via `TorrentQualityEvaluation`) and probe evidence (via `TorrentProbeCandidate`) for a given `movie_href`.
- **Gated evaluator** (`javdb/quality/assist_evaluator.py`) — `evaluate_assist_for_movies()` is a no-op unless `policy_mode='assist'`; scores each candidate, groups by inferred category, calls `rank_candidates`, UPSERTs `TorrentQualityEvaluation` rows with `policy_mode='assist'`. Returns `{movies, candidates, would_replace}` summary.
- **API** (`apps/api/routers/quality.py` + `apps/api/schemas/quality.py`) — three new JWT-gated endpoints: `GET /api/quality/recommendations`, `GET /api/quality/needs-review`, `POST /api/quality/review-labels`. Limit cap 200; invalid label → 422; reviewer stamped from JWT subject.
- **CLI + workflow** (`apps/cli/qb/quality_assist.py` + `QBFileFilter.yml` + `config.py.example`) — double-gated CLI (requires `TORRENT_QUALITY_EVIDENCE_ENABLED=true` AND `policy_mode=assist`); assist step added to `QBFileFilter.yml` after the probe step.

**Outstanding operator step (out of band):** apply `javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql` to remote `javdb-reports` D1 database:
```
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql
```
Then optionally re-align local SQLite via `python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`.

> **History:** this file began as the Phase-2 *outline*. The outline's Task D
> (Top-K + remote probe) shipped as [IMP-ADR024-10](IMP-ADR024-10-remote-probe-topk.md).
> Its Task C (Web review UI) is deferred to a separate `javdb-autospider-web`
> round (ADR-018 dual-backend). This refined plan covers the **CICD Python**
> assist backend: ranking, the operator review-label store, and the read/write
> API. The Web client consumes the API this plan defines.

**Goal:** Move from shadow-only observation to **assist** mode — rank the
production-downloaded torrent against the IMP-10 `quality_probe` runner-up
evidence per movie+category, record `shadow_rank` / `would_replace_current_choice`
on evaluation rows, expose a per-movie recommendation + a `needs_review` queue
over the API, and persist operator accept/reject **review labels** (the labelled
dataset Phase 3 tunes thresholds against). **No production download decision
changes** — assist writes evaluations and review labels only.

**Architecture:** Three additive layers over the Phase-1 / IMP-10 foundation:
1. **Ranking (pure).** `javdb/quality/assist.py::rank_candidates` takes the
   candidate evaluations for one (movie_href, category) and assigns `shadow_rank`
   (1 = best by score) + `would_replace_current_choice` (True on the
   `production_download` candidate iff a `quality_probe` candidate outranks it).
2. **Evaluator (orchestration, gated).** When `TORRENT_QUALITY_POLICY_MODE=assist`,
   re-score the stored `TorrentQualityEvidence` rows (production + probe) per
   movie, rank within category, and UPSERT `TorrentQualityEvaluation` rows with
   `policy_mode='assist'`. Writes-only.
3. **Review-label store + API.** A new D1-first `TorrentQualityReviewLabel` table
   + repo records operator decisions. `/api/quality` gains a recommendation view,
   a `needs_review` queue, and a review-label write endpoint (the contract the
   Web client aligns to).

**Tech Stack:** Python 3.11, FastAPI, pytest, SQL (D1 + SQLite mirror), `config_generator`.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md) D8/D11 (assist mode), the 2026-06-19 grilled decisions (full assist, API-only this round, operator review-label store), and IMP-10's "Scope reminder for the next IMPs".

**Depends on:** [IMP-ADR024-10](IMP-ADR024-10-remote-probe-topk.md) (provides `quality_probe` evidence + the probe queue) — **shipped & merged**. Phase-1 scoring/repo/API.

**Blocks:** Phase 3 ([IMP-ADR024-09](IMP-ADR024-09-phase3-enforce.md)) — enforce tunes thresholds against the review labels this plan collects. The Web review UI round consumes this plan's API.

---

## Key facts (verified against current code)

- `TorrentQualityEvaluation` already has the columns assist writes: `shadow_rank`,
  `would_replace_current_choice`, `policy_mode`, `decision` (Phase-1 left them
  null/`shadow`). **No core-table migration needed** for ranking.
- `javdb/quality/scoring.py::score_torrent(features, context)` returns
  `score` + `decision` + reason codes. Reuse it to score every candidate.
- `TorrentQualityEvidence` rows store the features assist needs: `total_size_bytes`,
  `main_video_size_bytes`, `main_video_ratio`, `video_file_count`,
  `subtitle_file_count`, `non_video_file_count`, `junk_size_bytes`,
  `junk_size_ratio`, `suspicious_file_count`, and `features_json.main_video_name`.
  The `target_role` column distinguishes `production_download` vs `quality_probe`.
- `TorrentProbeCandidate` (IMP-10) links a probe `info_hash` → `movie_href` +
  `javdb_category` + `magnet_name` (the context to re-score a probe candidate).
- `TorrentQualityRepo` (conn-injected) has: `upsert_evidence`, `get_evidence`,
  `upsert_evaluation`, `list_evaluations_for_movie`, `list_recent_evaluations`.
- `apps/api/routers/quality.py` (prefix `/api/quality`, JWT-gated) has
  `GET /evaluations` + `GET /evidence/:info_hash`. Schemas in
  `apps/api/schemas/quality.py`.
- **D1 migration lesson (from IMP-10):** any new `CREATE TABLE` migration must
  (a) carry a `-- Write-Class:` header (ADR-042 D6), (b) contain **no semicolons
  in comments** (D1 `executescript` splits on `;`), and (c) be **mirrored into
  `javdb/storage/db/_db_migrations.py`** local DDL or
  `test_rollback_full_fidelity.py` fails CI.

## Scope Boundaries

- **No production-path change.** Assist never touches the uploader, the magnet
  selection, or the download decision. It writes `TorrentQualityEvaluation` +
  `TorrentQualityReviewLabel` rows only.
- **Gated.** Ranking runs only when `TORRENT_QUALITY_POLICY_MODE=assist`. Default
  stays `shadow` (Phase-1 behaviour). The API endpoints are read-only except the
  review-label write, which records operator metadata (never gates production).
- **No Web UI** (separate `javdb-autospider-web` round; this plan defines the API
  contract it consumes).
- **No enforce / threshold tuning** (Phase 3 / IMP-09).

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/quality/assist.py` | `rank_candidates(...)` pure ranking (shadow_rank + would_replace_current_choice). |
| Create | `javdb/quality/assist_evaluator.py` | Gated orchestration: gather evidence per movie → score → rank → UPSERT evaluations. |
| Modify | `javdb/storage/repos/torrent_quality_repo.py` | Add `list_evidence_for_movie(movie_href)` (join probe candidates + production) returning evidence rows by `target_role`. |
| Create | `javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql` | D1-first review-label table (Write-Class header, no comment semicolons). |
| Modify | `javdb/storage/db/_db_migrations.py` | Mirror `TorrentQualityReviewLabel` in local `_REPORTS_DDL` (schema-parity). |
| Create | `javdb/storage/repos/torrent_quality_review_repo.py` | `TorrentQualityReviewRepo`: upsert_label / list_labels (conn-injected). |
| Modify | `apps/api/routers/quality.py` | Add `GET /recommendations`, `GET /needs-review`, `POST /review-labels`. |
| Modify | `apps/api/schemas/quality.py` | Add recommendation + review-label request/response schemas. |
| Create | `apps/cli/qb/quality_assist.py` | CLI to run the assist evaluator (gated). |
| Modify | `javdb/infra/config_generator.py` + `config.py.example` | Document `TORRENT_QUALITY_POLICY_MODE=assist` activation (key already exists). |
| Create | `tests/unit/test_quality_assist_ranking.py` | Ranking unit tests. |
| Create | `tests/unit/test_quality_assist_evaluator.py` | Evaluator orchestration tests (fakes). |
| Create | `tests/unit/test_torrent_quality_review_repo.py` | Review-label repo tests. |
| Create | `tests/unit/test_quality_assist_api.py` | API endpoint tests. |
| Create | `tests/unit/test_quality_assist_cli.py` | CLI gate tests. |

---

## Task 1 — Ranking core (pure)

**Files:** Create `javdb/quality/assist.py`; Test `tests/unit/test_quality_assist_ranking.py`.

- [ ] **Step 1: Write the failing test**

```python
"""ADR-024 IMP-08: per-category candidate ranking."""

from __future__ import annotations

from javdb.quality.assist import rank_candidates

PROD = "production_download"
PROBE = "quality_probe"


def _c(info_hash, role, score):
    return {"info_hash": info_hash, "target_role": role, "score": score}


def test_ranks_by_score_desc_and_flags_replacement_when_probe_wins():
    ranked = rank_candidates([
        _c("prod", PROD, 0.55),
        _c("probeA", PROBE, 0.80),
        _c("probeB", PROBE, 0.40),
    ])
    by_hash = {r["info_hash"]: r for r in ranked}
    assert by_hash["probeA"]["shadow_rank"] == 1
    assert by_hash["prod"]["shadow_rank"] == 2
    assert by_hash["probeB"]["shadow_rank"] == 3
    # a probe outranks production -> the ranker would replace the current choice
    assert by_hash["prod"]["would_replace_current_choice"] is True
    # probe candidates are never "the current choice"
    assert by_hash["probeA"]["would_replace_current_choice"] is False


def test_no_replacement_when_production_is_best():
    ranked = rank_candidates([
        _c("prod", PROD, 0.90),
        _c("probeA", PROBE, 0.50),
    ])
    by_hash = {r["info_hash"]: r for r in ranked}
    assert by_hash["prod"]["shadow_rank"] == 1
    assert by_hash["prod"]["would_replace_current_choice"] is False


def test_ties_keep_production_ahead_for_stability():
    # equal score: production must not be displaced by a tie
    ranked = rank_candidates([
        _c("probeA", PROBE, 0.60),
        _c("prod", PROD, 0.60),
    ])
    by_hash = {r["info_hash"]: r for r in ranked}
    assert by_hash["prod"]["shadow_rank"] == 1
    assert by_hash["prod"]["would_replace_current_choice"] is False


def test_single_production_candidate():
    ranked = rank_candidates([_c("prod", PROD, 0.7)])
    assert ranked[0]["shadow_rank"] == 1
    assert ranked[0]["would_replace_current_choice"] is False


def test_no_production_candidate_sets_replacement_false_everywhere():
    # defensive: if production evidence is missing, nothing is "the current choice"
    ranked = rank_candidates([_c("probeA", PROBE, 0.7), _c("probeB", PROBE, 0.5)])
    assert all(r["would_replace_current_choice"] is False for r in ranked)
    assert [r["shadow_rank"] for r in sorted(ranked, key=lambda r: r["shadow_rank"])] == [1, 2]
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError: javdb.quality.assist`).

- [ ] **Step 3: Implement `javdb/quality/assist.py`**

```python
"""ADR-024 IMP-08: per-category candidate ranking (pure, no I/O).

Given the scored candidates for ONE (movie_href, category) — the production
download plus any quality_probe runner-ups — assign shadow_rank (1 = best) and
flag would_replace_current_choice on the production candidate iff a probe
candidate outranks it. Shadow-only: this never changes the production download.
"""

from __future__ import annotations

from typing import Any, Dict, List

PRODUCTION_TARGET_ROLE = "production_download"
PROBE_TARGET_ROLE = "quality_probe"


def _sort_key(c: Dict[str, Any]):
    # Score desc; on ties keep the production_download candidate ahead so a tie
    # never "replaces" the current choice (stability). Final tie-break info_hash.
    is_prod = 0 if c.get("target_role") == PRODUCTION_TARGET_ROLE else 1
    return (-float(c.get("score") or 0.0), is_prod, str(c.get("info_hash") or ""))


def rank_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return new dicts (input not mutated) with shadow_rank +
    would_replace_current_choice added, in input order preserved per item."""
    order = sorted(candidates, key=_sort_key)
    rank_by_hash = {c["info_hash"]: i + 1 for i, c in enumerate(order)}
    best_hash = order[0]["info_hash"] if order else None

    out: List[Dict[str, Any]] = []
    for c in candidates:
        rank = rank_by_hash[c["info_hash"]]
        is_prod = c.get("target_role") == PRODUCTION_TARGET_ROLE
        replace = bool(is_prod and best_hash is not None and best_hash != c["info_hash"])
        out.append({**c, "shadow_rank": rank, "would_replace_current_choice": replace})
    return out
```

- [ ] **Step 4: Run → PASS (5 tests).**
- [ ] **Step 5: Commit** `feat(quality): add assist candidate ranking (ADR-024)`.

---

## Task 2 — Review-label table + repo (D1-first)

**Files:** Create the migration, the repo, the local-DDL mirror, and the test.

- [ ] **Step 1: D1 migration** `javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql`

```sql
-- 2026-06-20: Add TorrentQualityReviewLabel table (ADR-024 IMP-08).
-- Write-Class: diagnostic
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql
--
-- Diagnostic (ADR-042 D6): operator accept/reject labels over shadow quality
-- evaluations. It is the labelled dataset Phase 3 tunes thresholds against. It
-- never gates production correctness. Keep the semicolon character out of these
-- comments (the D1 apply path splits scripts on that character).
CREATE TABLE IF NOT EXISTS TorrentQualityReviewLabel (
    info_hash        TEXT NOT NULL,
    movie_href       TEXT NOT NULL,
    scoring_version  TEXT NOT NULL,
    label            TEXT NOT NULL
                         CHECK (label IN ('accept', 'reject', 'skip')),
    reviewer         TEXT,
    note             TEXT,
    reviewed_at      TEXT NOT NULL,
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);

CREATE INDEX IF NOT EXISTS idx_quality_review_label_movie
    ON TorrentQualityReviewLabel(movie_href);
```

- [ ] **Step 2: Mirror in local DDL** — in `javdb/storage/db/_db_migrations.py`,
  inside `_REPORTS_DDL` (right after the `TorrentProbeCandidate` block added by
  IMP-10, before the closing `"""`), paste the **same** `CREATE TABLE` + index
  verbatim. (Required or `test_rollback_full_fidelity.py` fails.)

- [ ] **Step 3: Write the failing repo test** `tests/unit/test_torrent_quality_review_repo.py`

```python
"""ADR-024 IMP-08: operator review-label repository."""

from __future__ import annotations

import sqlite3

import pytest

from javdb.storage.repos.torrent_quality_review_repo import (
    ReviewLabel,
    TorrentQualityReviewRepo,
)

_DDL = """
CREATE TABLE TorrentQualityReviewLabel (
    info_hash TEXT NOT NULL, movie_href TEXT NOT NULL, scoring_version TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('accept','reject','skip')),
    reviewer TEXT, note TEXT, reviewed_at TEXT NOT NULL,
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);
"""


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return TorrentQualityReviewRepo(conn)


def _label(label="accept"):
    return ReviewLabel(
        info_hash="h1", movie_href="/v/abc", scoring_version="adr024-shadow-v1",
        label=label, reviewer="ted", note="looks good",
    )


def test_upsert_then_list(repo):
    repo.upsert_label(_label(), reviewed_at="2026-06-20T00:00:00Z")
    rows = repo.list_labels(movie_href="/v/abc")
    assert len(rows) == 1
    assert rows[0]["label"] == "accept"
    assert rows[0]["reviewer"] == "ted"


def test_upsert_is_idempotent_and_updates(repo):
    repo.upsert_label(_label("accept"), reviewed_at="2026-06-20T00:00:00Z")
    repo.upsert_label(_label("reject"), reviewed_at="2026-06-20T01:00:00Z")  # same PK
    rows = repo.list_labels(movie_href="/v/abc")
    assert len(rows) == 1
    assert rows[0]["label"] == "reject"  # latest wins


def test_invalid_label_rejected_by_check(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_label(_label("maybe"), reviewed_at="2026-06-20T00:00:00Z")
```

- [ ] **Step 4: Implement `javdb/storage/repos/torrent_quality_review_repo.py`**
  (mirror the conn-injection + `sqlite3.Row` + `ON CONFLICT ... DO UPDATE` pattern
  of `torrent_probe_repo.py`). `ReviewLabel` is a dataclass
  (info_hash, movie_href, scoring_version, label, reviewer=None, note=None).
  `upsert_label(label, *, reviewed_at)` UPSERTs on the PK;
  `list_labels(*, movie_href=None, limit=None)` returns dict rows.

- [ ] **Step 5: Run → PASS (3 tests). Run `test_rollback_full_fidelity.py` → PASS** (proves the local-DDL mirror is correct).

```bash
pytest tests/unit/test_torrent_quality_review_repo.py tests/unit/test_rollback_full_fidelity.py -v
```

- [ ] **Step 6: Commit** `feat(db): add TorrentQualityReviewLabel store (ADR-024)`.

> **Operator note (out of band):** apply the migration to remote `javdb-reports`
> via `make_d1_connection('reports').executescript(...)` (or wrangler), then
> optionally re-align SQLite. Record in the completion note.

---

## Task 3 — Repo helper: gather a movie's candidate evidence

**Files:** Modify `javdb/storage/repos/torrent_quality_repo.py`; extend `tests/unit/test_torrent_quality_repo.py`.

- [ ] **Step 1: Write the failing test** — add to the existing repo test file a case
  that inserts a `production_download` evidence row + a `quality_probe` evidence row
  whose `info_hash` is linked to the same `movie_href` via a `TorrentProbeCandidate`
  row, then asserts `list_evidence_for_movie('/v/abc')` returns both, each tagged
  with its `target_role` and (for probe) the candidate's `javdb_category`/`magnet_name`.

```python
def test_list_evidence_for_movie_joins_production_and_probe(quality_repo_with_probe):
    repo, _ = quality_repo_with_probe
    rows = repo.list_evidence_for_movie("/v/abc")
    roles = {r["info_hash"]: r["target_role"] for r in rows}
    assert roles == {"prodhash": "production_download", "probehash": "quality_probe"}
    probe = next(r for r in rows if r["target_role"] == "quality_probe")
    assert probe["javdb_category"] == "subtitle"
    assert probe["movie_href"] == "/v/abc"
```

(Provide a fixture that builds an in-memory DB with the three tables — reuse the
DDL constants from the sibling tests — and seeds: one production evidence row
whose `movie_href` link comes from an existing `TorrentQualityEvaluation` row, and
one probe evidence row whose link comes from `TorrentProbeCandidate`. The
implementer wires the fixture to match the query below.)

- [ ] **Step 2: Run → FAIL** (`AttributeError: list_evidence_for_movie`).

- [ ] **Step 3: Implement `list_evidence_for_movie`** on `TorrentQualityRepo`.
  The query unions two sources keyed to `movie_href`:
  - **production:** `TorrentQualityEvidence` rows with `target_role='production_download'`
    whose `info_hash` appears in `TorrentQualityEvaluation` for that `movie_href`
    (production context already carries href from the Phase-1 collector).
  - **probe:** `TorrentQualityEvidence` rows with `target_role='quality_probe'`
    whose `info_hash` appears in `TorrentProbeCandidate` for that `movie_href`,
    carrying the candidate's `javdb_category` / `magnet_name`.
  Return a list of dict rows including `target_role`, `movie_href`, and (for probe)
  `javdb_category` / `magnet_name`. Use `LEFT JOIN`s so a movie with only
  production evidence still returns it.

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(db): list a movie's production + probe evidence (ADR-024)`.

---

## Task 4 — Assist evaluator (gated orchestration)

**Files:** Create `javdb/quality/assist_evaluator.py`; Test `tests/unit/test_quality_assist_evaluator.py`.

- [ ] **Step 1: Write the failing test** — inject a fake repo returning, for one
  movie, a production evidence row + two probe evidence rows (varying junk/main-video
  features so scores differ). Assert the evaluator:
  - reconstructs features from each evidence row and scores via `score_torrent`,
  - groups by category, ranks within category,
  - UPSERTs one `TorrentQualityEvaluation` per candidate with `policy_mode='assist'`,
    correct `shadow_rank`, and `would_replace_current_choice` set only on the
    production row when a probe outranks it,
  - returns a summary dict `{movies, candidates, would_replace}`.

```python
from javdb.quality.assist_evaluator import evaluate_assist_for_movies


class _Repo:
    def __init__(self, evidence_by_movie):
        self._ev = evidence_by_movie
        self.evaluations = []
    def list_evidence_for_movie(self, movie_href):
        return self._ev.get(movie_href, [])
    def upsert_evaluation(self, rec):
        self.evaluations.append(rec)


def _ev(info_hash, role, *, junk_ratio=0.0, main_ratio=0.95, cat="subtitle",
        name="ABC-123-C", subs=1):
    return {
        "info_hash": info_hash, "target_role": role, "movie_href": "/v/abc",
        "javdb_category": cat, "magnet_name": name,
        "total_size_bytes": 5_000_000_000, "main_video_size_bytes": 4_800_000_000,
        "main_video_ratio": main_ratio, "video_file_count": 1,
        "subtitle_file_count": subs, "non_video_file_count": 0,
        "junk_size_bytes": 0, "junk_size_ratio": junk_ratio,
        "suspicious_file_count": 0, "main_video_name": name + ".mkv",
    }


def test_evaluator_ranks_and_flags_replacement():
    repo = _Repo({"/v/abc": [
        _ev("prod", "production_download", junk_ratio=0.40),   # junk -> low score
        _ev("probeA", "quality_probe", junk_ratio=0.0),         # clean -> high score
    ]})
    summary = evaluate_assist_for_movies(["/v/abc"], repo=repo, policy_mode="assist")
    assert summary["candidates"] == 2
    by_hash = {e.info_hash: e for e in repo.evaluations}
    assert by_hash["prod"].policy_mode == "assist"
    assert by_hash["probeA"].shadow_rank == 1
    assert by_hash["prod"].would_replace_current_choice is True
    assert summary["would_replace"] == 1


def test_evaluator_noop_when_not_assist():
    repo = _Repo({"/v/abc": [_ev("prod", "production_download")]})
    summary = evaluate_assist_for_movies(["/v/abc"], repo=repo, policy_mode="shadow")
    assert summary == {"movies": 0, "candidates": 0, "would_replace": 0}
    assert repo.evaluations == []
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `javdb/quality/assist_evaluator.py`.**
  - `_features_from_evidence(row)` → the features dict `score_torrent` expects
    (map the promoted evidence columns + `main_video_name`).
  - `_context_from_evidence(row)` → `{javdb_category, magnet_name, javdb_tags: []}`.
  - `evaluate_assist_for_movies(movie_hrefs, *, repo, policy_mode, scoring_version=SCORING_VERSION)`:
    no-op (return zeroed summary) unless `policy_mode == 'assist'`; for each movie,
    `repo.list_evidence_for_movie`, score each candidate, group by
    `scored['inferred_category']`, `rank_candidates` per group, then
    `repo.upsert_evaluation(EvaluationRecord(... policy_mode='assist', shadow_rank,
    would_replace_current_choice, decision, score, reasons ...))`. Return
    `{movies, candidates, would_replace}`.
  - A thin `run_assist(*, days, categories, use_proxy)` production wiring mirroring
    `collector.run_collection` (open `REPORTS_DB_PATH`, resolve recent movie hrefs
    from recent evaluations/probe candidates, call `evaluate_assist_for_movies`).

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(quality): assist evaluator ranks probe vs production (ADR-024)`.

---

## Task 5 — API: recommendations + needs-review + review-label write

**Files:** Modify `apps/api/routers/quality.py` + `apps/api/schemas/quality.py`; Test `tests/unit/test_quality_assist_api.py`.

- [ ] **Step 1: Write the failing test** (FastAPI `TestClient`, mirror the existing
  quality API test setup + auth). Cover:
  - `GET /api/quality/recommendations?movie_href=/v/abc` → per category, the current
    (`would_replace_current_choice` false / production) choice vs the recommended
    (`shadow_rank==1`) candidate + the reason-code diff. Returns `{items: [...]}`.
  - `GET /api/quality/needs-review?limit=` → evaluations where
    `decision='needs_review'` OR `would_replace_current_choice` is true. `limit`
    defaults 50, capped 200, `limit<=0` → 400.
  - `POST /api/quality/review-labels` with `{info_hash, movie_href, scoring_version,
    label, note?}` → records a label via `TorrentQualityReviewRepo`, returns 200
    `{status: "recorded"}`; invalid `label` → 422 (schema) ; auth required (401
    without token).

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** Add schemas (`QualityRecommendationSchema`,
  `QualityRecommendationListResponse`, `ReviewLabelRequest`, `ReviewLabelResponse`)
  to `schemas/quality.py`. In `routers/quality.py`: add the three routes under the
  existing `/api/quality` prefix + auth dependency. Build recommendations from
  `repo.list_evaluations_for_movie` grouped by `javdb_category` (current =
  production row, recommended = `shadow_rank==1` row, `reason_diff` = set-diff of
  reason codes). `needs-review` from a new repo query (or filter
  `list_recent_evaluations`). The write endpoint resolves a
  `TorrentQualityReviewRepo` (same conn pattern as `_repo()`), validates `label`
  via the Pydantic enum, stamps `reviewed_at` server-side, and `reviewer` from the
  JWT subject. Mirror the bool/JSON shaping helpers already in the router.

- [ ] **Step 4: Run → PASS. Regenerate the OpenAPI contract if the repo has a
  dump step** (check `scripts/ci` / `apps.cli.ops` for an openapi/contract dump;
  the Web client's `api.gen.ts` is generated from it — note in the completion note
  that the Web round must re-vendor types).

- [ ] **Step 5: Commit** `feat(api): assist recommendations + needs-review + review labels (ADR-024)`.

---

## Task 6 — CLI + config + workflow

**Files:** Create `apps/cli/qb/quality_assist.py`; Modify `config.py.example` + `.github/workflows/QBFileFilter.yml`; Test `tests/unit/test_quality_assist_cli.py`.

- [ ] **Step 1: Failing CLI test** — double-gated like `quality_probe.py`:
  runs the evaluator only when `TORRENT_QUALITY_EVIDENCE_ENABLED` is true AND
  `TORRENT_QUALITY_POLICY_MODE == 'assist'` (or `--force`).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `apps/cli/qb/quality_assist.py`** mirroring
  `apps/cli/qb/quality_probe.py` (repo-root cwd, argparse, gate, `run_assist`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Wire** an assist step into `QBFileFilter.yml` after the probe step
  (gated on `vars.TORRENT_QUALITY_EVIDENCE_ENABLED == 'true' && vars.TORRENT_QUALITY_POLICY_MODE == 'assist'`),
  and document the `assist` activation in `config.py.example`'s quality block.
  Lint the YAML.
- [ ] **Step 6: Commit** `feat(cli,ci): add gated assist evaluator runner (ADR-024)`.

---

## Task 7 — Docs

**Files:** Modify `ADR-024-torrent-quality-evidence.md` + `.zh.md`; this IMP's status; handbook if API surface is user-facing.

- [ ] Update ADR-024 roadmap row for Phase 2 (assist landed; Web UI still a follow-up
  round), add a Status Log entry (bilingual). Mark this IMP `Completed` with a
  completion note. Update the developer API reference
  (`docs/handbook/en/developer/` + paired `zh/`) with the three new `/api/quality`
  endpoints if that reference enumerates endpoints.
- [ ] Commit `docs(adr-024): assist mode (IMP-08) landed`.

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Ranking | `pytest tests/unit/test_quality_assist_ranking.py -v` PASS |
| 2 | Review-label store + parity | `pytest tests/unit/test_torrent_quality_review_repo.py tests/unit/test_rollback_full_fidelity.py -v` PASS |
| 3 | Evidence join | repo test PASS |
| 4 | Evaluator gated + ranks | `pytest tests/unit/test_quality_assist_evaluator.py -v` PASS (no-op unless assist) |
| 5 | API | `pytest tests/unit/test_quality_assist_api.py -v` PASS (auth + limit cap + write) |
| 6 | CLI double-gate | `pytest tests/unit/test_quality_assist_cli.py -v` PASS |
| 7 | No production mutation | `grep -nE "add_torrent|delete_torrents|set_file_priority|uploader" javdb/quality/assist.py javdb/quality/assist_evaluator.py` → no output |
| 8 | Default behaviour unchanged | with `TORRENT_QUALITY_POLICY_MODE=shadow`, evaluator is a no-op; broad regression `pytest tests/unit -k "quality or assist or qb or api or db_migrations or rollback_full" -q --continue-on-collection-errors` PASS (only pre-existing proxy collection errors) |
| 9 | Migration applyable + D1 lesson | new migration has a `Write-Class` header, no comment semicolons, and is mirrored in `_db_migrations.py` |

## Scope reminder for the next round / IMP-09

- **Web review UI** (separate `javdb-autospider-web` round) consumes the
  `/api/quality/recommendations` + `/needs-review` reads and the
  `POST /review-labels` write defined here.
- **IMP-09 (enforce machinery):** rollout gate, offline replay that tunes
  thresholds against the `TorrentQualityReviewLabel` dataset this plan collects,
  backfill/reporting, off-switch — enforce stays gated OFF.
