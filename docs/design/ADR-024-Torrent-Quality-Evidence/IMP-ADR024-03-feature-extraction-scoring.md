# IMP-ADR024-03: ADR-024 Phase 1 — Feature Extraction & Pure Scoring

**Status:** Completed — implemented 2026-05-31.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two pure, well-tested modules — `javdb/quality/features.py` (turn a qBittorrent file list into objective evidence features) and `javdb/quality/scoring.py` (turn features + movie context into an explainable shadow score with structured reason codes) — each carrying a version constant.

**Architecture:** Both modules are **pure functions** with no I/O, mirroring the ADR-023 `recommend_policy.ts` pattern (deterministic, version-stamped, unit-testable). `features.py` exposes `PROBE_SCHEMA_VERSION` + `extract_file_features(files)`. `scoring.py` exposes `SCORING_VERSION` + `score_torrent(features, context)`. Per ADR-024 D9 the score is **decomposed** into signals and reason codes, never an opaque number.

**Tech Stack:** Python 3.11, `dataclasses`, pytest. No network, no DB.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), D9 (explainability), "Scoring Signals", D3 (`probe_schema_version` / `scoring_version`). D10 explicitly defers video-content inspection — these modules use file-list metadata only.

**Related:** [IMP-ADR024-02](IMP-ADR024-02-models-repo.md) (records consume these outputs) · [IMP-ADR024-05](IMP-ADR024-05-evidence-collection.md) (collector calls these).

**Depends on:** Nothing (pure modules). Can land in parallel with IMP-01/02.

**Blocks:** IMP-ADR024-05 (collector wires features→evidence and score→evaluation).

---

## Design Review note (2026-05-31)

A `brainstorming` review checked this plan against the hardened IMP-02 model
contract and the codebase; the embedded `features.py` + `scoring.py` were
extracted and run (**10/10 tests pass**). Outcomes:

- **Category vocabulary is correct.** `scoring.py`'s `{subtitle, no_subtitle,
  hacked_subtitle, hacked_no_subtitle}` matches the codebase
  (`javdb/spider/services/dedup.py`, `javdb/pipeline/policies.py`).
- **Decision — evidence measures the torrent *as published*.** `features.py`
  intentionally ignores qB `priority`/`progress` and counts ALL files. Rationale:
  ADR-024's goal is candidate-quality evidence, and the `QBFileFilter` job zeroes
  junk-file priority on production torrents *before* collection — counting only
  `priority>0` would hide the "this torrent is junky" signal. The `quality_probe`
  role is metadata-only (all default priority) anyway.
- **Integration seam (for IMP-05).** `extract_file_features` returns a FLAT dict
  whose nine numeric keys (`total_size_bytes` … `suspicious_file_count`) map to
  `EvidenceRecord`'s promoted named fields, plus `main_video_name` — an audit /
  scoring-hint breadcrumb that is NOT a column. IMP-05 must map the promoted keys
  to `EvidenceRecord` fields and route `main_video_name` into
  `EvidenceRecord.features`; it must NOT dump the whole dict into `.features`
  (the IMP-02 non-overlap invariant would raise `ValueError`).
- **`context["javdb_category"]` contract.** IMP-05 must pass one of the four type
  keys above — never the qB category (`'JavDB'`) nor the `'中字'/'无字'` subtitle
  axis.
- **Phase-1 non-signals (intentional):** `javdb_tags` is stored on
  `EvaluationRecord` but not consumed by `score_torrent`; scoring thresholds are
  v1 guesses, version-stamped via `SCORING_VERSION` and shadow-only, so they are
  tunable without a contract change.

## Implementation Review note (2026-05-31)

Implementation added two review-driven hardenings beyond the initial embedded
draft:

1. **Missing-video rejection is isolated.** `main_video_missing` now applies a
   strong enough penalty for a non-junk, non-video file list to produce
   `rejected_shadow`; the unit test uses `readme.md` so it does not rely on the
   junk-file penalty.
2. **Subtitle name hints are explainable and token-aware.** Name-derived subtitle
   evidence emits `subtitle_name_hint`; ASCII hints match tokens instead of broad
   substrings, so names like `ABC-123 subject` do not suppress
   `category_mismatch`. Short suffix hints (`c`, `uc`, `cu`) only count as the
   final token, preserving names such as `ABC-123-C`.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/quality/features.py` | File-list feature extraction + `PROBE_SCHEMA_VERSION`. |
