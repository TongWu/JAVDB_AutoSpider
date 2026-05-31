# IMP-ADR024-02: ADR-024 Phase 1 — Models & Repository

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed dataclasses for the two ADR-024 records and a `TorrentQualityRepo` that UPSERTs/reads them on the canonical D1 `reports` database, following the established repo pattern (constructor takes `db_path`, each method opens a short-lived `get_db()`).

**Architecture:** A new domain package `javdb/quality/` holds the dataclasses (`models.py`). A new repo `javdb/storage/repos/torrent_quality_repo.py` mirrors `OpsIncidentRepo` (ADR-026): direct UPSERT keyed by the table primary keys, no session/pending flow. `get_db()` takes the filesystem path `REPORTS_DB_PATH`, **never** a logical name.

**Tech Stack:** Python 3.11, `dataclasses`, Cloudflare D1 via `get_db()`, pytest.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), D2, D3; "Evidence Model".

**Related:** [IMP-ADR024-01](IMP-ADR024-01-d1-schema.md) (tables) · [IMP-ADR024-03](IMP-ADR024-03-feature-extraction-scoring.md) (produces records) · [IMP-ADR024-06](IMP-ADR024-06-read-api.md) (reads via repo).

**Depends on:** IMP-ADR024-01 (tables must exist).

**Blocks:** IMP-ADR024-03, -05, -06.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/quality/__init__.py` | Package marker + public exports. |
| Create | `javdb/quality/models.py` | `EvidenceRecord` + `EvaluationRecord` dataclasses with `to_row()`. |
| Create | `javdb/storage/repos/torrent_quality_repo.py` | UPSERT/read repo on `REPORTS_DB_PATH`. |
| Create | `tests/unit/test_torrent_quality_repo.py` | Round-trip UPSERT/get/list tests. |

## Scope Boundaries

- Do **not** add scoring or feature-extraction logic here (that is IMP-03).
- Do **not** add SQLite-only behavior; the repo is backend-agnostic via `get_db()`.
- Do **not** write a CLI or API here (IMP-05 / IMP-06).

---

## Task 1 — Record dataclasses

**Files:**
- Create: `javdb/quality/__init__.py`
- Create: `javdb/quality/models.py`
- Test: `tests/unit/test_torrent_quality_repo.py` (created in Task 2)

- [ ] **Step 1: Create the package marker**

Create `javdb/quality/__init__.py`:

```python
"""Torrent quality evidence + shadow evaluation domain (ADR-024 Phase 1)."""

from javdb.quality.models import EvaluationRecord, EvidenceRecord

__all__ = ["EvidenceRecord", "EvaluationRecord"]
```

- [ ] **Step 2: Create the dataclasses**

Create `javdb/quality/models.py`:

```python
"""Typed records for ADR-024 torrent quality evidence and evaluation.

`EvidenceRecord` mirrors `TorrentQualityEvidence` (torrent-level objective
facts). `EvaluationRecord` mirrors `TorrentQualityEvaluation` (movie-context
shadow scoring). Both expose `to_row()` returning a column->value dict that the
repository UPSERTs directly. The `policy_mode` / `decision` /
`would_replace_current_choice` / `shadow_rank` fields are reserved for ADR-024
Phase 2/3; Phase 1 always sets `policy_mode="shadow"`.
"""

from __future__ import annotations

