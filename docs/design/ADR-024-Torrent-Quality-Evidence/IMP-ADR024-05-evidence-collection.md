# IMP-ADR024-05: ADR-024 Phase 1 — Evidence Collection (CLI + Workflow + Config)

**Status:** Completed — implemented 2026-06-01 (design-reviewed & hardened 2026-05-31; see Design Review note).

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the shadow evidence collector that reads the **production-downloaded** torrents' file lists (reusing the IMP-04 shared helpers), extracts features (IMP-03), scores them (IMP-03), and writes `TorrentQualityEvidence` + `TorrentQualityEvaluation` rows (IMP-02). Expose it as a CLI, gate it behind config, and upgrade the existing **QBFileFilter** workflow to run filtering and evidence collection in the same dispatch.

**Architecture:** A new service `javdb/quality/collector.py` orchestrates: connect to the **production** qB (reusing the file-filter login/connection path) → list recent torrents in the production categories → wait for metadata → for each, fetch file list, extract features, score against per-torrent context, UPSERT evidence + evaluation. `target_role` is always `production_download` in this IMP (the remote `quality_probe` endpoint and Top-K runner-ups are deferred — see ADR roadmap). A thin CLI `apps/cli/qb/quality_evidence.py` wraps it. The **QBFileFilter** workflow gains a step that runs the collector after the filter, on the same encrypted config, gated by `TORRENT_QUALITY_EVIDENCE_ENABLED`.

**Tech Stack:** Python 3.11, `requests`, argparse, pytest, GitHub Actions YAML, `config_generator`.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), D4 (`production_download` role), D6/D11 (shadow-only, no production behavior change), D9 (reason codes), "qBittorrent Isolation". Operationalizes the grilled decisions: production-selected-only, reuse file-filter wheels, upgrade the existing filter workflow.

**Related:** [IMP-ADR024-02](IMP-ADR024-02-models-repo.md) · [IMP-ADR024-03](IMP-ADR024-03-feature-extraction-scoring.md) · [IMP-ADR024-04](IMP-ADR024-04-file-filter-modularize.md) · [IMP-ADR024-06](IMP-ADR024-06-read-api.md).

**Depends on:** IMP-01, IMP-02, IMP-03, IMP-04.

**Blocks:** IMP-06 (API reads the rows this writes).

---

## Design Review note (2026-05-31)

A `brainstorming` review against the hardened IMP-02 contract and the codebase
fixed two real defects in the first draft of this plan:

1. **Repo construction matched the hardened IMP-02.** The draft called
   `TorrentQualityRepo()` (no args), which no longer exists — IMP-02 is now
   conn-injected. `run_collection` opens `with get_db(REPORTS_DB_PATH) as conn`
   and constructs `TorrentQualityRepo(conn)`, running the whole collection inside
   that `with` (one connection holds all UPSERTs for the run).
2. **Real movie context now joins from `AcquisitionOutcome` (ADR-033).** The draft
   fed the qB category (`torrent.get("category")`, e.g. "Daily Ingestion") as
   `javdb_category`, which is never a JavDB type key — that silently pinned
   `category_consistent=True` (disabling an ADR headline signal) and polluted the
   column. `AcquisitionOutcome` (written by the uploader, keyed by `qb_hash`)
   already carries `href` / `video_code` / type-`category`, so `_build_context`
   joins it by `qb_hash`. Torrents with no outcome row get `javdb_category=None`,
   never the qB category. A `_build_context` unit test pins this.

The features→`EvidenceRecord` seam was already correct in the draft
(`features={k: feats[k] for k in ("main_video_name",)}` — promoted keys go to
named fields, only the non-promoted hint goes to `.features`, respecting the
IMP-02 non-overlap invariant).

## Completion note (2026-06-01)

Implemented in branch `adr-024-imp-05`. The delivered slice keeps the IMP scope:
`production_download` only, read-only qB access, direct `TorrentQualityRepo`
UPSERTs, CLI/config/workflow gates, and handbook/wiki-source documentation.