| Create | `javdb/quality/scoring.py` | Explainable shadow scoring + reason codes + `SCORING_VERSION`. |
| Create | `tests/unit/test_quality_features.py` | Feature-extraction unit tests. |
| Create | `tests/unit/test_quality_scoring.py` | Scoring + reason-code unit tests. |
| Modify | `javdb/quality/__init__.py` | Export the new symbols. |

## Scope Boundaries

- No I/O: these modules never call qBittorrent, the DB, or the network.
- Evidence is **as-published**: `features.py` counts all files and ignores qB `priority`/`progress` (intentional — see Design Review note).
- No video-content inspection (D10): frame/OCR/watermark detection is out of scope.
- Do not change production category semantics — scoring is shadow-only.
- `resolution_claim_unsupported` is a thin best-effort signal here; deep resolution
  verification is deferred. Keep the reason set grounded in file-list facts.

---

## Task 1 — File-list feature extraction

**Files:**
- Create: `javdb/quality/features.py`
- Test: `tests/unit/test_quality_features.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_quality_features.py`:

```python
"""Tests for ADR-024 file-list feature extraction (Phase 1)."""

from __future__ import annotations

from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features


def test_version_constant_is_stable():
    assert PROBE_SCHEMA_VERSION == "adr024-probe-v1"


def test_single_clean_video():
    files = [{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}]
    f = extract_file_features(files)
    assert f["total_size_bytes"] == 4_000_000_000
    assert f["main_video_size_bytes"] == 4_000_000_000
    assert f["main_video_name"] == "ABC-123.mp4"
    assert f["main_video_ratio"] == 1.0
    assert f["video_file_count"] == 1
    assert f["subtitle_file_count"] == 0
    assert f["non_video_file_count"] == 0
    assert f["junk_size_bytes"] == 0
    assert f["junk_size_ratio"] == 0.0
    assert f["suspicious_file_count"] == 0


def test_video_with_subtitle_and_junk():
    files = [
        {"name": "ABC-123/ABC-123.mkv", "size": 5_000_000_000, "priority": 1},
        {"name": "ABC-123/ABC-123.srt", "size": 50_000, "priority": 1},
        {"name": "ABC-123/读我.txt", "size": 1_000, "priority": 1},
        {"name": "ABC-123/广告.jpg", "size": 200_000, "priority": 1},
    ]
    f = extract_file_features(files)
    assert f["video_file_count"] == 1
    assert f["subtitle_file_count"] == 1
    assert f["non_video_file_count"] == 3  # srt + txt + jpg are non-video
    assert f["junk_size_bytes"] == 201_000  # txt + jpg
    assert f["main_video_size_bytes"] == 5_000_000_000
    assert f["main_video_name"] == "ABC-123.mkv"
    assert 0.0 < f["junk_size_ratio"] < 0.001
    assert f["suspicious_file_count"] == 2


def test_inflated_torrent_with_ad_archive():
    files = [
        {"name": "movie.mp4", "size": 1_000_000_000, "priority": 1},
        {"name": "【最新地址】.txt", "size": 2_000, "priority": 1},
        {"name": "bonus.rar", "size": 3_000_000_000, "priority": 1},
    ]
    f = extract_file_features(files)
    # rar archive + txt are junk; the .rar dominates size
    assert f["junk_size_bytes"] == 3_000_002_000
    assert f["junk_size_ratio"] > 0.7
    assert f["main_video_size_bytes"] == 1_000_000_000
    assert f["main_video_name"] == "movie.mp4"
    assert f["main_video_ratio"] < 0.3


def test_empty_file_list():
    f = extract_file_features([])
    assert f["total_size_bytes"] == 0
    assert f["main_video_ratio"] == 0.0
    assert f["video_file_count"] == 0
    assert f["main_video_name"] == ""
```

