# IMP-ADR026-02: ADR-026 Phase 2 - Incident History, Similarity, And Analytics

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-026 Phase 2 by turning persisted read-only diagnosis records into an operator-facing incident history with deterministic similarity search and lightweight analytics.

**Architecture:** Keep D1 as the source of truth and extend the Phase 1 `OpsIncidents` read model with derived, explainable feature records. Add Python API endpoints for history filters, similar incidents, and aggregate metrics, then mirror the same read-only surface in the Cloudflare Worker-backed web repo. Build a dense diagnostics UI that lets operators inspect incident detail, compare similar incidents, and understand recurring failure patterns without introducing any remediation path.

**Tech Stack:** Python 3.11, Cloudflare D1, FastAPI/Pydantic, pytest, TypeScript, Hono, Vue 3, Naive UI, Vitest, Playwright, Markdown docs.

**Source spec:** [ADR-026](ADR-026-ai-operations-diagnosis.md), Phase 2 roadmap, D1-D10.

**Non-negotiable:** Phase 2 remains read-only. It must not roll back sessions, rerun workflows, modify D1 except for storing derived read-model metadata, delete qBittorrent tasks, approve remediation, or mark recovery events resolved.

## Table of Contents

- [File Map](#file-map)
- [Scope Boundaries](#scope-boundaries)
- [Task 1: D1 Feature Read Model](#task-1-d1-feature-read-model)
- [Task 2: Feature Extraction Contracts](#task-2-feature-extraction-contracts)
- [Task 3: Repository Search And Feature Persistence](#task-3-repository-search-and-feature-persistence)
- [Task 4: Similarity Scoring](#task-4-similarity-scoring)
- [Task 5: Analytics Aggregation](#task-5-analytics-aggregation)
- [Task 6: Python API Read Endpoints](#task-6-python-api-read-endpoints)
- [Task 7: Cloudflare Worker API Parity](#task-7-cloudflare-worker-api-parity)
- [Task 8: Frontend API Client And UI](#task-8-frontend-api-client-and-ui)
- [Task 9: Documentation And Workflow Review](#task-9-documentation-and-workflow-review)
- [Task 10: Verification And Closeout](#task-10-verification-and-closeout)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql` | D1-first derived feature table for explainable incident similarity and analytics. |
| Modify | `javdb/storage/db/_db_migrations.py` | Include the new feature table in local SQLite mirror initialization. |
| Modify | `javdb/ops/diagnosis/models.py` | Add typed feature, similarity, and analytics contracts. |
| Create | `javdb/ops/diagnosis/features.py` | Extract deterministic tokens and categorical features from incident bundles plus persisted records. |
| Create | `javdb/ops/diagnosis/similarity.py` | Score similar incidents using explainable weighted overlap, not embeddings. |
| Create | `javdb/ops/diagnosis/analytics.py` | Produce lightweight counts by type, status, confidence, and open high-confidence incidents. |
| Modify | `javdb/ops/diagnosis/service.py` | Persist derived feature rows alongside incident records. |
| Modify | `javdb/storage/repos/ops_incident_repo.py` | Add search filters and feature upsert/list methods. |
| Modify | `apps/api/schemas/diagnostics.py` | Add history filter, similarity, and analytics response schemas. |
| Modify | `apps/api/routers/diagnostics.py` | Add read-only endpoints for history, similar incidents, and analytics. |
| Create | `tests/unit/test_ops_diagnosis_features.py` | Feature extraction tests. |
| Create | `tests/unit/test_ops_diagnosis_similarity.py` | Similarity scoring tests. |
| Create | `tests/unit/test_ops_diagnosis_analytics.py` | Analytics aggregation tests. |
| Modify | `tests/unit/test_ops_incident_repo.py` | Repository tests for feature upsert and filtered incident queries. |
| Modify | `tests/unit/test_ops_diagnostics_api.py` | API tests for new read-only endpoints. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts` | Cloudflare Worker read-only D1 endpoints matching Python API shapes. |
| Modify | `../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts` | Worker diagnostics route tests for incidents, similarity, and analytics. |
| Modify | `../JAVDB_AutoSpider_Web/src/api/diagnostics.ts` | Frontend API client types and functions. |
| Create | `../JAVDB_AutoSpider_Web/src/pages/diagnostics/OpsIncidentsPage.vue` | Incident history and detail UI. |
| Modify | `../JAVDB_AutoSpider_Web/src/router/routes.ts` | Add `/diag/ops-incidents` route. |
| Modify | `../JAVDB_AutoSpider_Web/src/components/layout/Sidebar.vue` | Add diagnostics menu entry. |
| Modify | `../JAVDB_AutoSpider_Web/src/i18n/locales/en.json` | English UI strings. |
| Modify | `../JAVDB_AutoSpider_Web/src/i18n/locales/zh-CN.json` | Chinese UI strings. |
| Modify | `../JAVDB_AutoSpider_Web/src/i18n/locales/ja.json` | Japanese UI strings with English fallback wording if needed. |
| Create | `../JAVDB_AutoSpider_Web/tests/unit/ops-incidents-api.spec.ts` | Frontend API client tests. |
| Create | `../JAVDB_AutoSpider_Web/tests/e2e/ops-incidents.spec.ts` | Playwright smoke for the new diagnostics UI. |
| Modify | `docs/handbook/en/ops/troubleshooting.md` | Document incident history, similarity, and analytics usage. |
| Modify | `docs/handbook/zh/ops/troubleshooting.md` | Chinese mirror. |

## Scope Boundaries

- Use deterministic feature extraction and weighted overlap for Phase 2. Do not add embeddings, Vectorize, or model-based clustering in this phase.
- Store derived metadata only. Do not store full raw logs or new large artifacts in D1.
- Keep UI controls read-only. Buttons may refresh, filter, copy IDs, or open related records, but may not trigger rollback, rerun, cleanup, or recovery mutation.
- Keep Python API and Cloudflare Worker API response shapes aligned because diagnostics is an overlapping surface.
- Capture workflow context at diagnosis time and copy it into the feature row; do not try to recover it later from raw logs.
- Treat analytics as operational hints, not authoritative SLA reporting.

---

## Task 1: D1 Feature Read Model

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql`
- Modify: `javdb/storage/db/_db_migrations.py`

- [ ] **Step 1: Write the D1 migration**

Create `javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql`:

```sql
-- 2026-05-27: Add derived feature rows for ADR-026 Phase 2.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql
--
-- OpsIncidentFeatures stores compact, explainable similarity metadata.
-- It is derived from OpsIncidents and never stores full raw logs.

CREATE TABLE IF NOT EXISTS OpsIncidentFeatures (
  incident_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence TEXT NOT NULL,
  workflow_name TEXT,
  run_id TEXT,
  run_attempt INTEGER,
  session_id TEXT,
  feature_version TEXT NOT NULL,
  categorical_features_json TEXT NOT NULL DEFAULT '{}',
  text_tokens_json TEXT NOT NULL DEFAULT '[]',
  unsafe_action_tokens_json TEXT NOT NULL DEFAULT '[]',
  evidence_kinds_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (incident_id) REFERENCES OpsIncidents(incident_id)
);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_type_status
  ON OpsIncidentFeatures(incident_type, status);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_workflow
  ON OpsIncidentFeatures(workflow_name);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_run
  ON OpsIncidentFeatures(run_id, run_attempt);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_session
  ON OpsIncidentFeatures(session_id);
```

- [ ] **Step 2: Add local mirror DDL**

Modify `javdb/storage/db/_db_migrations.py` by adding the same `OpsIncidentFeatures` table and indexes to the reports DDL block that already contains `OpsIncidents`.

Expected local mirror behavior: a fresh SQLite reports database created for tests contains both `OpsIncidents` and `OpsIncidentFeatures`.

- [ ] **Step 3: Verify schema syntax locally**

Run:

```bash
python3 -m compileall javdb/storage/db/_db_migrations.py
```

Expected: compile succeeds.

- [ ] **Step 4: Defer remote apply**

Record this command for execution during implementation rollout, but do not run it while writing the plan:

```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql
```

Expected during rollout: D1 creates `OpsIncidentFeatures`.

---

## Task 2: Feature Extraction Contracts

**Files:**
- Modify: `javdb/ops/diagnosis/models.py`
- Create: `javdb/ops/diagnosis/features.py`
- Create: `tests/unit/test_ops_diagnosis_features.py`

- [ ] **Step 1: Write failing feature tests**

Create `tests/unit/test_ops_diagnosis_features.py`:

```python
from __future__ import annotations

import json

from javdb.ops.diagnosis.features import build_incident_features
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord


def _context() -> tuple[IncidentBundle, OpsIncidentRecord]:
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        run_id="100",
        run_attempt=2,
        session_id=None,
        workflow_name="DailyIngestion",
        workflow_result="failure",
    )
    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="medium",
        confirmed_findings=[
            "Workflow result is failure.",
            "qBittorrent upload side effect may already have happened.",
        ],
        likely_causes=["The ingestion workflow did not complete successfully."],
        unknowns=["Session id is missing; rollback safety cannot be proven."],
        recommended_next_actions=["Inspect the failed workflow job logs before retrying the run."],
        unsafe_actions=["Do not run forced rollback without locating the owning session."],
        evidence_refs=[],
        model_version="deterministic-fallback-v1",
        detector_version="adr026-detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result).with_persistence_status("d1_written")
    return bundle, record


def test_build_incident_features_extracts_categorical_and_text_tokens():
    bundle, record = _context()
    features = build_incident_features(record, bundle=bundle)

    assert features.incident_id.startswith("opsinc_")
    assert features.incident_type == "failed_ingestion"
    assert features.workflow_name == "DailyIngestion"
    assert features.feature_version == "ops-incident-features-v1"
    assert json.loads(features.categorical_features_json)["confidence"] == "medium"
    assert "workflow" in json.loads(features.text_tokens_json)
    assert "rollback" in json.loads(features.unsafe_action_tokens_json)
```

Expected failure before implementation: `ModuleNotFoundError` or missing `build_incident_features`.

- [ ] **Step 2: Add model**

Add to `javdb/ops/diagnosis/models.py`:

```python
@dataclass(frozen=True)
class OpsIncidentFeatures:
    incident_id: str
    incident_type: str
    status: str
    confidence: Confidence
    workflow_name: str | None
    run_id: str | None
    run_attempt: int | None
    session_id: str | None
    feature_version: str
    categorical_features_json: str
    text_tokens_json: str
    unsafe_action_tokens_json: str
    evidence_kinds_json: str
    created_at: str
    updated_at: str
```

Export `OpsIncidentFeatures` from `javdb/ops/diagnosis/__init__.py`.

- [ ] **Step 3: Implement deterministic feature extraction**

Create `javdb/ops/diagnosis/features.py`:

```python
"""Deterministic feature extraction for ADR-026 incident analytics."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Iterable

from javdb.ops.diagnosis.models import IncidentBundle, OpsIncidentFeatures, OpsIncidentRecord, utc_now_iso

FEATURE_VERSION = "ops-incident-features-v1"
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")
_STOPWORDS = {
    "and",
    "are",
    "before",
    "cannot",
    "the",
    "this",
    "with",
    "without",
}


def _json_load_list(raw: str) -> list:
    value = json.loads(raw or "[]")
    return value if isinstance(value, list) else []


def _tokens(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        for match in _TOKEN_RE.findall(value.lower()):
            if match in _STOPWORDS or match in seen:
                continue
            seen.add(match)
            ordered.append(match)
    return ordered[:80]


def _evidence_kinds(raw: str) -> list[str]:
    kinds: list[str] = []
    seen: set[str] = set()
    for item in _json_load_list(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind and kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def build_incident_features(
    record: OpsIncidentRecord,
    *,
    bundle: IncidentBundle | None = None,
) -> OpsIncidentFeatures:
    findings = [str(item) for item in _json_load_list(record.confirmed_findings_json)]
    causes = [str(item) for item in _json_load_list(record.likely_causes_json)]
    unknowns = [str(item) for item in _json_load_list(record.unknowns_json)]
    actions = [str(item) for item in _json_load_list(record.recommended_next_actions_json)]
    unsafe_actions = [str(item) for item in _json_load_list(record.unsafe_actions_json)]
    evidence_kinds = _evidence_kinds(record.evidence_refs_json)
    now = utc_now_iso()
    categorical = {
        "incident_type": record.incident_type,
        "status": record.status,
        "confidence": record.confidence,
        "trigger_source": record.trigger_source,
        "persistence_status": record.persistence_status,
        "model_version": record.model_version,
        "detector_version": record.detector_version,
    }
    return OpsIncidentFeatures(
        incident_id=record.incident_id,
        incident_type=record.incident_type,
        status=record.status,
        confidence=record.confidence,
        workflow_name=bundle.workflow_name if bundle is not None else None,
        run_id=record.run_id,
        run_attempt=record.run_attempt,
        session_id=record.session_id,
        feature_version=FEATURE_VERSION,
        categorical_features_json=json.dumps(categorical, separators=(",", ":"), ensure_ascii=False),
        text_tokens_json=json.dumps(_tokens([*findings, *causes, *unknowns, *actions]), separators=(",", ":")),
        unsafe_action_tokens_json=json.dumps(_tokens(unsafe_actions), separators=(",", ":")),
        evidence_kinds_json=json.dumps(evidence_kinds, separators=(",", ":")),
        created_at=now,
        updated_at=now,
    )
```

- [ ] **Step 4: Run feature tests**

Run:

```bash
pytest tests/unit/test_ops_diagnosis_features.py -v
```

Expected: pass.

---

## Task 3: Repository Search And Feature Persistence

**Files:**
- Modify: `javdb/storage/repos/ops_incident_repo.py`
- Modify: `javdb/ops/diagnosis/service.py`
- Modify: `tests/unit/test_ops_incident_repo.py`
- Modify: `tests/unit/test_ops_diagnosis_service.py`

- [ ] **Step 1: Add repository tests**

Add to `tests/unit/test_ops_incident_repo.py`:

```python
from javdb.ops.diagnosis.features import build_incident_features


FEATURE_DDL = """
CREATE TABLE OpsIncidentFeatures (
  incident_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence TEXT NOT NULL,
  workflow_name TEXT,
  run_id TEXT,
  run_attempt INTEGER,
  session_id TEXT,
  feature_version TEXT NOT NULL,
  categorical_features_json TEXT NOT NULL,
  text_tokens_json TEXT NOT NULL,
  unsafe_action_tokens_json TEXT NOT NULL,
  evidence_kinds_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def test_repo_upserts_and_reads_features():
    conn = _conn()
    conn.execute(FEATURE_DDL)
    repo = OpsIncidentRepo(conn)
    record = _record()

    repo.upsert(record)
    repo.upsert_features(build_incident_features(record))
    features = repo.get_features(record.incident_id)

    assert features is not None
    assert features.incident_id == record.incident_id
    assert features.incident_type == "failed_ingestion"


def test_repo_filters_incidents_by_incident_type_and_confidence():
    conn = _conn()
    repo = OpsIncidentRepo(conn)
    first = _record()
    second = OpsIncidentRecord(
        **{
            **first.__dict__,
            "incident_id": "opsinc_drift",
            "incident_type": "d1_drift",
            "confidence": "high",
        }
    )

    repo.upsert(first)
    repo.upsert(second)

    items = repo.list(incident_type="d1_drift", confidence="high", limit=20)

    assert [item.incident_id for item in items] == ["opsinc_drift"]
```

- [ ] **Step 2: Add feature columns and mapper**

Modify `javdb/storage/repos/ops_incident_repo.py`:

```python
from javdb.ops.diagnosis.models import OpsIncidentFeatures, OpsIncidentRecord

_FEATURE_COLUMNS = (
    "incident_id",
    "incident_type",
    "status",
    "confidence",
    "workflow_name",
    "run_id",
    "run_attempt",
    "session_id",
    "feature_version",
    "categorical_features_json",
    "text_tokens_json",
    "unsafe_action_tokens_json",
    "evidence_kinds_json",
    "created_at",
    "updated_at",
)


def _row_to_features(row: sqlite3.Row) -> OpsIncidentFeatures:
    return OpsIncidentFeatures(**{column: row[column] for column in _FEATURE_COLUMNS})
```

- [ ] **Step 3: Extend `list` filters**

Change `OpsIncidentRepo.list(...)` signature:

```python
def list(
    self,
    *,
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    incident_type: str | None = None,
    confidence: str | None = None,
    limit: int = 50,
) -> list[OpsIncidentRecord]:
```

Add clauses:

```python
if incident_type:
    clauses.append("incident_type = ?")
    params.append(incident_type)
if confidence:
    clauses.append("confidence = ?")
    params.append(confidence)
```

- [ ] **Step 4: Add feature methods**

Add to `OpsIncidentRepo`:

```python
def upsert_features(self, features: OpsIncidentFeatures) -> None:
    values = [getattr(features, column) for column in _FEATURE_COLUMNS]
    placeholders = ", ".join(["?"] * len(_FEATURE_COLUMNS))
    columns = ", ".join(_FEATURE_COLUMNS)
    update_columns = [column for column in _FEATURE_COLUMNS if column != "incident_id"]
    updates = ", ".join([f"{column}=excluded.{column}" for column in update_columns])
    self._conn.execute(
        f"""
        INSERT INTO OpsIncidentFeatures ({columns})
        VALUES ({placeholders})
        ON CONFLICT(incident_id) DO UPDATE SET {updates}
        """,
        values,
    )

def get_features(self, incident_id: str) -> OpsIncidentFeatures | None:
    row = self._conn.execute(
        f"SELECT {', '.join(_FEATURE_COLUMNS)} FROM OpsIncidentFeatures WHERE incident_id = ?",
        [incident_id],
    ).fetchone()
    return None if row is None else _row_to_features(row)

def list_features(self, *, limit: int = 500) -> list[OpsIncidentFeatures]:
    rows = self._conn.execute(
        f"SELECT {', '.join(_FEATURE_COLUMNS)} FROM OpsIncidentFeatures ORDER BY updated_at DESC LIMIT ?",
        [limit],
    ).fetchall()
    return [_row_to_features(row) for row in rows]
```

- [ ] **Step 5: Add service feature persistence test**

Extend `tests/unit/test_ops_diagnosis_service.py`:

```python
def test_service_persists_feature_row_when_incident_is_written():
    class CapturingRepo:
        def __init__(self):
            self.records = []
            self.features = []

        def upsert(self, record):
            self.records.append(record)

        def upsert_features(self, features):
            self.features.append(features)

    repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(bundle, repo=repo)

    assert record.persistence_status == "d1_written"
    assert repo.features[0].incident_id == record.incident_id
    assert repo.features[0].workflow_name == "DailyIngestion"
```

- [ ] **Step 6: Persist feature rows from service**

Modify `javdb/ops/diagnosis/service.py`:

```python
from javdb.ops.diagnosis.features import build_incident_features
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


def _persist_incident_features(bundle: IncidentBundle, record: OpsIncidentRecord, repo: object | None) -> None:
    features = build_incident_features(record, bundle=bundle)
    if repo is not None and hasattr(repo, "upsert_features"):
        repo.upsert_features(features)
        return
    with get_db(REPORTS_DB_PATH) as conn:
        OpsIncidentRepo(conn).upsert_features(features)
```

Call it after incident persistence:

```python
persisted = persist_incident(record, repo=repo, jsonl_path=jsonl_path)
if persisted.persistence_status == "d1_written":
    _persist_incident_features(bundle, persisted, repo)
return persisted
```

Do not write a feature row when incident persistence fell back to JSONL.

- [ ] **Step 7: Run repository and service tests**

Run:

```bash
pytest tests/unit/test_ops_incident_repo.py tests/unit/test_ops_diagnosis_service.py -v
```

Expected: pass.

---

## Task 4: Similarity Scoring

**Files:**
- Create: `javdb/ops/diagnosis/similarity.py`
- Create: `tests/unit/test_ops_diagnosis_similarity.py`

- [ ] **Step 1: Write similarity tests**

Create `tests/unit/test_ops_diagnosis_similarity.py`:

```python
from __future__ import annotations

from javdb.ops.diagnosis.features import build_incident_features
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.similarity import rank_similar_incidents


def _record(incident_id_suffix: str, incident_type: str, findings: list[str]) -> OpsIncidentRecord:
    result = DiagnosisResult(
        incident_type=incident_type,
        confidence="medium",
        confirmed_findings=findings,
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=[],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli", run_id=incident_id_suffix, run_attempt=1),
        result,
    )
    return record


def test_rank_similar_incidents_prefers_same_type_and_shared_tokens():
    target = build_incident_features(_record("target", "failed_ingestion", ["workflow failure rollback unknown"]))
    close = build_incident_features(_record("close", "failed_ingestion", ["workflow failed rollback unsafe"]))
    far = build_incident_features(_record("far", "d1_recovery_outbox", ["dead letter recovery outbox"]))

    ranked = rank_similar_incidents(target, [far, close])

    assert [item.incident_id for item in ranked] == [close.incident_id, far.incident_id]
    assert ranked[0].score > ranked[1].score
    assert "incident_type" in ranked[0].matched_reasons
    assert any(reason.startswith("text_tokens:") for reason in ranked[0].matched_reasons)
```

- [ ] **Step 2: Add similarity model**

Add to `javdb/ops/diagnosis/models.py`:

```python
@dataclass(frozen=True)
class SimilarIncident:
    incident_id: str
    score: float
    matched_reasons: list[str]
```

Export it from `javdb/ops/diagnosis/__init__.py`.

- [ ] **Step 3: Implement similarity module**

Create `javdb/ops/diagnosis/similarity.py`:

```python
"""Explainable incident similarity for ADR-026 Phase 2."""

from __future__ import annotations

import json

from javdb.ops.diagnosis.models import OpsIncidentFeatures, SimilarIncident


def _list(raw: str) -> list[str]:
    value = json.loads(raw or "[]")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _categorical(raw: str) -> dict[str, str]:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _overlap(left: list[str], right: list[str]) -> tuple[float, list[str]]:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0, []
    matched = sorted(left_set & right_set)
    union = left_set | right_set
    return len(matched) / len(union), matched


def score_similarity(target: OpsIncidentFeatures, candidate: OpsIncidentFeatures) -> SimilarIncident:
    score = 0.0
    reasons: list[str] = []

    if target.incident_type == candidate.incident_type:
        score += 0.35
        reasons.append("incident_type")
    if target.confidence == candidate.confidence:
        score += 0.05
        reasons.append("confidence")

    target_cat = _categorical(target.categorical_features_json)
    candidate_cat = _categorical(candidate.categorical_features_json)
    if target_cat.get("trigger_source") and target_cat.get("trigger_source") == candidate_cat.get("trigger_source"):
        score += 0.05
        reasons.append("trigger_source")

    text_score, text_matches = _overlap(_list(target.text_tokens_json), _list(candidate.text_tokens_json))
    if text_matches:
        score += 0.35 * text_score
        reasons.append("text_tokens:" + ",".join(text_matches[:5]))

    unsafe_score, unsafe_matches = _overlap(
        _list(target.unsafe_action_tokens_json),
        _list(candidate.unsafe_action_tokens_json),
    )
    if unsafe_matches:
        score += 0.15 * unsafe_score
        reasons.append("unsafe_actions:" + ",".join(unsafe_matches[:5]))

    evidence_score, evidence_matches = _overlap(_list(target.evidence_kinds_json), _list(candidate.evidence_kinds_json))
    if evidence_matches:
        score += 0.05 * evidence_score
        reasons.append("evidence_kinds:" + ",".join(evidence_matches[:5]))

    return SimilarIncident(
        incident_id=candidate.incident_id,
        score=round(min(score, 1.0), 4),
        matched_reasons=reasons,
    )


def rank_similar_incidents(
    target: OpsIncidentFeatures,
    candidates: list[OpsIncidentFeatures],
    *,
    limit: int = 5,
) -> list[SimilarIncident]:
    scored = [
        score_similarity(target, candidate)
        for candidate in candidates
        if candidate.incident_id != target.incident_id
    ]
    scored.sort(key=lambda item: (-item.score, item.incident_id))
    return [item for item in scored if item.score > 0][:limit]
```

- [ ] **Step 4: Run similarity tests**

Run:

```bash
pytest tests/unit/test_ops_diagnosis_similarity.py -v
```

Expected: pass.

---

## Task 5: Analytics Aggregation

**Files:**
- Create: `javdb/ops/diagnosis/analytics.py`
- Create: `tests/unit/test_ops_diagnosis_analytics.py`

- [ ] **Step 1: Write analytics tests**

Create `tests/unit/test_ops_diagnosis_analytics.py`:

```python
from __future__ import annotations

from javdb.ops.diagnosis.analytics import summarize_incidents
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord


def _record(incident_type: str, status: str, confidence: str) -> OpsIncidentRecord:
    result = DiagnosisResult(
        incident_type=incident_type,
        confidence=confidence,
        confirmed_findings=[],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=[],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli"),
        result,
    )
    return OpsIncidentRecord(**{**record.__dict__, "status": status})


def test_summarize_incidents_counts_type_status_and_confidence():
    summary = summarize_incidents([
        _record("failed_ingestion", "open", "low"),
        _record("failed_ingestion", "resolved", "medium"),
        _record("d1_drift", "open", "medium"),
    ])

    assert summary["total"] == 3
    assert summary["by_type"] == {"failed_ingestion": 2, "d1_drift": 1}
    assert summary["by_status"] == {"open": 2, "resolved": 1}
    assert summary["by_confidence"] == {"low": 1, "medium": 2}
```

- [ ] **Step 2: Implement analytics**

Create `javdb/ops/diagnosis/analytics.py`:

```python
"""Lightweight ADR-026 incident analytics."""

from __future__ import annotations

from collections import Counter

from javdb.ops.diagnosis.models import OpsIncidentRecord


def _counter_dict(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))


def summarize_incidents(records: list[OpsIncidentRecord]) -> dict:
    return {
        "total": len(records),
        "by_type": _counter_dict([record.incident_type for record in records]),
        "by_status": _counter_dict([record.status for record in records]),
        "by_confidence": _counter_dict([record.confidence for record in records]),
        "open_high_confidence": sum(
            1 for record in records
            if record.status == "open" and record.confidence == "high"
        ),
    }
```

- [ ] **Step 3: Run analytics tests**

Run:

```bash
pytest tests/unit/test_ops_diagnosis_analytics.py -v
```

Expected: pass.

---

## Task 6: Python API Read Endpoints

**Files:**
- Modify: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/routers/diagnostics.py`
- Modify: `tests/unit/test_ops_diagnostics_api.py`

- [ ] **Step 1: Add API tests**

Add to `tests/unit/test_ops_diagnostics_api.py`:

```python
def test_ops_incidents_accepts_type_and_confidence_filters(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics

    captured = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(diagnostics, "_list_ops_incident_records", fake_list)

    response = admin_client.get("/api/diag/ops-incidents?incident_type=d1_drift&confidence=high")

    assert response.status_code == 200
    assert captured["incident_type"] == "d1_drift"
    assert captured["confidence"] == "high"


def test_ops_incident_analytics_returns_summary(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics

    monkeypatch.setattr(diagnostics, "_list_ops_incident_records", lambda **_kwargs: [])

    response = admin_client.get("/api/diag/ops-incidents/analytics")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["by_type"] == {}
```

- [ ] **Step 2: Add schemas**

Add to `apps/api/schemas/diagnostics.py`:

```python
class SimilarIncidentSchema(BaseModel):
    incident_id: str
    score: float
    matched_reasons: list[str]


class OpsIncidentSimilarityResponse(BaseModel):
    incident_id: str
    items: list[SimilarIncidentSchema]


class OpsIncidentAnalyticsResponse(BaseModel):
    total: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_confidence: dict[str, int]
    open_high_confidence: int
```

Add the three schema names to `__all__`.

- [ ] **Step 3: Extend list filters**

Modify `apps/api/routers/diagnostics.py` helper and route signatures:

```python
def _list_ops_incident_records(
    *,
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    incident_type: str | None = None,
    confidence: str | None = None,
    limit: int = 50,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).list(
            status=status,
            run_id=run_id,
            session_id=session_id,
            incident_type=incident_type,
            confidence=confidence,
            limit=limit,
        )
```

Add `incident_type` and `confidence` query parameters to `list_ops_incidents(...)`.

- [ ] **Step 4: Add analytics endpoint**

Add to `apps/api/routers/diagnostics.py`:

```python
from javdb.ops.diagnosis.analytics import summarize_incidents


@router.get("/ops-incidents/analytics", response_model=OpsIncidentAnalyticsResponse)
def get_ops_incident_analytics(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentAnalyticsResponse:
    records = _list_ops_incident_records(limit=500)
    return OpsIncidentAnalyticsResponse(**summarize_incidents(records))
```

Place this static route before `/ops-incidents/{incident_id}`.

- [ ] **Step 5: Add similar endpoint**

Add to `apps/api/routers/diagnostics.py`:

```python
from javdb.ops.diagnosis.similarity import rank_similar_incidents


def _similar_ops_incident_records(incident_id: str, *, limit: int = 5):
    with get_db(REPORTS_DB_PATH) as conn:
        repo = OpsIncidentRepo(conn)
        target = repo.get_features(incident_id)
        if target is None:
            return None
        candidates = repo.list_features(limit=500)
        return rank_similar_incidents(target, candidates, limit=limit)


@router.get("/ops-incidents/{incident_id}/similar", response_model=OpsIncidentSimilarityResponse)
def get_similar_ops_incidents(
    incident_id: str,
    limit: int = 5,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentSimilarityResponse:
    items = _similar_ops_incident_records(incident_id, limit=min(limit, 20))
    if items is None:
        raise HTTPException(status_code=404, detail="Incident features not found")
    return OpsIncidentSimilarityResponse(
        incident_id=incident_id,
        items=[
            SimilarIncidentSchema(
                incident_id=item.incident_id,
                score=item.score,
                matched_reasons=item.matched_reasons,
            )
            for item in items
        ],
    )
```

Place this route before `/ops-incidents/{incident_id}`.

- [ ] **Step 6: Run API tests**

Run:

```bash
pytest tests/unit/test_ops_diagnostics_api.py -v
```

Expected: pass.

---

## Task 7: Cloudflare Worker API Parity

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts`
- Modify: `../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts`

- [ ] **Step 1: Add Worker route tests**

Add to `server/__tests__/diagnostics-routes.test.ts` in the Web repo:

```ts
async function seedOpsIncidentTables(db: D1Database) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS OpsIncidents (
      incident_id TEXT PRIMARY KEY,
      trigger_source TEXT NOT NULL,
      run_id TEXT,
      run_attempt INTEGER,
      session_id TEXT,
      incident_type TEXT NOT NULL,
      status TEXT NOT NULL,
      persistence_status TEXT NOT NULL,
      model_version TEXT NOT NULL,
      detector_version TEXT NOT NULL,
      bundle_schema_version TEXT NOT NULL,
      confidence TEXT NOT NULL,
      confirmed_findings_json TEXT NOT NULL,
      likely_causes_json TEXT NOT NULL,
      unknowns_json TEXT NOT NULL,
      recommended_next_actions_json TEXT NOT NULL,
      unsafe_actions_json TEXT NOT NULL,
      evidence_refs_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      resolved_at TEXT
    )
  `).run();
  await db.prepare("DELETE FROM OpsIncidents").run();
  await db.prepare(`
    INSERT INTO OpsIncidents (
      incident_id, trigger_source, run_id, run_attempt, session_id, incident_type, status,
      persistence_status, model_version, detector_version, bundle_schema_version, confidence,
      confirmed_findings_json, likely_causes_json, unknowns_json, recommended_next_actions_json,
      unsafe_actions_json, evidence_refs_json, created_at, updated_at, resolved_at
    )
    VALUES (
      'opsinc_test', 'workflow_failure', '100', 1, '20260527T000000.000000Z-0000-0000', 'failed_ingestion', 'open',
      'd1_written', 'fallback-v1', 'detectors-v1', 'bundle-v1', 'low',
      '["Workflow result is failure."]', '[]', '[]', '["Inspect logs."]',
      '[]', '[]', '2026-05-27T00:00:00Z', '2026-05-27T00:00:00Z', NULL
    )
  `).run();
}

it("GET /api/diag/ops-incidents returns persisted incidents", async () => {
  await seedOpsIncidentTables(env.REPORTS_DB);
  const token = await getToken();

  const res = await app.request("/api/diag/ops-incidents", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.items[0].incident_id).toBe("opsinc_test");
  expect(data.items[0].confirmed_findings).toEqual(["Workflow result is failure."]);
});

it("GET /api/diag/ops-incidents/analytics returns counts", async () => {
  await seedOpsIncidentTables(env.REPORTS_DB);
  const token = await getToken();

  const res = await app.request("/api/diag/ops-incidents/analytics", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.total).toBe(1);
  expect(data.by_type.failed_ingestion).toBe(1);
});
```

- [ ] **Step 2: Add Worker helpers**

Modify `server/routes/diagnostics.ts`:

```ts
function parseJsonArray(value: string | null): unknown[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function mapOpsIncident(row: any) {
  return {
    incident_id: row.incident_id,
    trigger_source: row.trigger_source,
    run_id: row.run_id ?? null,
    run_attempt: row.run_attempt ?? null,
    session_id: row.session_id ?? null,
    incident_type: row.incident_type,
    status: row.status,
    persistence_status: row.persistence_status,
    model_version: row.model_version,
    detector_version: row.detector_version,
    confidence: row.confidence,
    confirmed_findings: parseJsonArray(row.confirmed_findings_json),
    likely_causes: parseJsonArray(row.likely_causes_json),
    unknowns: parseJsonArray(row.unknowns_json),
    recommended_next_actions: parseJsonArray(row.recommended_next_actions_json),
    unsafe_actions: parseJsonArray(row.unsafe_actions_json),
    evidence_refs: parseJsonArray(row.evidence_refs_json),
    created_at: row.created_at,
    updated_at: row.updated_at,
    resolved_at: row.resolved_at ?? null,
  };
}
```

- [ ] **Step 3: Add Worker list/detail/analytics routes**

Add to `server/routes/diagnostics.ts`, before JavDB session routes or before dynamic routes if introduced later:

```ts
diagnosticsRoutes.get("/ops-incidents", async (c) => {
  const status = c.req.query("status");
  const incidentType = c.req.query("incident_type");
  const confidence = c.req.query("confidence");
  const limit = Math.max(1, Math.min(100, parseInt(c.req.query("limit") ?? "50", 10) || 50));
  const clauses: string[] = [];
  const bindings: (string | number)[] = [];
  if (status) {
    clauses.push("status = ?");
    bindings.push(status);
  }
  if (incidentType) {
    clauses.push("incident_type = ?");
    bindings.push(incidentType);
  }
  if (confidence) {
    clauses.push("confidence = ?");
    bindings.push(confidence);
  }
  const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = await c.env.REPORTS_DB
    .prepare(`SELECT * FROM OpsIncidents ${where} ORDER BY created_at DESC LIMIT ?`)
    .bind(...bindings, limit)
    .all();
  return c.json({ items: rows.results.map(mapOpsIncident) });
});

diagnosticsRoutes.get("/ops-incidents/analytics", async (c) => {
  const rows = await c.env.REPORTS_DB.prepare("SELECT incident_type, status, confidence FROM OpsIncidents LIMIT 500").all<any>();
  const byType: Record<string, number> = {};
  const byStatus: Record<string, number> = {};
  const byConfidence: Record<string, number> = {};
  for (const row of rows.results) {
    byType[row.incident_type] = (byType[row.incident_type] ?? 0) + 1;
    byStatus[row.status] = (byStatus[row.status] ?? 0) + 1;
    byConfidence[row.confidence] = (byConfidence[row.confidence] ?? 0) + 1;
  }
  return c.json({
    total: rows.results.length,
    by_type: byType,
    by_status: byStatus,
    by_confidence: byConfidence,
    open_high_confidence: rows.results.filter((row) => row.status === "open" && row.confidence === "high").length,
  });
});

diagnosticsRoutes.get("/ops-incidents/:incident_id", async (c) => {
  const incidentId = c.req.param("incident_id");
  const row = await c.env.REPORTS_DB
    .prepare("SELECT * FROM OpsIncidents WHERE incident_id = ?")
    .bind(incidentId)
    .first();
  if (!row) throw new HTTPException(404, { message: "Incident not found" });
  return c.json(mapOpsIncident(row));
});
```

Use the same output names as the Python API.

- [ ] **Step 4: Run Worker tests**

Run from the Web repo:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
```

Expected: pass.

---

## Task 8: Frontend API Client And UI

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/src/api/diagnostics.ts`
- Create: `../JAVDB_AutoSpider_Web/src/pages/diagnostics/OpsIncidentsPage.vue`
- Modify: `../JAVDB_AutoSpider_Web/src/router/routes.ts`
- Modify: `../JAVDB_AutoSpider_Web/src/components/layout/Sidebar.vue`
- Modify: `../JAVDB_AutoSpider_Web/src/i18n/locales/en.json`
- Modify: `../JAVDB_AutoSpider_Web/src/i18n/locales/zh-CN.json`
- Modify: `../JAVDB_AutoSpider_Web/src/i18n/locales/ja.json`
- Create: `../JAVDB_AutoSpider_Web/tests/unit/ops-incidents-api.spec.ts`
- Create: `../JAVDB_AutoSpider_Web/tests/e2e/ops-incidents.spec.ts`

- [ ] **Step 1: Add frontend API tests**

Create `tests/unit/ops-incidents-api.spec.ts` in the Web repo:

```ts
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { getOpsIncidentAnalytics, listOpsIncidents } from '@/api/diagnostics'

describe('ops incidents diagnostics API', () => {
  it('lists incidents with filters', async () => {
    const spy = vi.spyOn(http, 'get').mockResolvedValueOnce({ data: { items: [] } })

    const result = await listOpsIncidents({ incident_type: 'failed_ingestion', confidence: 'low' })

    expect(result.items).toEqual([])
    expect(spy).toHaveBeenCalledWith('/api/diag/ops-incidents', {
      params: { incident_type: 'failed_ingestion', confidence: 'low' },
    })
  })

  it('loads analytics', async () => {
    vi.spyOn(http, 'get').mockResolvedValueOnce({
      data: {
        total: 1,
        by_type: { failed_ingestion: 1 },
        by_status: { open: 1 },
        by_confidence: { low: 1 },
        open_high_confidence: 0,
      },
    })

    const result = await getOpsIncidentAnalytics()

    expect(result.total).toBe(1)
    expect(result.by_type.failed_ingestion).toBe(1)
  })
})
```

- [ ] **Step 2: Add API client contracts**

Modify `src/api/diagnostics.ts` in the Web repo:

```ts
export interface EvidenceRef {
  kind: string
  ref: string
  label?: string | null
}

export interface OpsIncident {
  incident_id: string
  trigger_source: string
  run_id?: string | null
  run_attempt?: number | null
  session_id?: string | null
  incident_type: string
  status: string
  persistence_status: string
  model_version: string
  detector_version: string
  confidence: string
  confirmed_findings: string[]
  likely_causes: string[]
  unknowns: string[]
  recommended_next_actions: string[]
  unsafe_actions: string[]
  evidence_refs: EvidenceRef[]
  created_at: string
  updated_at: string
  resolved_at?: string | null
}

export interface OpsIncidentListResponse {
  items: OpsIncident[]
}

export interface OpsIncidentAnalytics {
  total: number
  by_type: Record<string, number>
  by_status: Record<string, number>
  by_confidence: Record<string, number>
  open_high_confidence: number
}

export interface ListOpsIncidentParams {
  status?: string
  incident_type?: string
  confidence?: string
  run_id?: string
  session_id?: string
}

export async function listOpsIncidents(params: ListOpsIncidentParams = {}): Promise<OpsIncidentListResponse> {
  const { data } = await http.get<OpsIncidentListResponse>('/api/diag/ops-incidents', { params })
  return data
}

export async function getOpsIncident(incidentId: string): Promise<OpsIncident> {
  const { data } = await http.get<OpsIncident>(`/api/diag/ops-incidents/${incidentId}`)
  return data
}

export async function getOpsIncidentAnalytics(): Promise<OpsIncidentAnalytics> {
  const { data } = await http.get<OpsIncidentAnalytics>('/api/diag/ops-incidents/analytics')
  return data
}
```

- [ ] **Step 3: Create incident history page**

Create `src/pages/diagnostics/OpsIncidentsPage.vue` in the Web repo. The page should:

- Load analytics and the first page of incidents on mount.
- Provide compact filters for status, incident type, and confidence.
- Render a table with created time, type, status, confidence, run id, session id, and first confirmed finding.
- Render detail in a drawer or side panel when an incident is selected.
- Render `confirmed_findings`, `likely_causes`, `unknowns`, `recommended_next_actions`, `unsafe_actions`, and `evidence_refs` as separate dense sections.
- Include refresh and copy-id controls only. Do not include rollback, rerun, delete, apply, resolve, or acknowledge buttons.

Use existing Naive UI components already used in diagnostics pages: `NCard`, `NAlert`, `NButton`, `NDataTable`, `NDescriptions`, `NDescriptionsItem`, `NDrawer`, `NDrawerContent`, `NSelect`, `NSpace`, `NSpin`, and `NTag`.

- [ ] **Step 4: Add route and sidebar entry**

Modify `src/router/routes.ts`:

```ts
{
  path: '/diag/ops-incidents',
  name: 'diag-ops-incidents',
  component: () => import('@/pages/diagnostics/OpsIncidentsPage.vue'),
  meta: { requiresAuth: true },
},
```

Modify the diagnostics children in `src/components/layout/Sidebar.vue`:

```ts
{ label: t('nav.opsIncidents'), key: 'opsIncidents' },
```

Add the route map entry:

```ts
opsIncidents: '/diag/ops-incidents',
```

- [ ] **Step 5: Add i18n strings**

Add keys to `src/i18n/locales/en.json`:

```json
"opsIncidents": "Ops Incidents"
```

Add a `diag.opsIncidents` object with labels for title, subtitle, filters, analytics, empty state, findings, causes, unknowns, next actions, unsafe actions, evidence, and copy id.

Mirror the same key structure in `zh-CN.json` and `ja.json`.

- [ ] **Step 6: Add E2E smoke**

Create `tests/e2e/ops-incidents.spec.ts` in the Web repo:

```ts
import { test, expect } from '@playwright/test'
import { loginViaUi, markOnboarded, resetBackend } from './fixtures/auth'

test.describe('Ops incidents diagnostics', () => {
  test.beforeEach(async ({ request, page }) => {
    await resetBackend(request)
    await markOnboarded(request, page)
    await page.route('**/api/diag/ops-incidents', async (route) => {
      await route.fulfill({
        json: {
          items: [{
            incident_id: 'opsinc_test',
            trigger_source: 'workflow_failure',
            run_id: '100',
            run_attempt: 1,
            session_id: '20260527T120000.000000Z-0001-0001',
            incident_type: 'failed_ingestion',
            status: 'open',
            persistence_status: 'd1_written',
            model_version: 'fallback-v1',
            detector_version: 'detectors-v1',
            confidence: 'low',
            confirmed_findings: ['Workflow result is failure.'],
            likely_causes: [],
            unknowns: [],
            recommended_next_actions: ['Inspect logs.'],
            unsafe_actions: [],
            evidence_refs: [],
            created_at: '2026-05-27T00:00:00Z',
            updated_at: '2026-05-27T00:00:00Z',
            resolved_at: null,
          }],
        },
      })
    })
    await page.route('**/api/diag/ops-incidents/analytics', async (route) => {
      await route.fulfill({
        json: {
          total: 1,
          by_type: { failed_ingestion: 1 },
          by_status: { open: 1 },
          by_confidence: { low: 1 },
          open_high_confidence: 0,
        },
      })
    })
  })

  test('shows incident history and detail', async ({ page }) => {
    await loginViaUi(page)
    await page.goto('/diag/ops-incidents')
    await expect(page.getByText('opsinc_test')).toBeVisible()
    await expect(page.getByText('Workflow result is failure.')).toBeVisible()
  })
})
```

- [ ] **Step 7: Run frontend tests**

Run from the Web repo:

```bash
npm run test:unit -- tests/unit/ops-incidents-api.spec.ts
npm run test:e2e -- tests/e2e/ops-incidents.spec.ts
```

Expected: pass.

---

## Task 9: Documentation And Workflow Review

**Files:**
- Modify: `docs/handbook/en/ops/troubleshooting.md`
- Modify: `docs/handbook/zh/ops/troubleshooting.md`

- [ ] **Step 1: Update English troubleshooting**

Add to the ADR-026 section in `docs/handbook/en/ops/troubleshooting.md`:

````markdown
### Incident History And Similarity

The diagnostics API exposes persisted incidents at:

```bash
curl -H "Authorization: Bearer <token>" \
  "<api>/api/diag/ops-incidents?status=open&incident_type=failed_ingestion"
```

The web UI shows the same read-only records under **Diagnostics -> Ops Incidents**. Similar incidents are based on deterministic feature overlap: incident type, confidence, trigger source, text tokens, unsafe action tokens, and evidence kinds. The score is explainable and does not use embeddings in Phase 2.

The page is for investigation only. It cannot roll back, rerun, delete, apply drift fixes, or mark recovery work resolved.
````

- [ ] **Step 2: Update Chinese troubleshooting**

Add the matching section to `docs/handbook/zh/ops/troubleshooting.md`:

````markdown
### Incident 历史与相似事件

diagnostics API 会通过下面的接口暴露已持久化 incident：

```bash
curl -H "Authorization: Bearer <token>" \
  "<api>/api/diag/ops-incidents?status=open&incident_type=failed_ingestion"
```

Web UI 会在 **诊断 -> Ops Incidents** 展示同样的只读记录。相似 incident 基于确定性 feature overlap：incident type、confidence、trigger source、text tokens、unsafe action tokens 和 evidence kinds。Phase 2 的 score 是可解释的，不使用 embedding。

这个页面只用于调查。它不能 rollback、rerun、delete、apply drift fix，也不能把 recovery work 标记为 resolved。
````

- [ ] **Step 3: Review GitHub Actions impact**

No workflow YAML change is expected for Phase 2 because Phase 1 already creates incidents during failed/cancelled runs. Verify this with:

```bash
rg -n "diagnose_run|OPS_DIAGNOSIS_JSON|OpsIncidents" .github/workflows docs/design/ADR-026-AI-Operations-Diagnosis
```

Expected: Phase 1 workflow wiring remains the only workflow integration.

- [ ] **Step 4: Run documentation checks**

Run:

```bash
git diff --check -- \
  docs/design/ADR-026-AI-Operations-Diagnosis/IMP-ADR026-02-ai-ops-diagnosis-history-analytics.md \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md
```

Expected: no output.

---

## Task 10: Verification And Closeout

- [ ] **Step 1: Run Python tests**

Run:

```bash
pytest \
  tests/unit/test_ops_diagnosis_features.py \
  tests/unit/test_ops_diagnosis_similarity.py \
  tests/unit/test_ops_diagnosis_analytics.py \
  tests/unit/test_ops_incident_repo.py \
  tests/unit/test_ops_diagnostics_api.py \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run Web tests**

Run from `../JAVDB_AutoSpider_Web`:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
npm run test:unit -- tests/unit/ops-incidents-api.spec.ts
npm run test:e2e -- tests/e2e/ops-incidents.spec.ts
```

Expected: all pass.

- [ ] **Step 3: Run static checks**

Run from the main repo:

```bash
python3 -m compileall javdb/ops/diagnosis apps/api/routers/diagnostics.py apps/api/schemas/diagnostics.py
git diff --check
```

Run from the Web repo:

```bash
npm run typecheck
npm run lint
```

Expected: no failures.

- [ ] **Step 4: Manual UI smoke**

Start the Python API and Web dev server with local test data, then open:

```text
http://localhost:5173/diag/ops-incidents
```

Expected:

- Incident table loads without layout shift.
- Filters update the list.
- Detail panel shows structured findings, likely causes, unknowns, next actions, unsafe actions, and evidence.
- No remediation buttons are present.

- [ ] **Step 5: Commit**

Commit only the Phase 2 source, tests, workflow/doc updates, and Web repo changes. Do not commit `reports/` data files.

```bash
git add \
  javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql \
  javdb/storage/db/_db_migrations.py \
  javdb/ops/diagnosis \
  javdb/storage/repos/ops_incident_repo.py \
  apps/api/schemas/diagnostics.py \
  apps/api/routers/diagnostics.py \
  tests/unit/test_ops_diagnosis_features.py \
  tests/unit/test_ops_diagnosis_similarity.py \
  tests/unit/test_ops_diagnosis_analytics.py \
  tests/unit/test_ops_incident_repo.py \
  tests/unit/test_ops_diagnostics_api.py \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md
git commit -m "feat(ops): add incident history analytics"
```

Commit the Web repo changes separately:

```bash
cd ../JAVDB_AutoSpider_Web
git add \
  server/routes/diagnostics.ts \
  server/__tests__/diagnostics-routes.test.ts \
  src/api/diagnostics.ts \
  src/pages/diagnostics/OpsIncidentsPage.vue \
  src/router/routes.ts \
  src/components/layout/Sidebar.vue \
  src/i18n/locales/en.json \
  src/i18n/locales/zh-CN.json \
  src/i18n/locales/ja.json \
  tests/unit/ops-incidents-api.spec.ts \
  tests/e2e/ops-incidents.spec.ts
git commit -m "feat(diagnostics): add ops incident history"
```