During review, the direct CLI/config path was hardened so an empty
`TORRENT_QUALITY_CATEGORIES` value skips collection instead of scanning every
qBittorrent category. `QBFileFilter.yml` resolves evidence categories from the
manual dispatch input, then `TORRENT_QUALITY_CATEGORIES`, then its explicit
default list, so scheduled evidence collection is bounded while direct runs fail
closed unless the caller provides a JSON category array or `--categories`.
Workflow dry-runs skip the collector because evidence collection writes durable
D1 rows.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/quality/collector.py` | Orchestrate collect→extract→score→UPSERT for `production_download`. |
| Create | `apps/cli/qb/quality_evidence.py` | CLI entrypoint (argparse, exit code, enable gate). |
| Create | `tests/unit/test_quality_collector.py` | Collector orchestration tests with fakes. |
| Create | `tests/unit/test_quality_evidence_cli.py` | CLI gate + exit-code tests. |
| Modify | `javdb/infra/config_generator.py` | Add `TORRENT_QUALITY_*` config keys. |
| Modify | `config.py.example` | Document the new config block. |
| Modify | `.github/workflows/QBFileFilter.yml` | Add an evidence-collection step after the filter; pass the new env vars. |

## Scope Boundaries

- `target_role` is `production_download` only. **No** remote probe endpoint, **no**
  adding/removing torrents, **no** Top-K runner-up collection (deferred — ADR roadmap).
- Read-only against qB: the collector never sets file priority, deletes, or
  recategorizes. It only GETs `torrents/info` and `torrents/files`.
- Writes go via `TorrentQualityRepo` direct UPSERT — never the session/pending flow.
- Default `TORRENT_QUALITY_EVIDENCE_ENABLED=False`: a fresh deploy collects nothing.
- `TORRENT_QUALITY_POLICY_MODE` is locked to `shadow` semantics in Phase 1 (the
  collector always writes `policy_mode="shadow"`); `assist`/`enforce` are accepted
  as values but have no behavioral effect yet.

---

## Task 1 — Collector service

**Files:**
- Create: `javdb/quality/collector.py`
- Test: `tests/unit/test_quality_collector.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_quality_collector.py`:

```python
"""Tests for the ADR-024 evidence collector (Phase 1)."""

from __future__ import annotations

from javdb.quality.collector import collect_production_evidence


class _FakeRepo:
    def __init__(self):
        self.evidence = []
        self.evaluations = []

    def upsert_evidence(self, rec):
        self.evidence.append(rec)

    def upsert_evaluation(self, rec):
        self.evaluations.append(rec)


def _torrent(h, name, category):
    return {"hash": h, "name": name, "category": category, "added_on": 1_000}


def test_collects_evidence_and_evaluation_per_torrent():
    repo = _FakeRepo()
    torrents = [_torrent("HASH1", "ABC-123-C", "Daily Ingestion")]
    files_by_hash = {
        "HASH1": [
            {"name": "ABC-123-C.mkv", "size": 5_000_000_000, "priority": 1},
            {"name": "ABC-123-C.srt", "size": 60_000, "priority": 1},
        ]
    }

    summary = collect_production_evidence(
        torrents=torrents,
        fetch_files=lambda h: files_by_hash.get(h),
        repo=repo,
        context_for=lambda t: {
            "movie_href": "/v/abc",
            "video_code": "ABC-123",
            "javdb_category": "subtitle",
            "magnet_name": t["name"],
            "javdb_tags": ["中文字幕"],
        },
    )

    assert summary["evidence_written"] == 1
    assert summary["evaluations_written"] == 1
    assert summary["probe_unavailable"] == 0
    ev = repo.evidence[0]
    assert ev.info_hash == "HASH1"
    assert ev.target_role == "production_download"
    assert ev.main_video_size_bytes == 5_000_000_000
    ev2 = repo.evaluations[0]
    assert ev2.movie_href == "/v/abc"
    assert ev2.policy_mode == "shadow"
    assert ev2.decision == "accepted_shadow"