- [x] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_quality_features.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality.features`.

- [x] **Step 3: Implement `features.py`**

Create `javdb/quality/features.py`:

```python
"""ADR-024 Phase 1 — file-list feature extraction (pure, no I/O).

Turns a qBittorrent file list (``[{"name", "size", "priority", ...}, ...]``)
into objective evidence features. D10 forbids video-content inspection, so this
uses file names, extensions, and sizes only. The ``PROBE_SCHEMA_VERSION``
constant versions the feature shape so stored evidence stays interpretable as
extraction logic evolves.

``priority`` / ``progress`` are intentionally ignored: evidence describes the
torrent **as published** (all files), not what is currently selected on disk.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

PROBE_SCHEMA_VERSION = "adr024-probe-v1"

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".ts", ".m2ts",
        ".mpg", ".mpeg", ".m4v", ".rmvb", ".rm", ".vob", ".webm", ".iso",
    }
)
SUBTITLE_EXTENSIONS = frozenset(
    {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".smi"}
)
# Non-primary content that inflates total torrent size.
JUNK_EXTENSIONS = frozenset(
    {
        ".txt", ".nfo", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
        ".url", ".html", ".htm", ".lnk", ".db", ".exe", ".zip", ".rar",
        ".7z", ".torrent",
    }
)
# Advertising / sample markers in file names (case-insensitive).
JUNK_NAME_MARKERS = (
    "sample", "样片", "预览", "广告", "最新地址", "更多", "扫码",
    "防屏蔽", "发布组", "宣传", "trailer",
)


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _basename(name: str) -> str:
    return os.path.basename(name)


def _is_junk(name: str, ext: str) -> bool:
    if ext in JUNK_EXTENSIONS:
        return True
    lowered = _basename(name).lower()
    return any(marker in lowered for marker in JUNK_NAME_MARKERS)


def extract_file_features(files: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute objective evidence features from a qB file list.

    Returns a dict with the numeric columns expected by
    ``EvidenceRecord`` plus a ``main_video_name`` hint for scoring.
    """
    total = 0
    main_video_size = 0
    main_video_name = ""
    video_count = 0
    subtitle_count = 0
    non_video_count = 0
    junk_bytes = 0
    suspicious = 0

    for f in files:
        name = str(f.get("name", ""))
        try:
            size = int(f.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        total += size
        ext = _ext(name)

        if ext in VIDEO_EXTENSIONS:
            video_count += 1
            if size > main_video_size:
                main_video_size = size
                main_video_name = _basename(name)
        else:
            non_video_count += 1
            if ext in SUBTITLE_EXTENSIONS:
                subtitle_count += 1

        if _is_junk(name, ext):
            junk_bytes += size
            suspicious += 1

    main_video_ratio = (main_video_size / total) if total > 0 else 0.0
    junk_size_ratio = (junk_bytes / total) if total > 0 else 0.0

    return {
        "total_size_bytes": total,
        "main_video_size_bytes": main_video_size,
        "main_video_ratio": main_video_ratio,
        "main_video_name": main_video_name,
        "video_file_count": video_count,
        "subtitle_file_count": subtitle_count,
        "non_video_file_count": non_video_count,
        "junk_size_bytes": junk_bytes,
        "junk_size_ratio": junk_size_ratio,
        "suspicious_file_count": suspicious,
    }
```

- [x] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_quality_features.py -v
```

Expected: PASS (7 tests).

- [x] **Step 5: Commit**

```bash
git add javdb/quality/features.py tests/unit/test_quality_features.py
git commit -m "feat(quality): add file-list feature extraction (ADR-024)"
```

---

## Task 2 — Explainable shadow scoring

**Files:**
- Create: `javdb/quality/scoring.py`
- Test: `tests/unit/test_quality_scoring.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_quality_scoring.py`:

```python
"""Tests for ADR-024 explainable shadow scoring (Phase 1)."""

from __future__ import annotations

from javdb.quality.features import extract_file_features
from javdb.quality.scoring import SCORING_VERSION, score_torrent


def _features(files):
    return extract_file_features(files)


def test_version_constant_is_stable():
    assert SCORING_VERSION == "adr024-shadow-v1"


def test_clean_subtitled_video_scores_high():
    feats = _features(
        [
            {"name": "ABC-123-C.mkv", "size": 5_000_000_000, "priority": 1},
            {"name": "ABC-123-C.srt", "size": 60_000, "priority": 1},
        ]
    )
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123-C 中文字幕", "javdb_tags": ["中文字幕"]},
    )
    assert result["score"] >= 0.8
    assert "main_video_detected" in result["reasons"]
    assert "subtitle_file_present" in result["reasons"]
    assert result["category_consistent"] is True
    assert result["subtitle_evidence"] == "file_present"
    assert result["decision"] == "accepted_shadow"


def test_subtitle_category_without_subtitle_is_flagged():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123", "javdb_tags": []},
    )
    assert "subtitle_file_missing" in result["reasons"]
    assert "category_mismatch" in result["reasons"]
    assert result["category_consistent"] is False
    assert result["subtitle_evidence"] == "absent"
    assert result["decision"] == "needs_review"


def test_subtitle_category_with_name_hint_only_is_accepted():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123-C", "javdb_tags": []},
    )
    assert "subtitle_name_hint" in result["reasons"]
    assert result["subtitle_evidence"] == "name_hint"
    assert result["category_consistent"] is True
    assert result["decision"] == "accepted_shadow"


def test_ascii_name_hint_does_not_match_subject_substring():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123 subject", "javdb_tags": []},
    )
    assert "subtitle_name_hint" not in result["reasons"]
    assert "subtitle_file_missing" in result["reasons"]
    assert "category_mismatch" in result["reasons"]
    assert result["subtitle_evidence"] == "absent"
    assert result["category_consistent"] is False
    assert result["decision"] == "needs_review"


def test_inflated_junk_torrent_scores_low():
    feats = _features(
        [
            {"name": "movie.mp4", "size": 800_000_000, "priority": 1},
            {"name": "bonus.rar", "size": 4_000_000_000, "priority": 1},
            {"name": "广告.txt", "size": 5_000, "priority": 1},
        ]
    )
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "movie", "javdb_tags": []},
    )
    assert "junk_ratio_high" in result["reasons"]
    assert result["score"] < 0.4
    assert result["decision"] == "rejected_shadow"


def test_no_video_file_is_rejected():
    feats = _features([{"name": "readme.md", "size": 1_000, "priority": 1}])
    result = score_torrent(feats, {"javdb_category": "no_subtitle", "magnet_name": "x", "javdb_tags": []})
    assert "main_video_missing" in result["reasons"]
    assert result["score"] < 0.4
    assert result["decision"] == "rejected_shadow"
```

- [x] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_quality_scoring.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality.scoring`.

- [x] **Step 3: Implement `scoring.py`**

Create `javdb/quality/scoring.py`:

```python
"""ADR-024 Phase 1 — explainable shadow scoring (pure, no I/O).

Turns the objective features from ``features.extract_file_features`` plus movie
context into a decomposed score and structured reason codes. Per ADR-024 D9 the
score is never opaque: every adjustment maps to a reason code. ``SCORING_VERSION``
versions the rule set so stored evaluations remain interpretable. Phase 1 output
is shadow-only — it never changes the production download decision.
"""

from __future__ import annotations

import re
from typing import Any

SCORING_VERSION = "adr024-shadow-v1"

# Tunable thresholds (kept here, version-stamped via SCORING_VERSION).
JUNK_RATIO_HIGH = 0.15
MAIN_VIDEO_RATIO_FLOOR = 0.5
ACCEPT_SCORE = 0.6
REJECT_SCORE = 0.4

# javdb_category values that claim embedded/sidecar subtitles.
_SUBTITLE_CATEGORIES = frozenset({"subtitle", "hacked_subtitle"})
_CJK_SUBTITLE_NAME_MARKERS = ("字幕", "中文", "中字")
_ASCII_SUBTITLE_TOKENS = frozenset(
    {"sub", "subs", "subbed", "chinese", "chs", "cht", "zh", "cn"}
)
_ASCII_FINAL_SUBTITLE_TOKENS = frozenset({"c", "uc", "cu"})


def _subtitle_name_hint(magnet_name: str) -> bool:
    lowered = (magnet_name or "").lower()
    if any(marker in lowered for marker in _CJK_SUBTITLE_NAME_MARKERS):
        return True

    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if any(token in _ASCII_SUBTITLE_TOKENS for token in tokens):
        return True
    return bool(tokens and tokens[-1] in _ASCII_FINAL_SUBTITLE_TOKENS)


def score_torrent(
    features: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Return a decomposed shadow score + reason codes + decision.

    ``features`` is the output of ``extract_file_features``. ``context`` carries
    movie-level claims: ``javdb_category``, ``magnet_name``, ``javdb_tags``.
    """
    reasons: list[str] = []
    score = 1.0

    javdb_category = context.get("javdb_category") or ""
    magnet_name = context.get("magnet_name") or ""

    # --- main video presence ---
    if features.get("video_file_count", 0) > 0:
        reasons.append("main_video_detected")
    else:
        reasons.append("main_video_missing")
        score -= 0.7

    # --- effective main-video size, not raw total ---
    main_ratio = float(features.get("main_video_ratio", 0.0))
    if features.get("video_file_count", 0) > 0 and main_ratio < MAIN_VIDEO_RATIO_FLOOR:
        reasons.append("main_video_ratio_low")
        score -= 0.25

    # --- junk / ad penalty ---
    junk_ratio = float(features.get("junk_size_ratio", 0.0))
    if junk_ratio >= JUNK_RATIO_HIGH:
        reasons.append("junk_ratio_high")
        score -= min(0.4, junk_ratio)

    # --- subtitle evidence ---
    if features.get("subtitle_file_count", 0) > 0:
        subtitle_evidence = "file_present"
        reasons.append("subtitle_file_present")
    elif _subtitle_name_hint(magnet_name):
        subtitle_evidence = "name_hint"  # weak signal per ADR Scoring Signals
        reasons.append("subtitle_name_hint")
    else:
        subtitle_evidence = "absent"

    # --- category consistency ---
    category_consistent = True
    if javdb_category in _SUBTITLE_CATEGORIES and subtitle_evidence == "absent":
        category_consistent = False
        reasons.append("subtitle_file_missing")
        reasons.append("category_mismatch")
        score -= 0.3

    # --- abnormal file count ---
    if features.get("suspicious_file_count", 0) >= 5:
        reasons.append("abnormal_file_count")
        score -= 0.1

    score = max(0.0, min(1.0, score))

    if not category_consistent:
        decision = "needs_review"
    elif score >= ACCEPT_SCORE:
        decision = "accepted_shadow"
    elif score < REJECT_SCORE:
        decision = "rejected_shadow"
    else:
        decision = "needs_review"

    return {
        "score": score,
        "reasons": reasons,
        "subtitle_evidence": subtitle_evidence,
        "category_consistent": category_consistent,
        "inferred_category": _infer_category(features, context, subtitle_evidence),
        "resolution_consistent": None,  # deferred: deep resolution check (D10-adjacent)
        "decision": decision,
    }


def _infer_category(
    features: dict[str, Any], context: dict[str, Any], subtitle_evidence: str
) -> str:
    """Best-effort inferred category from file-list + name hints.

    Phase 1 only distinguishes subtitle vs no_subtitle from file evidence; the
    hacked/censored axis needs content inspection (deferred by D10), so it is
    carried over from the JavDB-claimed category prefix.
    """
    claimed = context.get("javdb_category") or ""
    hacked = claimed.startswith("hacked_")
    has_subtitle = subtitle_evidence == "file_present"
    if hacked:
        return "hacked_subtitle" if has_subtitle else "hacked_no_subtitle"
    return "subtitle" if has_subtitle else "no_subtitle"
```

- [x] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_quality_scoring.py -v
```

Expected: PASS (5 tests).

- [x] **Step 5: Commit**

```bash
git add javdb/quality/scoring.py tests/unit/test_quality_scoring.py
git commit -m "feat(quality): add explainable shadow scoring (ADR-024)"
```

---

## Task 3 — Export the new symbols

**Files:**
- Modify: `javdb/quality/__init__.py`

- [x] **Step 1: Extend the package exports**

Replace the body of `javdb/quality/__init__.py` (created in IMP-02) with:

```python
"""Torrent quality evidence + shadow evaluation domain (ADR-024 Phase 1)."""

from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features
from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.quality.scoring import SCORING_VERSION, score_torrent

__all__ = [
    "EvidenceRecord",
    "EvaluationRecord",
    "PROBE_SCHEMA_VERSION",
    "SCORING_VERSION",
    "extract_file_features",
    "score_torrent",
]
```

- [x] **Step 2: Verify imports resolve**

Run:

```bash
python3 -c "from javdb.quality import PROBE_SCHEMA_VERSION, SCORING_VERSION, extract_file_features, score_torrent; print(PROBE_SCHEMA_VERSION, SCORING_VERSION)"
```

Expected: `adr024-probe-v1 adr024-shadow-v1`.

- [x] **Step 3: Commit**

```bash
git add javdb/quality/__init__.py
git commit -m "feat(quality): export feature/scoring symbols (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Features pure & versioned | `pytest tests/unit/test_quality_features.py -v` → PASS; `PROBE_SCHEMA_VERSION` present |
| 2 | Scoring explainable | `pytest tests/unit/test_quality_scoring.py -v` → PASS; every score change emits a reason code |
| 3 | No I/O | `rg -n "import requests\|get_db\|http" javdb/quality/features.py javdb/quality/scoring.py` → no output |
| 4 | Exports resolve | Task 3 Step 2 prints both version strings |