import json
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
    features: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    source_fingerprint: Optional[str] = None

    def to_row(self) -> dict[str, Any]:
        return {
            "info_hash": self.info_hash,
            "probe_schema_version": self.probe_schema_version,
            "target_role": self.target_role,
            "probe_target_name": self.probe_target_name,
            "metadata_status": self.metadata_status,
            "metadata_started_at": self.metadata_started_at,
            "metadata_completed_at": self.metadata_completed_at,
            "total_size_bytes": self.total_size_bytes,
            "main_video_size_bytes": self.main_video_size_bytes,
            "main_video_ratio": self.main_video_ratio,
            "video_file_count": self.video_file_count,
            "subtitle_file_count": self.subtitle_file_count,
            "non_video_file_count": self.non_video_file_count,
            "junk_size_bytes": self.junk_size_bytes,
            "junk_size_ratio": self.junk_size_ratio,
            "suspicious_file_count": self.suspicious_file_count,
            "features_json": json.dumps(self.features, ensure_ascii=False),
            "reasons_json": json.dumps(self.reasons, ensure_ascii=False),
            "source_fingerprint": self.source_fingerprint,
        }


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

    @staticmethod
    def _bool_to_int(value: Optional[bool]) -> Optional[int]:
        if value is None:
            return None
        return 1 if value else 0

    def to_row(self) -> dict[str, Any]:
        return {
            "info_hash": self.info_hash,
            "movie_href": self.movie_href,
            "scoring_version": self.scoring_version,
            "video_code": self.video_code,
            "javdb_category": self.javdb_category,
            "magnet_name": self.magnet_name,
            "javdb_tags_json": json.dumps(self.javdb_tags, ensure_ascii=False),
            "javdb_size_text": self.javdb_size_text,
            "inferred_category": self.inferred_category,
            "category_consistent": self._bool_to_int(self.category_consistent),
            "subtitle_evidence": self.subtitle_evidence,
            "resolution_consistent": self._bool_to_int(self.resolution_consistent),
            "source_trust": self.source_trust,
            "score": self.score,
            "shadow_rank": self.shadow_rank,
            "would_replace_current_choice": self._bool_to_int(
                self.would_replace_current_choice
            ),
            "policy_mode": self.policy_mode,
            "decision": self.decision,
            "reasons_json": json.dumps(self.reasons, ensure_ascii=False),
        }
```

- [ ] **Step 3: Commit**

```bash
git add javdb/quality/__init__.py javdb/quality/models.py
git commit -m "feat(quality): add torrent quality record dataclasses (ADR-024)"
```

---

## Task 2 — Repository

**Files:**
- Create: `javdb/storage/repos/torrent_quality_repo.py`
- Test: `tests/unit/test_torrent_quality_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_torrent_quality_repo.py`:

```python
"""Round-trip tests for TorrentQualityRepo (ADR-024 Phase 1)."""

from __future__ import annotations

from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.storage.db import REPORTS_DB_PATH
from javdb.storage.db._db_migrations import init_db
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo


def _repo(tmp_path) -> TorrentQualityRepo:
    db_path = str(tmp_path / "reports.db")
    init_db(db_path)
    return TorrentQualityRepo(db_path=db_path)


def test_upsert_and_get_evidence(tmp_path):
    repo = _repo(tmp_path)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        total_size_bytes=1000,
        main_video_size_bytes=900,
        main_video_ratio=0.9,
        reasons=["main_video_detected"],
        features={"video_file_count": 1},
    )
    repo.upsert_evidence(rec)

    row = repo.get_evidence("ABC123", "v1", "production_download")
    assert row is not None
    assert row["total_size_bytes"] == 1000
    assert row["main_video_ratio"] == 0.9
    assert "main_video_detected" in row["reasons_json"]


def test_upsert_evidence_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
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


