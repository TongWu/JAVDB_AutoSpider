# IMP-ADR024-02: ADR-024 Phase 1 — Models & Repository

**Status:** Completed — implemented 2026-05-31.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed dataclasses for the two ADR-024 records and a `TorrentQualityRepo` that UPSERTs/reads them on the canonical D1 `reports` database, following the established **conn-injected** repo pattern (constructor takes a live `get_db()` connection; a single `_*_COLUMNS` tuple drives INSERT/extract/row→dict).

**Architecture:** A new domain package `javdb/quality/` holds the dataclasses (`models.py`) as **pure domain objects** (no `to_row()`). A new repo `javdb/storage/repos/torrent_quality_repo.py` mirrors `AcquisitionOutcomeRepo` (ADR-033): `__init__(self, conn)`, direct UPSERT keyed by the table primary keys, no session/pending flow. The repo owns all storage concerns (`json.dumps`, bool→int, the promoted-column/`features` split). Reads are backend-agnostic (rows accessed by column **name**, with `row_factory = sqlite3.Row` set defensively).

**Tech Stack:** Python 3.11, `dataclasses`, Cloudflare D1 via a `get_db()`-supplied connection, pytest.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), D2, D3; "Evidence Model".

**Related:** [IMP-ADR024-01](IMP-ADR024-01-d1-schema.md) (tables) · [IMP-ADR024-03](IMP-ADR024-03-feature-extraction-scoring.md) (produces records) · [IMP-ADR024-06](IMP-ADR024-06-read-api.md) (reads via repo).

**Depends on:** IMP-ADR024-01 (tables must exist).

**Blocks:** IMP-ADR024-03, -05, -06.

---

## Design Review note (2026-05-31)

A `brainstorming` pass reviewed the first draft of this IMP and corrected two
factual/structural issues plus locked the feature-storage contract:

1. **Repo is conn-injected, not path-based.** The first draft claimed it mirrored
   `OpsIncidentRepo` "constructor takes an explicit `db_path`." `OpsIncidentRepo`
   is actually `__init__(self, conn)`, and every recent ADR-era direct-UPSERT
   repo (ADR-026/033/035/036/040) is conn-injected. Conn-injection lets the
   IMP-05 collector write one torrent's evidence + N evaluations in a single
   `get_db()` transaction (atomic, fewer D1 round-trips).
2. **Backend-agnostic row access.** The first draft read rows by positional index
   (`row[i]`), which raises under the D1 backend (dict rows). Rows are read by
   column **name**.
3. **Feature storage contract (Decision):** typed columns = promoted, queryable
   facts; `EvidenceRecord.features` holds ONLY not-yet-promoted experimental /
   summary features. The two never overlap — enforced at the repo serialization
   boundary (`_PROMOTED_EVIDENCE_KEYS`) and pinned by a unit test. This is the
   escape hatch that lets IMP-03 add features without a model/schema change.

Decisions: schema (IMP-01) was in scope but needs no change under this contract;
package `javdb/quality/`; record names `EvidenceRecord` / `EvaluationRecord`.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/quality/__init__.py` | Package marker + public exports. |
| Create | `javdb/quality/models.py` | `EvidenceRecord` + `EvaluationRecord` pure dataclasses (no serialization). |
| Create | `javdb/storage/repos/torrent_quality_repo.py` | Conn-injected UPSERT/read repo; owns row mapping. |
| Create | `tests/unit/test_torrent_quality_repo.py` | Round-trip UPSERT/get/list + feature/column non-overlap invariant. |

## Scope Boundaries

- Do **not** add scoring or feature-extraction logic here (that is IMP-03).
- Do **not** add SQLite-only behavior; the repo is backend-agnostic (conn from
  `get_db()`, rows by name).
- Do **not** write a CLI or API here (IMP-05 / IMP-06).
- Do **not** give the model a `to_row()` / serialization method; mapping lives in
  the repo.

---

## Task 1 — Record dataclasses

**Files:**
- Create: `javdb/quality/__init__.py`
- Create: `javdb/quality/models.py`
- Test: `tests/unit/test_torrent_quality_repo.py` (created in Task 2)

- [x] **Step 1: Create the package marker**

Create `javdb/quality/__init__.py`:

```python
"""Torrent quality evidence + shadow evaluation domain (ADR-024 Phase 1)."""