def test_records_probe_unavailable_when_metadata_missing():
    repo = _FakeRepo()
    torrents = [_torrent("HASH2", "X", "Daily Ingestion")]

    summary = collect_production_evidence(
        torrents=torrents,
        fetch_files=lambda h: None,  # API failure / no metadata
        repo=repo,
        context_for=lambda t: {
            "movie_href": "/v/x",
            "video_code": "X",
            "javdb_category": "no_subtitle",
            "magnet_name": "X",
            "javdb_tags": [],
        },
    )

    assert summary["probe_unavailable"] == 1
    assert summary["evidence_written"] == 1  # evidence row records the failure
    ev = repo.evidence[0]
    assert ev.metadata_status == "probe_unavailable"
    assert "probe_unavailable" in ev.reasons


def test_skips_torrents_without_hash():
    repo = _FakeRepo()
    torrents = [{"name": "no-hash", "category": "Daily Ingestion", "added_on": 1}]
    summary = collect_production_evidence(
        torrents=torrents,
        fetch_files=lambda h: [],
        repo=repo,
        context_for=lambda t: {"movie_href": "", "video_code": "", "javdb_category": "", "magnet_name": "", "javdb_tags": []},
    )
    assert summary["evidence_written"] == 0
    assert summary["skipped"] == 1


def test_build_context_uses_acquisition_outcome_join():
    from javdb.quality.collector import _build_context

    class _Outcome:
        href = "/v/abc"
        video_code = "ABC-123"
        category = "subtitle"  # JavDB type key, not the qB category

    ctx = _build_context(
        {"hash": "H", "name": "ABC-123-C", "category": "Daily Ingestion"}, _Outcome()
    )
    assert ctx["movie_href"] == "/v/abc"
    assert ctx["video_code"] == "ABC-123"
    assert ctx["javdb_category"] == "subtitle"  # the type key, not "Daily Ingestion"