def test_upsert_and_list_evaluation(tmp_path):
    repo = _repo(tmp_path)
    rec = EvaluationRecord(
        info_hash="ABC123",
        movie_href="/v/abc",
        scoring_version="v1",
        video_code="ABC-123",
        javdb_category="subtitle",
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_torrent_quality_repo.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.storage.repos.torrent_quality_repo`.

- [ ] **Step 3: Implement the repository**

Create `javdb/storage/repos/torrent_quality_repo.py`:

```python
"""Repository for ADR-024 torrent quality tables (Phase 1).

Direct-UPSERT access to `TorrentQualityEvidence` and `TorrentQualityEvaluation`
on the canonical D1 `reports` database. These tables sit outside the
Pending->Commit session flow. Follows the `OpsIncidentRepo` pattern: the
constructor takes an explicit `db_path` (defaulting to `REPORTS_DB_PATH`) and
each method opens its own short-lived `get_db()` connection.
"""

from __future__ import annotations

from typing import Any, Optional

from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.storage.db import REPORTS_DB_PATH, get_db

_EVIDENCE_COLS = [
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
]
_EVIDENCE_PK = ("info_hash", "probe_schema_version", "target_role")

_EVALUATION_COLS = [
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
]
_EVALUATION_PK = ("info_hash", "movie_href", "scoring_version")


def _upsert_sql(table: str, cols: list[str], pk: tuple[str, ...]) -> str:
    placeholders = ", ".join(["?"] * len(cols))
    conflict = ", ".join(pk)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
    updates = f"{updates}, updated_at=(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
    return (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )


class TorrentQualityRepo:
    """Read/write access to the ADR-024 quality tables."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or REPORTS_DB_PATH

    # -- evidence -----------------------------------------------------------
    def upsert_evidence(self, record: EvidenceRecord) -> None:
        row = record.to_row()
        sql = _upsert_sql("TorrentQualityEvidence", _EVIDENCE_COLS, _EVIDENCE_PK)
        values = [row[c] for c in _EVIDENCE_COLS]
        with get_db(self._db_path) as conn:
            conn.execute(sql, values)

    def get_evidence(
        self, info_hash: str, probe_schema_version: str, target_role: str
    ) -> Optional[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVIDENCE_COLS)} FROM TorrentQualityEvidence "
            "WHERE info_hash = ? AND probe_schema_version = ? AND target_role = ?"
        )
        with get_db(self._db_path) as conn:
            row = conn.execute(
                sql, (info_hash, probe_schema_version, target_role)
            ).fetchone()
        return self._to_dict(row, _EVIDENCE_COLS) if row else None

    # -- evaluation ---------------------------------------------------------
    def upsert_evaluation(self, record: EvaluationRecord) -> None:
        row = record.to_row()
        sql = _upsert_sql(
            "TorrentQualityEvaluation", _EVALUATION_COLS, _EVALUATION_PK
        )
        values = [row[c] for c in _EVALUATION_COLS]
        with get_db(self._db_path) as conn:
            conn.execute(sql, values)

    def list_evaluations_for_movie(
        self, movie_href: str
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVALUATION_COLS)} FROM TorrentQualityEvaluation "
            "WHERE movie_href = ? ORDER BY shadow_rank ASC"
        )
        with get_db(self._db_path) as conn:
            rows = conn.execute(sql, (movie_href,)).fetchall()
        return [self._to_dict(r, _EVALUATION_COLS) for r in rows]

    def list_recent_evaluations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVALUATION_COLS)} FROM TorrentQualityEvaluation "
            "ORDER BY created_at DESC LIMIT ?"
        )
        with get_db(self._db_path) as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [self._to_dict(r, _EVALUATION_COLS) for r in rows]

    @staticmethod
    def _to_dict(row: Any, cols: list[str]) -> dict[str, Any]:
        return {col: row[i] for i, col in enumerate(cols)}
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_torrent_quality_repo.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/torrent_quality_repo.py tests/unit/test_torrent_quality_repo.py
git commit -m "feat(quality): add TorrentQualityRepo with UPSERT/read (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Records serialize | `EvidenceRecord.to_row()` / `EvaluationRecord.to_row()` produce the exact repo column keys |
| 2 | Repo round-trips | `pytest tests/unit/test_torrent_quality_repo.py -v` → PASS |
| 3 | No session coupling | Repo never imports `db_session` / pending-write helpers |
| 4 | Path-based `get_db` | Repo passes `REPORTS_DB_PATH` (a path), never a logical name |