from javdb.quality.models import EvaluationRecord, EvidenceRecord

__all__ = ["EvidenceRecord", "EvaluationRecord"]
```

- [x] **Step 2: Create the dataclasses**

Create `javdb/quality/models.py`. These are **pure** dataclasses — no `to_row()`;
the repo owns all mapping to DB columns.

```python
"""Typed records for ADR-024 torrent quality evidence and evaluation.

`EvidenceRecord` mirrors `TorrentQualityEvidence` (torrent-level objective
facts). `EvaluationRecord` mirrors `TorrentQualityEvaluation` (movie-context
shadow scoring). Both are pure domain objects: serialization to DB rows lives in
`TorrentQualityRepo`, not here.

Feature-storage contract (ADR-024 review, 2026-05-31): the promoted typed fields
on `EvidenceRecord` are the queryable columns. `EvidenceRecord.features` holds
ONLY not-yet-promoted experimental/summary features and must never reuse a
promoted column name; the repo enforces this and a unit test pins it.

`policy_mode` / `decision` / `would_replace_current_choice` / `shadow_rank` are
reserved for ADR-024 Phase 2/3; Phase 1 always sets `policy_mode="shadow"`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EvidenceRecord:
    info_hash: str
    probe_schema_version: str
    target_role: str  # "production_download" | "quality_probe"
    probe_target_name: Optional[str] = None
    metadata_status: Optional[str] = None
    metadata_started_at: Optional[str] = None
    metadata_completed_at: Optional[str] = None
    total_size_bytes: Optional[int] = None
    main_video_size_bytes: Optional[int] = None
    main_video_ratio: Optional[float] = None
    video_file_count: Optional[int] = None
    subtitle_file_count: Optional[int] = None
    non_video_file_count: Optional[int] = None
    junk_size_bytes: Optional[int] = None
    junk_size_ratio: Optional[float] = None
    suspicious_file_count: Optional[int] = None
    # Non-promoted experimental/summary features only (never a promoted column).
    features: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    source_fingerprint: Optional[str] = None


@dataclass
class EvaluationRecord:
    info_hash: str
    movie_href: str
    scoring_version: str
    video_code: Optional[str] = None
    javdb_category: Optional[str] = None
    magnet_name: Optional[str] = None
    javdb_tags: list[str] = field(default_factory=list)
    javdb_size_text: Optional[str] = None
    inferred_category: Optional[str] = None
    category_consistent: Optional[bool] = None
    subtitle_evidence: Optional[str] = None
    resolution_consistent: Optional[bool] = None
    source_trust: Optional[str] = None
    score: Optional[float] = None
    shadow_rank: Optional[int] = None
    would_replace_current_choice: Optional[bool] = None
    policy_mode: str = "shadow"
    decision: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
```

- [x] **Step 3: Commit**

```bash
git add javdb/quality/__init__.py javdb/quality/models.py
git commit -m "feat(quality): add torrent quality record dataclasses (ADR-024)"
```

---

## Task 2 — Repository

**Files:**
- Create: `javdb/storage/repos/torrent_quality_repo.py`
- Test: `tests/unit/test_torrent_quality_repo.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_torrent_quality_repo.py`. The fixture mirrors
`acquisition_outcome_conn` (tests/conftest.py): an in-memory SQLite seeded from
the real D1 migration file, so the test exercises the shipped DDL.

```python
"""Round-trip + invariant tests for TorrentQualityRepo (ADR-024 Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

_MIGRATION = Path(
    "javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql"
)


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MIGRATION.read_text(encoding="utf-8"))
    try:
        yield conn
    finally:
        conn.close()