def test_build_context_without_outcome_does_not_use_qb_category():
    from javdb.quality.collector import _build_context

    ctx = _build_context({"hash": "H", "name": "x", "category": "Daily Ingestion"}, None)
    assert ctx["javdb_category"] is None  # never the qB category
    assert ctx["movie_href"] == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_quality_collector.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality.collector`.

- [ ] **Step 3: Implement `collector.py`**

Create `javdb/quality/collector.py`:

```python
"""ADR-024 Phase 1 — production-download evidence collector.

Reads the file lists of torrents already downloaded by the production pipeline,
extracts objective features, computes an explainable shadow score, and UPSERTs
``TorrentQualityEvidence`` + ``TorrentQualityEvaluation`` rows. Shadow-only: it
never mutates qBittorrent and never changes the production download decision.

The orchestration core ``collect_production_evidence`` is pure of I/O wiring —
``fetch_files`` (qB file-list getter), ``repo`` (TorrentQualityRepo-shaped), and
``context_for`` (torrent -> movie context) are injected so it is unit testable.
``run_collection`` is the thin production wiring used by the CLI.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features
from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.quality.scoring import SCORING_VERSION, score_torrent

logger = logging.getLogger(__name__)

PRODUCTION_TARGET_ROLE = "production_download"


def collect_production_evidence(
    *,
    torrents: list[dict],
    fetch_files: Callable[[str], Optional[list]],
    repo: Any,
    context_for: Callable[[dict], dict],
    probe_target_name: str = "production",
) -> dict[str, int]:
    """Collect evidence + evaluation for each production torrent.

    Returns a summary dict of counters. Each torrent yields exactly one evidence
    row (recording ``probe_unavailable`` when its file list is missing) and, when
    features are available, one evaluation row.
    """
    summary = {
        "scanned": 0,
        "skipped": 0,
        "evidence_written": 0,
        "evaluations_written": 0,
        "probe_unavailable": 0,
    }

    for torrent in torrents:
        summary["scanned"] += 1
        info_hash = (torrent.get("hash") or "").strip()
        if not info_hash:
            summary["skipped"] += 1
            continue

        context = context_for(torrent)
        files = fetch_files(info_hash)

        if not files:  # None (API failure) or [] (metadata not ready)
            evidence = EvidenceRecord(
                info_hash=info_hash,
                probe_schema_version=PROBE_SCHEMA_VERSION,
                target_role=PRODUCTION_TARGET_ROLE,
                probe_target_name=probe_target_name,
                metadata_status="probe_unavailable",
                reasons=["probe_unavailable"],
            )
            repo.upsert_evidence(evidence)
            summary["evidence_written"] += 1
            summary["probe_unavailable"] += 1
            continue

        feats = extract_file_features(files)
        evidence = EvidenceRecord(
            info_hash=info_hash,
            probe_schema_version=PROBE_SCHEMA_VERSION,
            target_role=PRODUCTION_TARGET_ROLE,
            probe_target_name=probe_target_name,
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
            features={k: feats[k] for k in ("main_video_name",)},
        )
        repo.upsert_evidence(evidence)
        summary["evidence_written"] += 1

        scored = score_torrent(feats, context)
        evaluation = EvaluationRecord(
            info_hash=info_hash,
            movie_href=context.get("movie_href", ""),
            scoring_version=SCORING_VERSION,
            video_code=context.get("video_code"),
            javdb_category=context.get("javdb_category"),
            magnet_name=context.get("magnet_name"),
            javdb_tags=context.get("javdb_tags", []),
            javdb_size_text=context.get("javdb_size_text"),
            inferred_category=scored["inferred_category"],
            category_consistent=scored["category_consistent"],
            subtitle_evidence=scored["subtitle_evidence"],
            resolution_consistent=scored["resolution_consistent"],
            score=scored["score"],
            shadow_rank=None,  # Top-K ranking deferred
            would_replace_current_choice=False,  # production-selected-only in Phase 1
            policy_mode="shadow",
            decision=scored["decision"],
            reasons=scored["reasons"],
        )
        repo.upsert_evaluation(evaluation)
        summary["evaluations_written"] += 1

    logger.info(
        "Quality evidence: scanned=%d evidence=%d evaluations=%d probe_unavailable=%d skipped=%d",
        summary["scanned"], summary["evidence_written"], summary["evaluations_written"],
        summary["probe_unavailable"], summary["skipped"],
    )
    return summary


def _build_context(torrent: dict, outcome: Any) -> dict:
    """Movie context for scoring, joined from the ADR-033 AcquisitionOutcome row.

    The production qB torrent does not itself carry the JavDB
    ``href``/``video_code``/type-category, but ``AcquisitionOutcome`` (written by
    the uploader, keyed by ``qb_hash``) does. When a row exists we use its real
    ``href`` / ``video_code`` / ``category`` (the JavDB *type* key:
    subtitle/no_subtitle/hacked_*), which activates the category-consistency
    signal. When no row exists we leave ``javdb_category=None`` and
    ``movie_href=""`` — we must NOT fall back to the qB category (e.g.
    "Daily Ingestion"), which is not a type key and would both pollute the column
    and silently disable the consistency check.
    """
    if outcome is not None:
        return {
            "movie_href": getattr(outcome, "href", "") or "",
            "video_code": getattr(outcome, "video_code", None),
            "javdb_category": getattr(outcome, "category", None),
            "magnet_name": torrent.get("name"),
            "javdb_tags": [],
            "javdb_size_text": None,
        }
    return {
        "movie_href": "",
        "video_code": None,
        "javdb_category": None,
        "magnet_name": torrent.get("name"),
        "javdb_tags": [],
        "javdb_size_text": None,
    }


def run_collection(
    *,
    days: int = 2,
    categories: Optional[list[str]] = None,
    use_proxy=None,
) -> dict[str, int]:
    """Production wiring: connect to qB, gather torrents, collect evidence.

    Reuses the file-filter connection/login path and the shared readonly helpers,
    joins real movie context from ``AcquisitionOutcome`` (ADR-033) by ``qb_hash``,
    and writes through a conn-injected ``TorrentQualityRepo`` (one ``reports``
    connection holds every UPSERT for the run). Returns the collector summary dict.
    """
    import requests

    from javdb.integrations.qb import readonly
    from javdb.integrations.qb.file_filter import service as ff
    from javdb.storage.db import OPERATIONS_DB_PATH, REPORTS_DB_PATH, get_db
    from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
    from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

    ff.initialize_proxy_helper(use_proxy)
    if not ff.test_qbittorrent_connection(use_proxy):
        raise RuntimeError("Cannot connect to qBittorrent")

    session = requests.Session()
    try:
        if not ff.login_to_qbittorrent(session, use_proxy):
            raise RuntimeError("Failed to login to qBittorrent")

        torrents = ff.get_recent_torrents(
            session, days=days, categories=categories, use_proxy=use_proxy
        )
        if not torrents:
            return {
                "scanned": 0, "skipped": 0, "evidence_written": 0,
                "evaluations_written": 0, "probe_unavailable": 0,
            }

        # Let qB fetch metadata for freshly added torrents before reading files.
        readonly.wait_for_metadata_readiness(
            torrents,
            fetch_files=lambda h: ff.get_torrent_files(session, h, use_proxy),
        )

        # Join real movie context (href / video_code / type-category) by qb_hash.
        outcomes: dict[str, Any] = {}
        with get_db(OPERATIONS_DB_PATH) as ops_conn:
            acq_repo = AcquisitionOutcomeRepo(ops_conn)
            for torrent in torrents:
                h = (torrent.get("hash") or "").strip()
                if not h:
                    continue
                rec = acq_repo.get(h)
                if rec is not None:
                    outcomes[h] = rec

        with get_db(REPORTS_DB_PATH) as conn:
            repo = TorrentQualityRepo(conn)
            return collect_production_evidence(
                torrents=torrents,
                fetch_files=lambda h: ff.get_torrent_files(session, h, use_proxy),
                repo=repo,
                context_for=lambda t: _build_context(
                    t, outcomes.get((t.get("hash") or "").strip())
                ),
            )
    finally:
        session.close()
```

> **Note for the implementer (movie context):** Phase 1 joins real movie context
> from `AcquisitionOutcome` (ADR-033) by `qb_hash` — `href`, `video_code`, and the
> JavDB type-`category` — so the category-consistency signal is live. Torrents
> with no `AcquisitionOutcome` row (e.g. manually added) get `javdb_category=None`
> and `movie_href=""`, never the qB category. Top-K runner-up collection and the
> remote `quality_probe` role remain deferred (ADR-024 roadmap). An empty
> `movie_href` is acceptable; the PK `(info_hash, movie_href, scoring_version)`
> still holds.

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_quality_collector.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/quality/collector.py tests/unit/test_quality_collector.py
git commit -m "feat(quality): add production-download evidence collector (ADR-024)"
```

---

## Task 2 — CLI entrypoint

**Files:**
- Create: `apps/cli/qb/quality_evidence.py`
- Test: `tests/unit/test_quality_evidence_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_quality_evidence_cli.py`:

```python
"""CLI tests for ADR-024 quality evidence collector."""

from __future__ import annotations

from unittest.mock import patch

from apps.cli.qb import quality_evidence as cli


def test_disabled_by_default_exits_zero_without_running(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    with patch.object(cli, "run_collection") as run:
        rc = cli.main(["--days", "2"])
    assert rc == 0
    run.assert_not_called()


def test_enabled_runs_collection(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    with patch.object(cli, "run_collection", return_value={
        "scanned": 1, "skipped": 0, "evidence_written": 1,
        "evaluations_written": 1, "probe_unavailable": 0,
    }) as run:
        rc = cli.main(["--days", "2", "--categories", '["Daily Ingestion"]'])
    assert rc == 0
    run.assert_called_once()


def test_force_overrides_disabled_gate(monkeypatch):
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: False)
    with patch.object(cli, "run_collection", return_value={
        "scanned": 0, "skipped": 0, "evidence_written": 0,
        "evaluations_written": 0, "probe_unavailable": 0,
    }) as run:
        rc = cli.main(["--force"])
    assert rc == 0
    run.assert_called_once()


def test_categories_fall_back_to_config(monkeypatch):
    # --categories omitted → use TORRENT_QUALITY_CATEGORIES from config.
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "cfg", lambda key, default=None: (
        '["Daily Ingestion"]' if key == "TORRENT_QUALITY_CATEGORIES" else default
    ))
    with patch.object(cli, "run_collection", return_value={
        "scanned": 0, "skipped": 0, "evidence_written": 0,
        "evaluations_written": 0, "probe_unavailable": 0,
    }) as run:
        rc = cli.main([])
    assert rc == 0
    assert run.call_args.kwargs["categories"] == ["Daily Ingestion"]


def test_cli_categories_override_config(monkeypatch):
    # --categories wins over the config key.
    monkeypatch.setattr(cli, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(cli, "cfg", lambda key, default=None: (
        '["Daily Ingestion"]' if key == "TORRENT_QUALITY_CATEGORIES" else default
    ))
    with patch.object(cli, "run_collection", return_value={
        "scanned": 0, "skipped": 0, "evidence_written": 0,
        "evaluations_written": 0, "probe_unavailable": 0,
    }) as run:
        rc = cli.main(["--categories", '["Ad Hoc"]'])
    assert rc == 0
    assert run.call_args.kwargs["categories"] == ["Ad Hoc"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_quality_evidence_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError: apps.cli.qb.quality_evidence`.

- [ ] **Step 3: Implement the CLI**

Create `apps/cli/qb/quality_evidence.py` (mirrors the `apps/cli/qb/file_filter.py`
adapter pattern: repo-root cwd before importing the service, argparse, exit code):

```python
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)

from javdb.infra.config import cfg
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override
from javdb.quality.collector import run_collection


def _evidence_enabled() -> bool:
    return bool(cfg("TORRENT_QUALITY_EVIDENCE_ENABLED", False))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect shadow torrent-quality evidence (ADR-024, read-only)"
    )
    parser.add_argument("--days", type=int, default=2, help="Days to look back for production torrents")
    parser.add_argument("--categories", type=str, default=None, help='JSON array of qB categories (e.g. ["Daily Ingestion"])')
    parser.add_argument("--force", action="store_true", help="Run even when TORRENT_QUALITY_EVIDENCE_ENABLED is False")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qBittorrent API requests",
        no_help="Force-disable proxy for qBittorrent API requests",
    )
    return parser.parse_args(argv)


def _parse_categories(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    categories = json.loads(raw)
    if not isinstance(categories, list):
        raise argparse.ArgumentTypeError("--categories must be a JSON array")
    return [str(c) for c in categories if c]


def _resolve_categories(cli_categories: str | None) -> list[str] | None:
    """CLI --categories wins; otherwise fall back to the config key.

    Honours the documented TORRENT_QUALITY_CATEGORIES config knob for direct CLI
    runs. An empty/whitespace config value returns None; run_collection then
    fails closed and skips rather than scanning every qBittorrent category.
    """
    if cli_categories is not None:
        return _parse_categories(cli_categories)
    configured = (cfg("TORRENT_QUALITY_CATEGORIES", "") or "").strip()
    if not configured:
        return None
    return _parse_categories(configured)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except (json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        raise SystemExit(str(exc)) from exc

    if not args.force and not _evidence_enabled():
        print("Torrent quality evidence disabled (TORRENT_QUALITY_EVIDENCE_ENABLED=False); skipping.")
        return 0

    try:
        categories = _resolve_categories(args.categories)
    except (json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        raise SystemExit(str(exc)) from exc

    summary = run_collection(
        days=args.days,
        categories=categories,
        use_proxy=resolve_proxy_override(args.use_proxy, args.no_proxy),
    )
    print(
        "Quality evidence summary: "
        f"scanned={summary['scanned']} evidence={summary['evidence_written']} "
        f"evaluations={summary['evaluations_written']} "
        f"probe_unavailable={summary['probe_unavailable']} skipped={summary['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_quality_evidence_cli.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/cli/qb/quality_evidence.py tests/unit/test_quality_evidence_cli.py
git commit -m "feat(cli): add torrent quality evidence CLI (ADR-024)"
```

---

## Task 3 — Config keys

**Files:**
- Modify: `javdb/infra/config_generator.py`
- Modify: `config.py.example`

- [ ] **Step 1: Add tuples to `get_config_map`**

In `javdb/infra/config_generator.py`, inside the list returned by `get_config_map`
(after the qBittorrent File Filter tuples, near line 399), add:

```python
        # Torrent quality evidence (ADR-024 Phase 1 — shadow only)
        ('TORRENT_QUALITY_EVIDENCE_ENABLED', 'TORRENT_QUALITY_EVIDENCE_ENABLED', get_env_bool, False, 'TORRENT QUALITY EVIDENCE'),
        ('TORRENT_QUALITY_POLICY_MODE', 'TORRENT_QUALITY_POLICY_MODE', get_env, 'shadow', 'TORRENT QUALITY EVIDENCE'),
        ('TORRENT_QUALITY_CATEGORIES', 'TORRENT_QUALITY_CATEGORIES', get_env, '', 'TORRENT QUALITY EVIDENCE'),
```

- [ ] **Step 2: Add the block to `config.py.example`**

In `config.py.example`, after the "qBittorrent File Filter Configuration" block
(after line ~442), add:

```python

# =============================================================================
# TORRENT QUALITY EVIDENCE (ADR-024 Phase 1 — shadow only)
# =============================================================================

# Phase 1 is read-only and shadow-only. When disabled, the collector exits
# without touching qBittorrent or the database. policy_mode is locked to
# 'shadow' in Phase 1; 'assist'/'enforce' are reserved for later phases.
TORRENT_QUALITY_EVIDENCE_ENABLED = False
TORRENT_QUALITY_POLICY_MODE = 'shadow'
# Optional JSON array of qB categories to scan.
# Empty means no category filter is configured; direct collection skips instead
# of scanning every qBittorrent category.
TORRENT_QUALITY_CATEGORIES = ''
```

- [ ] **Step 3: Verify config generation includes the keys**

Run:

```bash
python3 -c "
from javdb.infra.config_generator import get_config_map
names = {t[0] for t in get_config_map()}
assert {'TORRENT_QUALITY_EVIDENCE_ENABLED','TORRENT_QUALITY_POLICY_MODE','TORRENT_QUALITY_CATEGORIES'} <= names
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add javdb/infra/config_generator.py config.py.example
git commit -m "feat(config): add torrent quality evidence keys (ADR-024)"
```

---

## Task 4 — Upgrade the QBFileFilter workflow

**Files:**
- Modify: `.github/workflows/QBFileFilter.yml`

The existing `run-file-filter` job already: checks out, sets up Python, restores the
encrypted config, and runs `python3 -m apps.cli.qb.file_filter`. Add a second step
in that **same job** that runs the evidence collector against the same config, so a
single dispatch runs both tasks (per the grilled decision). Gate it on the new env
var so it is a no-op until enabled.

- [ ] **Step 1: Add the evidence-collection step to `run-file-filter`**

In `.github/workflows/QBFileFilter.yml`, in the `run-file-filter` job, immediately
after the existing `- name: Run qBittorrent File Filter` step (ends at line ~340),
add:

```yaml
      - name: Collect torrent quality evidence (ADR-024, shadow)
        if: ${{ vars.TORRENT_QUALITY_EVIDENCE_ENABLED == 'true' && github.event.inputs.dry_run != 'true' }}
        env:
          DAYS: ${{ github.event.inputs.days || '2' }}
          QB_EVIDENCE_CATEGORIES: ${{ github.event.inputs.categories || vars.TORRENT_QUALITY_CATEGORIES || '["Ad Hoc", "Daily Ingestion", "顶级"]' }}
        run: |
          set -e
          set -o pipefail
          ARGS=(--days "$DAYS")
          if [ -n "$QB_EVIDENCE_CATEGORIES" ]; then
            ARGS+=(--categories "$QB_EVIDENCE_CATEGORIES")
          fi
          echo "Running: python3 -m apps.cli.qb.quality_evidence ${ARGS[*]}"
          python3 -m apps.cli.qb.quality_evidence "${ARGS[@]}"
```

- [ ] **Step 2: Generate the config key in the setup job**

The collector reads `TORRENT_QUALITY_EVIDENCE_ENABLED` from the generated
`config.py`. In the `setup` job's `- name: Generate config.py ...` step `env:` block
(and the adhoc one if you want adhoc coverage), add these lines alongside the other
`VAR_*` entries (e.g. after `VAR_QB_FILE_FILTER_LOG_FILE`, line ~131):

```yaml
          VAR_TORRENT_QUALITY_EVIDENCE_ENABLED: ${{ vars.TORRENT_QUALITY_EVIDENCE_ENABLED || 'false' }}
          VAR_TORRENT_QUALITY_POLICY_MODE: ${{ vars.TORRENT_QUALITY_POLICY_MODE || 'shadow' }}
          VAR_TORRENT_QUALITY_CATEGORIES: ${{ vars.TORRENT_QUALITY_CATEGORIES || '' }}
```

> The `if:` on the collector step also reads the GitHub Variable directly, so the
> step is skipped at the job level when the variable is unset/`false` even before
> the gate inside the CLI fires — defense in depth.

- [ ] **Step 3: Lint the workflow YAML**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/QBFileFilter.yml')); print('yaml ok')"
```

Expected: `yaml ok`.

- [ ] **Step 4: Guard test for workflow wiring (optional but recommended)**

If the repo has a workflow-guard test pattern (e.g.
`tests/unit/test_workflow_resolve_write_mode.py`), add an assertion that
`QBFileFilter.yml` contains the `apps.cli.qb.quality_evidence` invocation. Otherwise,
record the manual check in the task summary.

```bash
rg -n "apps.cli.qb.quality_evidence" .github/workflows/QBFileFilter.yml
```

Expected: one match in the `run-file-filter` job.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/QBFileFilter.yml
git commit -m "ci(qb): run torrent quality evidence after file filter (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Collector orchestration | `pytest tests/unit/test_quality_collector.py -v` → PASS |
| 2 | CLI gate works | `pytest tests/unit/test_quality_evidence_cli.py -v` → PASS |
| 3 | Disabled by default | `run_collection` is not called when `TORRENT_QUALITY_EVIDENCE_ENABLED=False` and no `--force` |
| 4 | Config keys generate | Task 3 Step 3 prints `ok` |
| 5 | Workflow valid + wired | `yaml ok` + `rg` finds the collector invocation |
| 6 | No production mutation | `rg -n "set_file_priority\|delete_torrents\|add_torrent" javdb/quality/collector.py` → no output |