def test_upsert_and_get_evidence(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        total_size_bytes=1000,
        main_video_size_bytes=900,
        main_video_ratio=0.9,
        reasons=["main_video_detected"],
        features={"container": "mkv"},  # non-promoted only
    )
    repo.upsert_evidence(rec)

    row = repo.get_evidence("ABC123", "v1", "production_download")
    assert row is not None
    assert row["total_size_bytes"] == 1000
    assert row["main_video_ratio"] == 0.9
    assert "main_video_detected" in row["reasons_json"]
    assert "mkv" in row["features_json"]


def test_upsert_evidence_is_idempotent(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        total_size_bytes=1000,
    )
    repo.upsert_evidence(rec)
    rec.total_size_bytes = 2000
    repo.upsert_evidence(rec)

    row = repo.get_evidence("ABC123", "v1", "production_download")
    assert row["total_size_bytes"] == 2000
    assert conn.execute(
        "SELECT COUNT(*) FROM TorrentQualityEvidence"
    ).fetchone()[0] == 1


def test_features_must_not_duplicate_promoted_columns(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        video_file_count=1,
        features={"video_file_count": 1},  # collides with a promoted column
    )
    with pytest.raises(ValueError):
        repo.upsert_evidence(rec)


def test_upsert_and_list_evaluation(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvaluationRecord(
        info_hash="ABC123",
        movie_href="/v/abc",
        scoring_version="v1",
        video_code="ABC-123",
        javdb_category="subtitle",
        javdb_tags=["1080p", "subtitle"],
        score=0.82,
        shadow_rank=1,
        would_replace_current_choice=False,
        decision="accepted_shadow",
        reasons=["subtitle_file_missing"],
    )
    repo.upsert_evaluation(rec)

    rows = repo.list_evaluations_for_movie("/v/abc")
    assert len(rows) == 1
    assert rows[0]["video_code"] == "ABC-123"
    assert rows[0]["score"] == 0.82
    assert rows[0]["policy_mode"] == "shadow"
    assert rows[0]["would_replace_current_choice"] == 0
    assert "1080p" in rows[0]["javdb_tags_json"]


def test_list_recent_evaluations_orders_by_created_at(conn):
    repo = TorrentQualityRepo(conn)
    repo.upsert_evaluation(
        EvaluationRecord(
            info_hash="ABC123",
            movie_href="/v/abc",
            scoring_version="v1",
            video_code="ABC-123",
            shadow_rank=2,
        )
    )
    conn.execute(
        """
        UPDATE TorrentQualityEvaluation
        SET created_at = ?
        WHERE info_hash = ? AND movie_href = ? AND scoring_version = ?
        """,
        ("2026-05-31T00:00:00.000Z", "ABC123", "/v/abc", "v1"),
    )
    repo.upsert_evaluation(
        EvaluationRecord(
            info_hash="DEF456",
            movie_href="/v/def",
            scoring_version="v1",
            video_code="DEF-456",
            shadow_rank=1,
        )
    )
    conn.execute(
        """
        UPDATE TorrentQualityEvaluation
        SET created_at = ?
        WHERE info_hash = ? AND movie_href = ? AND scoring_version = ?
        """,
        ("2026-05-31T00:00:01.000Z", "DEF456", "/v/def", "v1"),
    )

    rows = repo.list_recent_evaluations(limit=1)
    assert len(rows) == 1
    assert rows[0]["info_hash"] == "DEF456"
```

- [x] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_torrent_quality_repo.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.quality` / `javdb.storage.repos.torrent_quality_repo`.

- [x] **Step 3: Implement the repository**

Create `javdb/storage/repos/torrent_quality_repo.py`:

```python
"""Repository for ADR-024 torrent quality tables (Phase 1).

Conn-injected direct-UPSERT access to `TorrentQualityEvidence` and
`TorrentQualityEvaluation` on the canonical D1 `reports` database. These tables
sit outside the Pending->Commit session flow. Follows the `AcquisitionOutcomeRepo`
(ADR-033) pattern: `__init__(self, conn)` so a caller (e.g. the IMP-05 collector)
can write one torrent's evidence + N evaluations in a single transaction; a single
`_*_COLUMNS` tuple drives INSERT columns, value extraction, and row->dict.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional

from javdb.quality.models import EvaluationRecord, EvidenceRecord

logger = logging.getLogger(__name__)

# Promoted, queryable evidence columns — `EvidenceRecord.features` may not reuse
# these names (otherwise one fact would live in both a column and features_json).
_PROMOTED_EVIDENCE_KEYS = frozenset(
    {
        "probe_target_name",
        "metadata_status",
        "metadata_started_at",
        "metadata_completed_at",
        "total_size_bytes",
        "main_video_size_bytes",
        "main_video_ratio",
        "video_file_count",
        "subtitle_file_count",
        "non_video_file_count",
        "junk_size_bytes",
        "junk_size_ratio",
        "suspicious_file_count",
        "source_fingerprint",
    }
)

_EVIDENCE_COLUMNS = (
    "info_hash",
    "probe_schema_version",
    "target_role",
    "probe_target_name",
    "metadata_status",
    "metadata_started_at",
    "metadata_completed_at",
    "total_size_bytes",
    "main_video_size_bytes",
    "main_video_ratio",
    "video_file_count",
    "subtitle_file_count",
    "non_video_file_count",
    "junk_size_bytes",
    "junk_size_ratio",
    "suspicious_file_count",
    "features_json",
    "reasons_json",
    "source_fingerprint",
)
_EVIDENCE_PK = ("info_hash", "probe_schema_version", "target_role")

_EVALUATION_COLUMNS = (
    "info_hash",
    "movie_href",
    "scoring_version",
    "video_code",
    "javdb_category",
    "magnet_name",
    "javdb_tags_json",
    "javdb_size_text",
    "inferred_category",
    "category_consistent",
    "subtitle_evidence",
    "resolution_consistent",
    "source_trust",
    "score",
    "shadow_rank",
    "would_replace_current_choice",
    "policy_mode",
    "decision",
    "reasons_json",
)
_EVALUATION_PK = ("info_hash", "movie_href", "scoring_version")


def _bool_to_int(value: Optional[bool]) -> Optional[int]:
    return None if value is None else (1 if value else 0)


def _upsert_sql(table: str, columns: tuple[str, ...], pk: tuple[str, ...]) -> str:
    placeholders = ", ".join(["?"] * len(columns))
    conflict = ", ".join(pk)
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in pk)
    updates = f"{updates}, updated_at=(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )


def _evidence_values(rec: EvidenceRecord) -> list[Any]:
    overlap = _PROMOTED_EVIDENCE_KEYS & set(rec.features)
    if overlap:
        raise ValueError(
            "EvidenceRecord.features must not duplicate promoted columns: "
            f"{sorted(overlap)}"
        )
    return [
        rec.info_hash,
        rec.probe_schema_version,
        rec.target_role,
        rec.probe_target_name,
        rec.metadata_status,
        rec.metadata_started_at,
        rec.metadata_completed_at,
        rec.total_size_bytes,
        rec.main_video_size_bytes,
        rec.main_video_ratio,
        rec.video_file_count,
        rec.subtitle_file_count,
        rec.non_video_file_count,
        rec.junk_size_bytes,
        rec.junk_size_ratio,
        rec.suspicious_file_count,
        json.dumps(rec.features, ensure_ascii=False),
        json.dumps(rec.reasons, ensure_ascii=False),
        rec.source_fingerprint,
    ]


def _evaluation_values(rec: EvaluationRecord) -> list[Any]:
    return [
        rec.info_hash,
        rec.movie_href,
        rec.scoring_version,
        rec.video_code,
        rec.javdb_category,
        rec.magnet_name,
        json.dumps(rec.javdb_tags, ensure_ascii=False),
        rec.javdb_size_text,
        rec.inferred_category,
        _bool_to_int(rec.category_consistent),
        rec.subtitle_evidence,
        _bool_to_int(rec.resolution_consistent),
        rec.source_trust,
        rec.score,
        rec.shadow_rank,
        _bool_to_int(rec.would_replace_current_choice),
        rec.policy_mode,
        rec.decision,
        json.dumps(rec.reasons, ensure_ascii=False),
    ]


class TorrentQualityRepo:
    """Conn-injected read/write access to the ADR-024 quality tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:  # D1 connections may not expose row_factory
            logger.debug("row_factory set failed", exc_info=True)

    # -- evidence -----------------------------------------------------------
    def upsert_evidence(self, record: EvidenceRecord) -> None:
        self._conn.execute(
            _upsert_sql("TorrentQualityEvidence", _EVIDENCE_COLUMNS, _EVIDENCE_PK),
            _evidence_values(record),
        )

    def get_evidence(
        self, info_hash: str, probe_schema_version: str, target_role: str
    ) -> Optional[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVIDENCE_COLUMNS)} FROM TorrentQualityEvidence "
            "WHERE info_hash = ? AND probe_schema_version = ? AND target_role = ?"
        )
        row = self._conn.execute(
            sql, (info_hash, probe_schema_version, target_role)
        ).fetchone()
        return self._to_dict(row, _EVIDENCE_COLUMNS) if row else None

    # -- evaluation ---------------------------------------------------------
    def upsert_evaluation(self, record: EvaluationRecord) -> None:
        self._conn.execute(
            _upsert_sql(
                "TorrentQualityEvaluation", _EVALUATION_COLUMNS, _EVALUATION_PK
            ),
            _evaluation_values(record),
        )

    def list_evaluations_for_movie(self, movie_href: str) -> list[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVALUATION_COLUMNS)} FROM TorrentQualityEvaluation "
            "WHERE movie_href = ? ORDER BY shadow_rank ASC"
        )
        rows = self._conn.execute(sql, (movie_href,)).fetchall()
        return [self._to_dict(r, _EVALUATION_COLUMNS) for r in rows]

    def list_recent_evaluations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVALUATION_COLUMNS)} FROM TorrentQualityEvaluation "
            "ORDER BY created_at DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (limit,)).fetchall()
        return [self._to_dict(r, _EVALUATION_COLUMNS) for r in rows]

    @staticmethod
    def _to_dict(row: Mapping[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
        # Access by NAME (works for sqlite3.Row and D1 dict rows alike).
        return {col: row[col] for col in columns}
```

- [x] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_torrent_quality_repo.py -v
```

Expected: PASS (5 tests).

- [x] **Step 5: Commit**

```bash
git add javdb/storage/repos/torrent_quality_repo.py tests/unit/test_torrent_quality_repo.py
git commit -m "feat(quality): add conn-injected TorrentQualityRepo with UPSERT/read (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Pure domain models | `EvidenceRecord` / `EvaluationRecord` carry no `to_row()` / serialization |
| 2 | Repo round-trips | `pytest tests/unit/test_torrent_quality_repo.py -v` → PASS (4 tests) |
| 3 | No session coupling | Repo never imports `db_session` / pending-write helpers |
| 4 | Conn-injected | Repo is `__init__(self, conn)` (matches ADR-033/040); caller owns `get_db()` |
| 5 | Backend-agnostic reads | Rows accessed by column **name**, never positional index |
| 6 | Feature/column non-overlap | `features` keys colliding with promoted columns raise `ValueError` (pinned by test) |
