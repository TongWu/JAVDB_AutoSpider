# IMP-ADR026-01: ADR-026 Phase 1 - Read-Only AI Operations Diagnosis

**Status:** Completed — delivered and verified on 2026-05-27.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-026 Phase 1 by adding a persistent, read-only operations diagnosis assistant that collects deterministic evidence, runs detector-first diagnosis, stores incident summaries, and exposes the result through CLI, API, and short email/workflow advisories.

**Architecture:** Add an `ops_diagnosis` domain package under `javdb/ops/` with dataclasses, collectors, detectors, an AI synthesis adapter, persistence, and JSONL fallback. Store incident records in the canonical D1 `reports` database and mirror to SQLite only for local debugging. Keep all mutation-capable recovery tools outside this assistant; Phase 1 only recommends operator actions.

**Tech Stack:** Python 3.11, Cloudflare D1 via existing `get_db()`, pytest, FastAPI/Pydantic, GitHub Actions YAML, JSON/JSONL, Markdown docs.

**Source spec:** [ADR-026](ADR-026-ai-operations-diagnosis.md), D1-D10.

**Non-negotiable:** This phase must not automatically roll back sessions, rerun workflows, modify D1 based on model output, delete qBittorrent tasks, or mark recovery events resolved.

## Table Of Contents

- [Task 1: D1 Incident Schema](#task-1-d1-incident-schema)
- [Task 2: Incident Models And JSONL Fallback](#task-2-incident-models-and-jsonl-fallback)
- [Task 3: Repository And D1-First Persistence](#task-3-repository-and-d1-first-persistence)
- [Task 4: Deterministic Bundle Collector And Detectors](#task-4-deterministic-bundle-collector-and-detectors)
- [Task 5: AI Synthesis Adapter And Service Orchestration](#task-5-ai-synthesis-adapter-and-service-orchestration)
- [Task 6: Operator CLI](#task-6-operator-cli)
- [Task 7: API Read Surface](#task-7-api-read-surface)
- [Task 8: Email And Workflow Integration](#task-8-email-and-workflow-integration)
- [Task 9: Configuration And Documentation](#task-9-configuration-and-documentation)
- [Task 10: Verification And Closeout](#task-10-verification-and-closeout)
- [Definition Of Done](#definition-of-done)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/migrations/d1/2026_05_27_add_ops_incidents.sql` | D1-first `OpsIncidents` schema for persisted diagnosis summaries. |
| Create | `javdb/ops/__init__.py` | Package marker for operator-facing service code. |
| Create | `javdb/ops/diagnosis/__init__.py` | Public exports for the diagnosis package. |
| Create | `javdb/ops/diagnosis/models.py` | Typed incident bundle, detector output, diagnosis result, and persistence records. |
| Create | `javdb/ops/diagnosis/jsonl_store.py` | JSONL fallback writer/reader for degraded persistence. |
| Create | `javdb/storage/repos/ops_incident_repo.py` | Repository for D1/SQLite `OpsIncidents` rows. |
| Create | `javdb/ops/diagnosis/persistence.py` | D1-first persistence with JSONL fallback status. |
| Create | `javdb/ops/diagnosis/collectors.py` | Deterministic incident bundle collector from run/session/log/outbox inputs. |
| Create | `javdb/ops/diagnosis/detectors.py` | Rule-based detectors for session, drift, recovery outbox, rollback safety, and qB side-effect facts. |
| Create | `javdb/ops/diagnosis/ai.py` | AI synthesis interface plus offline deterministic fallback. |
| Create | `javdb/ops/diagnosis/service.py` | Orchestrates collect -> detect -> synthesize -> persist. |
| Create | `apps/cli/ops/diagnose_run.py` | Operator CLI entry point. |
| Modify | `apps/cli/ops/README.md` | Document the new CLI. |
| Modify | `apps/api/schemas/diagnostics.py` | Add incident response schemas. |
| Modify | `apps/api/routers/diagnostics.py` | Add read-only incident list/get endpoints. |
| Modify | `javdb/integrations/notify/email.py` | Add short AI diagnosis advisory from persisted incident records when a diagnosis JSON path is provided. |
| Modify | `.github/workflows/DailyIngestion.yml` | Run read-only diagnosis on failed/cancelled runs and pass summary to email. |
| Modify | `.github/workflows/AdHocIngestion.yml` | Same as DailyIngestion. |
| Modify | `.github/workflows/TestIngestion.yml` | Add a dry-run/manual diagnostic smoke path without email integration. |
| Modify | `config.py.example` | Add AI ops diagnosis configuration keys. |
| Modify | `javdb/infra/config_generator.py` | Generate the new config keys from GitHub variables/secrets. |
| Create | `tests/unit/test_ops_diagnosis_models.py` | Model serialization and safety-shape tests. |
| Create | `tests/unit/test_ops_incident_repo.py` | Repository and persistence tests. |
| Create | `tests/unit/test_ops_diagnosis_collectors.py` | Bundle collector tests. |
| Create | `tests/unit/test_ops_diagnosis_detectors.py` | Detector tests. |
| Create | `tests/unit/test_ops_diagnosis_service.py` | End-to-end service orchestration tests with fake dependencies. |
| Create | `tests/unit/test_ops_diagnose_run_cli.py` | CLI behavior and exit-code tests. |
| Create | `tests/unit/test_ops_diagnostics_api.py` | API response tests. |
| Create | `tests/unit/test_email_ops_diagnosis.py` | Email short-advisory tests. |
| Modify | `tests/unit/test_workflow_resolve_write_mode.py` | Workflow guard tests for diagnosis job wiring. |
| Modify | `docs/handbook/en/ops/troubleshooting.md` | Operator-facing usage section. |
| Modify | `docs/handbook/zh/ops/troubleshooting.md` | Chinese mirror of the usage section. |
| Modify | `docs/handbook/en/ops/d1-rollback.md` | Cross-link diagnosis assistant from rollback SOP. |
| Modify | `docs/handbook/zh/ops/d1-rollback.md` | Chinese mirror link. |

## Scope Boundaries

- Do not add auto-apply or rollback execution to the diagnosis package.
- Do not parse full raw logs into D1. Store only selected snippets and references.
- Do not introduce a new external service.
- Do not change existing ADR-009 `drift_diagnose --apply` semantics.
- Do not change qBittorrent uploader behavior or file-filter behavior.
- Do not make AI model availability required for diagnosis. A deterministic fallback must produce a useful structured advisory when AI config is missing.

---

## Task 1: D1 Incident Schema

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_ops_incidents.sql`

- [ ] **Step 1: Create the migration**

```sql
-- 2026-05-27: Add OpsIncidents table (ADR-026 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ops_incidents.sql
--
-- OpsIncidents stores structured diagnosis summaries and evidence pointers.
-- It does not store full raw workflow logs.

CREATE TABLE IF NOT EXISTS OpsIncidents (
  incident_id TEXT PRIMARY KEY,
  trigger_source TEXT NOT NULL,
  run_id TEXT,
  run_attempt INTEGER,
  session_id TEXT,
  incident_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'acknowledged', 'resolved', 'dismissed')),
  persistence_status TEXT NOT NULL DEFAULT 'd1_written',
  model_version TEXT NOT NULL,
  detector_version TEXT NOT NULL,
  bundle_schema_version TEXT NOT NULL,
  confidence TEXT NOT NULL DEFAULT 'low'
    CHECK (confidence IN ('low', 'medium', 'high')),
  confirmed_findings_json TEXT NOT NULL DEFAULT '[]',
  likely_causes_json TEXT NOT NULL DEFAULT '[]',
  unknowns_json TEXT NOT NULL DEFAULT '[]',
  recommended_next_actions_json TEXT NOT NULL DEFAULT '[]',
  unsafe_actions_json TEXT NOT NULL DEFAULT '[]',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_created
  ON OpsIncidents(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_run
  ON OpsIncidents(run_id, run_attempt);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_session
  ON OpsIncidents(session_id);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_status_type
  ON OpsIncidents(status, incident_type);
```

- [ ] **Step 2: Apply to D1**

```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_27_add_ops_incidents.sql
```

Expected: migration succeeds and creates `OpsIncidents`.

- [ ] **Step 3: Verify D1 schema**

```bash
wrangler d1 execute javdb-reports --remote \
  --command="SELECT name FROM sqlite_master WHERE type='table' AND name='OpsIncidents';"
```

Expected: one row with `OpsIncidents`.

- [ ] **Step 4: Re-align local SQLite mirror**

```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

Expected: local `reports/reports.db` contains `OpsIncidents`.

---

## Task 2: Incident Models And JSONL Fallback

**Files:**
- Create: `javdb/ops/__init__.py`
- Create: `javdb/ops/diagnosis/__init__.py`
- Create: `javdb/ops/diagnosis/models.py`
- Create: `javdb/ops/diagnosis/jsonl_store.py`
- Create: `tests/unit/test_ops_diagnosis_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/unit/test_ops_diagnosis_models.py`:

```python
from __future__ import annotations

import json

from javdb.ops.diagnosis.models import (
    DiagnosisResult,
    EvidenceRef,
    IncidentBundle,
    OpsIncidentRecord,
    build_incident_id,
)


def test_incident_id_is_stable_for_run_attempt_session():
    first = build_incident_id(
        trigger_source="workflow_failure",
        run_id="123",
        run_attempt=2,
        session_id="20260527T120000.000000Z-abcd-0001",
        incident_type="failed_ingestion",
    )
    second = build_incident_id(
        trigger_source="workflow_failure",
        run_id="123",
        run_attempt=2,
        session_id="20260527T120000.000000Z-abcd-0001",
        incident_type="failed_ingestion",
    )

    assert first == second
    assert first.startswith("opsinc_")


def test_record_serializes_json_fields_as_compact_json_strings():
    result = DiagnosisResult(
        incident_type="d1_drift",
        confidence="medium",
        confirmed_findings=["D1 pending orphan exists"],
        likely_causes=["D1 write failed after SQLite success"],
        unknowns=["workflow logs unavailable"],
        recommended_next_actions=["Run drift diagnose CLI"],
        unsafe_actions=["Do not force rollback committed session"],
        evidence_refs=[EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/d1-rollback.md")],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        run_id="123",
        run_attempt=1,
        session_id="sid",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        bundle_schema_version="bundle-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result)

    assert record.incident_id.startswith("opsinc_")
    assert json.loads(record.confirmed_findings_json) == ["D1 pending orphan exists"]
    assert json.loads(record.evidence_refs_json) == [
        {"kind": "runbook", "ref": "docs/handbook/en/ops/d1-rollback.md", "label": None}
    ]
    assert record.status == "open"
    assert record.persistence_status == "not_written"
```

Expected failure before implementation: `ModuleNotFoundError: No module named 'javdb.ops'`.

- [ ] **Step 2: Implement model dataclasses**

Create `javdb/ops/__init__.py`:

```python
"""Operator-facing service packages."""
```

Create `javdb/ops/diagnosis/__init__.py`:

```python
"""Read-only operations diagnosis assistant."""

from javdb.ops.diagnosis.models import (
    DiagnosisResult,
    EvidenceRef,
    IncidentBundle,
    OpsIncidentRecord,
)

__all__ = [
    "DiagnosisResult",
    "EvidenceRef",
    "IncidentBundle",
    "OpsIncidentRecord",
]
```

Create `javdb/ops/diagnosis/models.py`:

```python
"""Typed contracts for ADR-026 operations diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal


Confidence = Literal["low", "medium", "high"]
IncidentStatus = Literal["open", "acknowledged", "resolved", "dismissed"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_incident_id(
    *,
    trigger_source: str,
    run_id: str | None,
    run_attempt: int | None,
    session_id: str | None,
    incident_type: str,
) -> str:
    raw = "|".join([
        trigger_source or "",
        run_id or "",
        str(run_attempt or ""),
        session_id or "",
        incident_type or "",
    ])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"opsinc_{digest}"


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    ref: str
    label: str | None = None


@dataclass(frozen=True)
class IncidentBundle:
    trigger_source: str
    run_id: str | None = None
    run_attempt: int | None = None
    session_id: str | None = None
    workflow_name: str | None = None
    workflow_result: str | None = None
    bundle_schema_version: str = "bundle-v1"
    session_status: str | None = None
    drift_verdict: str | None = None
    recovery_outbox_summary: dict[str, Any] = field(default_factory=dict)
    rollback_safety: str | None = None
    qb_side_effects: dict[str, Any] = field(default_factory=dict)
    log_snippets: list[str] = field(default_factory=list)
    email_summary: str | None = None
    runbook_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisResult:
    incident_type: str
    confidence: Confidence
    confirmed_findings: list[str]
    likely_causes: list[str]
    unknowns: list[str]
    recommended_next_actions: list[str]
    unsafe_actions: list[str]
    evidence_refs: list[EvidenceRef]
    model_version: str
    detector_version: str


@dataclass(frozen=True)
class OpsIncidentRecord:
    incident_id: str
    trigger_source: str
    run_id: str | None
    run_attempt: int | None
    session_id: str | None
    incident_type: str
    status: IncidentStatus
    persistence_status: str
    model_version: str
    detector_version: str
    bundle_schema_version: str
    confidence: Confidence
    confirmed_findings_json: str
    likely_causes_json: str
    unknowns_json: str
    recommended_next_actions_json: str
    unsafe_actions_json: str
    evidence_refs_json: str
    created_at: str
    updated_at: str
    resolved_at: str | None = None

    @classmethod
    def from_bundle_and_result(
        cls,
        bundle: IncidentBundle,
        result: DiagnosisResult,
    ) -> "OpsIncidentRecord":
        now = utc_now_iso()
        incident_id = build_incident_id(
            trigger_source=bundle.trigger_source,
            run_id=bundle.run_id,
            run_attempt=bundle.run_attempt,
            session_id=bundle.session_id,
            incident_type=result.incident_type,
        )
        return cls(
            incident_id=incident_id,
            trigger_source=bundle.trigger_source,
            run_id=bundle.run_id,
            run_attempt=bundle.run_attempt,
            session_id=bundle.session_id,
            incident_type=result.incident_type,
            status="open",
            persistence_status="not_written",
            model_version=result.model_version,
            detector_version=result.detector_version,
            bundle_schema_version=bundle.bundle_schema_version,
            confidence=result.confidence,
            confirmed_findings_json=_json_dumps(result.confirmed_findings),
            likely_causes_json=_json_dumps(result.likely_causes),
            unknowns_json=_json_dumps(result.unknowns),
            recommended_next_actions_json=_json_dumps(result.recommended_next_actions),
            unsafe_actions_json=_json_dumps(result.unsafe_actions),
            evidence_refs_json=_json_dumps([asdict(ref) for ref in result.evidence_refs]),
            created_at=now,
            updated_at=now,
        )

    def with_persistence_status(self, status: str) -> "OpsIncidentRecord":
        data = asdict(self)
        data["persistence_status"] = status
        data["updated_at"] = utc_now_iso()
        return OpsIncidentRecord(**data)
```

- [ ] **Step 3: Implement JSONL fallback store**

Create `javdb/ops/diagnosis/jsonl_store.py`:

```python
"""JSONL fallback persistence for ADR-026 diagnosis incidents."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Iterable

from javdb.ops.diagnosis.models import OpsIncidentRecord


def default_incident_jsonl_path(reports_dir: str | None = None) -> Path:
    root = reports_dir or os.environ.get("REPORTS_DIR", "reports")
    return Path(root) / "ops" / "ops_incidents.jsonl"


def append_incident_jsonl(record: OpsIncidentRecord, path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else default_incident_jsonl_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":")) + "\n")
    return target


def read_incident_jsonl(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict] = []
    with target.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_ops_diagnosis_models.py -v
```

Expected: pass.

---

## Task 3: Repository And D1-First Persistence

**Files:**
- Create: `javdb/storage/repos/ops_incident_repo.py`
- Create: `javdb/ops/diagnosis/persistence.py`
- Create: `tests/unit/test_ops_incident_repo.py`

- [ ] **Step 1: Write failing persistence tests**

Create `tests/unit/test_ops_incident_repo.py`:

```python
from __future__ import annotations

import json
import sqlite3

from javdb.ops.diagnosis.models import DiagnosisResult, EvidenceRef, IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.persistence import persist_incident
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


DDL = """
CREATE TABLE OpsIncidents (
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
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(DDL)
    return conn


def _record():
    bundle = IncidentBundle(trigger_source="manual_cli", run_id="42", run_attempt=1, session_id="sid")
    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="low",
        confirmed_findings=["workflow failed"],
        likely_causes=[],
        unknowns=["log artifact missing"],
        recommended_next_actions=["inspect logs"],
        unsafe_actions=["do not force rollback"],
        evidence_refs=[EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/troubleshooting.md")],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    return OpsIncidentRecord.from_bundle_and_result(bundle, result)


def test_repo_upserts_and_reads_incident():
    conn = _conn()
    repo = OpsIncidentRepo(conn)
    record = _record()

    repo.upsert(record.with_persistence_status("d1_written"))
    fetched = repo.get(record.incident_id)

    assert fetched is not None
    assert fetched.incident_id == record.incident_id
    assert fetched.persistence_status == "d1_written"
    assert json.loads(fetched.confirmed_findings_json) == ["workflow failed"]


def test_repo_lists_newest_first():
    conn = _conn()
    repo = OpsIncidentRepo(conn)
    first = _record().with_persistence_status("d1_written")
    second = OpsIncidentRecord(
        **{**first.__dict__, "incident_id": "opsinc_second", "created_at": "2099-01-01T00:00:00Z"}
    )

    repo.upsert(first)
    repo.upsert(second)
    items = repo.list(limit=10)

    assert [item.incident_id for item in items] == ["opsinc_second", first.incident_id]


def test_persist_incident_falls_back_to_jsonl_when_d1_fails(tmp_path):
    class FailingRepo:
        def upsert(self, record):
            raise RuntimeError("d1 unavailable")

    record = _record()
    path = tmp_path / "ops_incidents.jsonl"

    persisted = persist_incident(record, repo=FailingRepo(), jsonl_path=path)

    assert persisted.persistence_status == "d1_failed_jsonl_written"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["incident_id"] == record.incident_id
```

- [ ] **Step 2: Implement repository**

Create `javdb/storage/repos/ops_incident_repo.py`:

```python
"""Repository for ADR-026 OpsIncidents rows."""

from __future__ import annotations

import sqlite3

from javdb.ops.diagnosis.models import OpsIncidentRecord


_COLUMNS = (
    "incident_id",
    "trigger_source",
    "run_id",
    "run_attempt",
    "session_id",
    "incident_type",
    "status",
    "persistence_status",
    "model_version",
    "detector_version",
    "bundle_schema_version",
    "confidence",
    "confirmed_findings_json",
    "likely_causes_json",
    "unknowns_json",
    "recommended_next_actions_json",
    "unsafe_actions_json",
    "evidence_refs_json",
    "created_at",
    "updated_at",
    "resolved_at",
)


def _row_to_record(row: sqlite3.Row) -> OpsIncidentRecord:
    return OpsIncidentRecord(**{column: row[column] for column in _COLUMNS})


class OpsIncidentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: OpsIncidentRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        update_columns = [column for column in _COLUMNS if column != "incident_id"]
        updates = ", ".join([f"{column}=excluded.{column}" for column in update_columns])
        self._conn.execute(
            f"""
            INSERT INTO OpsIncidents ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_id) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, incident_id: str) -> OpsIncidentRecord | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OpsIncidents WHERE incident_id = ?",
            [incident_id],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list(
        self,
        *,
        status: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[OpsIncidentRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        sql = f"SELECT {', '.join(_COLUMNS)} FROM OpsIncidents"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [_row_to_record(row) for row in self._conn.execute(sql, params).fetchall()]
```

- [ ] **Step 3: Implement D1-first persistence wrapper**

Create `javdb/ops/diagnosis/persistence.py`:

```python
"""D1-first incident persistence with JSONL fallback."""

from __future__ import annotations

from pathlib import Path

from javdb.ops.diagnosis.jsonl_store import append_incident_jsonl
from javdb.ops.diagnosis.models import OpsIncidentRecord
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


def persist_incident(
    record: OpsIncidentRecord,
    *,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
) -> OpsIncidentRecord:
    d1_record = record.with_persistence_status("d1_written")
    try:
        if repo is not None:
            repo.upsert(d1_record)
        else:
            with get_db(REPORTS_DB_PATH) as conn:
                OpsIncidentRepo(conn).upsert(d1_record)
        return d1_record
    except Exception:
        fallback_record = record.with_persistence_status("d1_failed_jsonl_written")
        append_incident_jsonl(fallback_record, jsonl_path)
        return fallback_record
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_ops_incident_repo.py -v
```

Expected: pass.

---

## Task 4: Deterministic Bundle Collector And Detectors

**Files:**
- Create: `javdb/ops/diagnosis/collectors.py`
- Create: `javdb/ops/diagnosis/detectors.py`
- Create: `tests/unit/test_ops_diagnosis_collectors.py`
- Create: `tests/unit/test_ops_diagnosis_detectors.py`

- [ ] **Step 1: Write collector tests**

Create `tests/unit/test_ops_diagnosis_collectors.py`:

```python
from __future__ import annotations

from pathlib import Path

from javdb.ops.diagnosis.collectors import collect_incident_bundle


def test_collect_bundle_reads_capped_log_snippets(tmp_path):
    log = tmp_path / "pipeline.log"
    log.write_text("ok\nERROR failed spider\nWARNING retrying\n", encoding="utf-8")

    bundle = collect_incident_bundle(
        trigger_source="manual_cli",
        run_id="123",
        run_attempt=1,
        session_id="sid",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        log_paths=[log],
    )

    assert bundle.trigger_source == "manual_cli"
    assert bundle.workflow_result == "failure"
    assert bundle.log_snippets == ["pipeline.log: ERROR failed spider", "pipeline.log: WARNING retrying"]
    assert bundle.runbook_refs
```

- [ ] **Step 2: Write detector tests**

Create `tests/unit/test_ops_diagnosis_detectors.py`:

```python
from __future__ import annotations

from javdb.ops.diagnosis.detectors import detect_incident
from javdb.ops.diagnosis.models import IncidentBundle


def test_detector_classifies_failed_ingestion_with_unknown_rollback_when_session_missing():
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id=None,
    )

    result = detect_incident(bundle)

    assert result.incident_type == "failed_ingestion"
    assert "Workflow result is failure." in result.confirmed_findings
    assert "Session id is missing; rollback safety cannot be proven." in result.unknowns
    assert "Do not run forced rollback without locating the owning session." in result.unsafe_actions


def test_detector_classifies_d1_drift_from_known_verdict():
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id="sid",
        drift_verdict="SAFE_TO_APPLY",
        session_status="committed",
    )

    result = detect_incident(bundle)

    assert result.incident_type == "d1_drift"
    assert result.confidence == "medium"
    assert any("drift_diagnose" in action for action in result.recommended_next_actions)
    assert "Do not rollback the committed session." in result.unsafe_actions


def test_detector_flags_dead_lettered_recovery_outbox():
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        recovery_outbox_summary={"dead_lettered_count": 2, "pending_count": 0},
    )

    result = detect_incident(bundle)

    assert result.incident_type == "d1_recovery_outbox"
    assert "Recovery outbox has 2 dead-lettered event(s)." in result.confirmed_findings
    assert "Do not mark recovery work resolved before inspecting the dead-lettered ordering key." in result.unsafe_actions
```

- [ ] **Step 3: Implement collector**

Create `javdb/ops/diagnosis/collectors.py`:

```python
"""Deterministic evidence bundle collection for ADR-026."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from javdb.ops.diagnosis.models import EvidenceRef, IncidentBundle


_ERROR_MARKERS = ("ERROR", "WARNING", "Traceback", "failed", "FAILED", "cancelled")


def _collect_log_snippets(paths: Iterable[str | Path], *, max_lines: int = 20) -> list[str]:
    snippets: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(marker in line for marker in _ERROR_MARKERS):
                snippets.append(f"{path.name}: {line[:300]}")
                if len(snippets) >= max_lines:
                    return snippets
    return snippets


def collect_incident_bundle(
    *,
    trigger_source: str,
    run_id: str | None = None,
    run_attempt: int | None = None,
    session_id: str | None = None,
    workflow_name: str | None = None,
    workflow_result: str | None = None,
    session_status: str | None = None,
    drift_verdict: str | None = None,
    recovery_outbox_summary: dict | None = None,
    rollback_safety: str | None = None,
    qb_side_effects: dict | None = None,
    email_summary: str | None = None,
    log_paths: Iterable[str | Path] = (),
) -> IncidentBundle:
    return IncidentBundle(
        trigger_source=trigger_source,
        run_id=run_id,
        run_attempt=run_attempt,
        session_id=session_id,
        workflow_name=workflow_name,
        workflow_result=workflow_result,
        session_status=session_status,
        drift_verdict=drift_verdict,
        recovery_outbox_summary=dict(recovery_outbox_summary or {}),
        rollback_safety=rollback_safety,
        qb_side_effects=dict(qb_side_effects or {}),
        log_snippets=_collect_log_snippets(log_paths),
        email_summary=email_summary,
        runbook_refs=[
            EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/troubleshooting.md"),
            EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/d1-rollback.md"),
        ],
    )
```

- [ ] **Step 4: Implement detectors**

Create `javdb/ops/diagnosis/detectors.py`:

```python
"""Rule-based detectors for ADR-026 operations diagnosis."""

from __future__ import annotations

from javdb.ops.diagnosis.models import DiagnosisResult, EvidenceRef, IncidentBundle

DETECTOR_VERSION = "adr026-detectors-v1"


def detect_incident(bundle: IncidentBundle) -> DiagnosisResult:
    findings: list[str] = []
    causes: list[str] = []
    unknowns: list[str] = []
    actions: list[str] = []
    unsafe: list[str] = []
    evidence: list[EvidenceRef] = list(bundle.runbook_refs)
    confidence = "low"
    incident_type = "unknown"

    if bundle.workflow_result in {"failure", "cancelled"}:
        incident_type = "failed_ingestion"
        findings.append(f"Workflow result is {bundle.workflow_result}.")
        actions.append("Inspect the failed workflow job logs before retrying the run.")
        causes.append("The ingestion workflow did not complete successfully.")

    if bundle.drift_verdict:
        incident_type = "d1_drift"
        findings.append(f"Drift diagnosis verdict is {bundle.drift_verdict}.")
        evidence.append(EvidenceRef(kind="cli", ref="python3 -m apps.cli.db.drift_diagnose --since 24 --json"))
        if bundle.drift_verdict == "SAFE_TO_APPLY":
            confidence = "medium"
            actions.append("Run drift_diagnose apply only after reviewing the suggested session id.")
            unsafe.append("Do not rollback the committed session.")
        elif bundle.drift_verdict != "CLEAN":
            actions.append("Escalate D1 drift investigation before any cleanup.")
            unsafe.append("Do not apply D1 deletes for non-SAFE_TO_APPLY drift verdicts.")

    dead_lettered = int(bundle.recovery_outbox_summary.get("dead_lettered_count", 0) or 0)
    pending = int(bundle.recovery_outbox_summary.get("pending_count", 0) or 0)
    if dead_lettered:
        incident_type = "d1_recovery_outbox"
        findings.append(f"Recovery outbox has {dead_lettered} dead-lettered event(s).")
        actions.append("Inspect the D1 recovery outbox and repair or abandon the affected ordering key.")
        unsafe.append("Do not mark recovery work resolved before inspecting the dead-lettered ordering key.")
    elif pending:
        incident_type = "d1_recovery_outbox"
        findings.append(f"Recovery outbox has {pending} pending event(s).")
        actions.append("Drain the recovery outbox before committing or retrying the affected session.")

    if bundle.session_id is None:
        unknowns.append("Session id is missing; rollback safety cannot be proven.")
        unsafe.append("Do not run forced rollback without locating the owning session.")
    elif bundle.session_status:
        findings.append(f"Session status is {bundle.session_status}.")

    if bundle.qb_side_effects.get("uploaded") is True:
        findings.append("qBittorrent upload side effect may already have happened.")
        unsafe.append("Do not assume DB rollback removes qBittorrent-side downloads.")

    if not findings:
        unknowns.append("No known detector matched the incident bundle.")
        actions.append("Review workflow logs and runbooks manually.")

    return DiagnosisResult(
        incident_type=incident_type,
        confidence=confidence,
        confirmed_findings=findings,
        likely_causes=causes,
        unknowns=unknowns,
        recommended_next_actions=actions,
        unsafe_actions=unsafe,
        evidence_refs=evidence,
        model_version="deterministic-fallback-v1",
        detector_version=DETECTOR_VERSION,
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_ops_diagnosis_collectors.py tests/unit/test_ops_diagnosis_detectors.py -v
```

Expected: pass.

---

## Task 5: AI Synthesis Adapter And Service Orchestration

**Files:**
- Create: `javdb/ops/diagnosis/ai.py`
- Create: `javdb/ops/diagnosis/service.py`
- Create: `tests/unit/test_ops_diagnosis_service.py`

- [ ] **Step 1: Write orchestration tests**

Create `tests/unit/test_ops_diagnosis_service.py`:

```python
from __future__ import annotations

from javdb.ops.diagnosis.models import DiagnosisResult, EvidenceRef, IncidentBundle
from javdb.ops.diagnosis.service import diagnose_incident


class CapturingRepo:
    def __init__(self):
        self.records = []

    def upsert(self, record):
        self.records.append(record)


def test_service_runs_detector_and_persists_record():
    repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id=None,
    )

    record = diagnose_incident(bundle, repo=repo)

    assert record.incident_type == "failed_ingestion"
    assert record.persistence_status == "d1_written"
    assert len(repo.records) == 1
    assert repo.records[0].incident_id == record.incident_id


def test_service_uses_ai_synthesizer_when_available():
    repo = CapturingRepo()

    def synthesize(bundle, detector_result):
        return DiagnosisResult(
            incident_type="failed_ingestion",
            confidence="high",
            confirmed_findings=detector_result.confirmed_findings + ["AI summary produced"],
            likely_causes=["Known failure pattern"],
            unknowns=[],
            recommended_next_actions=["Open diagnosis page"],
            unsafe_actions=["Do not force rollback"],
            evidence_refs=[EvidenceRef(kind="incident", ref="synthetic")],
            model_version="fake-ai-v1",
            detector_version=detector_result.detector_version,
        )

    record = diagnose_incident(
        IncidentBundle(trigger_source="manual_cli", workflow_result="failure", session_id="sid"),
        repo=repo,
        synthesizer=synthesize,
    )

    assert record.model_version == "fake-ai-v1"
    assert "AI summary produced" in record.confirmed_findings_json
```

- [ ] **Step 2: Implement AI adapter**

Create `javdb/ops/diagnosis/ai.py`:

```python
"""AI synthesis boundary for ADR-026 operations diagnosis."""

from __future__ import annotations

from collections.abc import Callable

from javdb.infra.config import cfg
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle

Synthesizer = Callable[[IncidentBundle, DiagnosisResult], DiagnosisResult]


def _cfg_bool(name: str, default: bool = False) -> bool:
    raw = cfg(name, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(default)


def ai_diagnosis_enabled() -> bool:
    return _cfg_bool("OPS_DIAGNOSIS_AI_ENABLED", False)


def synthesize_with_configured_ai(
    bundle: IncidentBundle,
    detector_result: DiagnosisResult,
) -> DiagnosisResult:
    """Return detector result until a configured model adapter is implemented.

    Phase 1 keeps the interface explicit while allowing deployments without
    AI credentials to remain fully useful. Online model calls are intentionally
    left outside this phase; callers already depend on the stable synthesis
    interface defined here.
    """
    if not ai_diagnosis_enabled():
        return detector_result
    return detector_result
```

- [ ] **Step 3: Implement service orchestration**

Create `javdb/ops/diagnosis/service.py`:

```python
"""Service orchestration for ADR-026 read-only diagnosis."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from javdb.ops.diagnosis.ai import Synthesizer, synthesize_with_configured_ai
from javdb.ops.diagnosis.detectors import detect_incident
from javdb.ops.diagnosis.models import IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.persistence import persist_incident


def diagnose_incident(
    bundle: IncidentBundle,
    *,
    synthesizer: Synthesizer | None = None,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
) -> OpsIncidentRecord:
    detector_result = detect_incident(bundle)
    result = (synthesizer or synthesize_with_configured_ai)(bundle, detector_result)
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result)
    return persist_incident(record, repo=repo, jsonl_path=jsonl_path)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_ops_diagnosis_service.py -v
```

Expected: pass.

---

## Task 6: Operator CLI

**Files:**
- Create: `apps/cli/ops/diagnose_run.py`
- Modify: `apps/cli/ops/README.md`
- Create: `tests/unit/test_ops_diagnose_run_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/unit/test_ops_diagnose_run_cli.py`:

```python
from __future__ import annotations

import json

from apps.cli.ops import diagnose_run


def test_cli_requires_at_least_one_run_or_session_identifier(capsys):
    code = diagnose_run.main(["--json"])

    assert code == 2
    assert "provide --run-id or --session-id" in capsys.readouterr().err


def test_cli_prints_json_record(monkeypatch, capsys):
    def fake_diagnose(bundle, **_kwargs):
        class Record:
            incident_id = "opsinc_test"
            incident_type = "failed_ingestion"
            confidence = "low"
            persistence_status = "d1_written"
            confirmed_findings_json = '["Workflow result is failure."]'
            likely_causes_json = "[]"
            unknowns_json = "[]"
            recommended_next_actions_json = "[]"
            unsafe_actions_json = "[]"
            evidence_refs_json = "[]"
        return Record()

    monkeypatch.setattr(diagnose_run, "diagnose_incident", fake_diagnose)

    code = diagnose_run.main([
        "--run-id", "123",
        "--attempt", "1",
        "--workflow-result", "failure",
        "--json",
    ])

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["incident_id"] == "opsinc_test"
    assert payload["incident_type"] == "failed_ingestion"
```

- [ ] **Step 2: Implement CLI**

Create `apps/cli/ops/diagnose_run.py`:

```python
"""Read-only operations diagnosis CLI for ADR-026."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from javdb.infra.logging import setup_logging
from javdb.ops.diagnosis.collectors import collect_incident_bundle
from javdb.ops.diagnosis.service import diagnose_incident


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.diagnose_run",
        description="Collect read-only incident evidence and persist an ADR-026 diagnosis.",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--attempt", type=int, dest="run_attempt")
    parser.add_argument("--session-id")
    parser.add_argument("--workflow-name", default=None)
    parser.add_argument("--workflow-result", default=None, choices=("success", "failure", "cancelled", "skipped", None))
    parser.add_argument("--trigger-source", default="manual_cli")
    parser.add_argument("--session-status", default=None)
    parser.add_argument("--drift-verdict", default=None)
    parser.add_argument("--log", action="append", default=[], dest="log_paths")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def _record_to_payload(record) -> dict:
    return {
        "incident_id": record.incident_id,
        "incident_type": record.incident_type,
        "confidence": record.confidence,
        "persistence_status": record.persistence_status,
        "confirmed_findings": json.loads(record.confirmed_findings_json),
        "likely_causes": json.loads(record.likely_causes_json),
        "unknowns": json.loads(record.unknowns_json),
        "recommended_next_actions": json.loads(record.recommended_next_actions_json),
        "unsafe_actions": json.loads(record.unsafe_actions_json),
        "evidence_refs": json.loads(record.evidence_refs_json),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    if not args.run_id and not args.session_id:
        print("ERROR: provide --run-id or --session-id for diagnosis.", file=sys.stderr)
        return 2

    bundle = collect_incident_bundle(
        trigger_source=args.trigger_source,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        session_id=args.session_id,
        workflow_name=args.workflow_name,
        workflow_result=args.workflow_result,
        session_status=args.session_status,
        drift_verdict=args.drift_verdict,
        log_paths=args.log_paths,
    )
    record = diagnose_incident(bundle)
    payload = _record_to_payload(record)

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Incident: {payload['incident_id']}")
        print(f"Type: {payload['incident_type']}")
        print(f"Confidence: {payload['confidence']}")
        print(f"Persistence: {payload['persistence_status']}")
        for finding in payload["confirmed_findings"]:
            print(f"- {finding}")

    return 1 if payload["incident_type"] != "unknown" else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Update ops README**

Add a row to `apps/cli/ops/README.md`:

```markdown
| `diagnose_run.py` | Read-only ADR-026 operations diagnosis for failed workflow runs, sessions, D1 drift, recovery outbox state, and qB side-effect evidence. |
```

Add an invoked-by note:

```markdown
- **`DailyIngestion.yml` / `AdHocIngestion.yml`** — on failed or cancelled runs, `python3 -m apps.cli.ops.diagnose_run` creates a persisted read-only incident record for email/API review.
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_ops_diagnose_run_cli.py -v
```

Expected: pass.

---

## Task 7: API Read Surface

**Files:**
- Modify: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/routers/diagnostics.py`
- Create: `tests/unit/test_ops_diagnostics_api.py`

- [ ] **Step 1: Write API tests**

Create `tests/unit/test_ops_diagnostics_api.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(_isolate_sqlite):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return client


@pytest.fixture
def anon_client(_isolate_sqlite):
    from apps.api.services.runtime import app

    return TestClient(app)


def test_ops_incidents_requires_auth(anon_client: TestClient):
    response = anon_client.get("/api/diag/ops-incidents")
    assert response.status_code in {401, 403}


def test_ops_incident_schema_from_record(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import (
        DiagnosisResult,
        IncidentBundle,
        OpsIncidentRecord,
    )

    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="low",
        confirmed_findings=["workflow failed"],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=["inspect logs"],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli", run_id="123"),
        result,
    ).with_persistence_status("d1_written")

    monkeypatch.setattr(
        diagnostics,
        "_list_ops_incident_records",
        lambda **_kwargs: [record],
    )

    response = admin_client.get("/api/diag/ops-incidents")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["incident_type"] == "failed_ingestion"
    assert payload["items"][0]["confirmed_findings"] == ["workflow failed"]
```

- [ ] **Step 2: Add schemas**

Modify `apps/api/schemas/diagnostics.py`:

```python
class EvidenceRefSchema(BaseModel):
    kind: str
    ref: str
    label: Optional[str] = None


class OpsIncidentSchema(BaseModel):
    incident_id: str
    trigger_source: str
    run_id: Optional[str] = None
    run_attempt: Optional[int] = None
    session_id: Optional[str] = None
    incident_type: str
    status: str
    persistence_status: str
    model_version: str
    detector_version: str
    confidence: str
    confirmed_findings: list[str]
    likely_causes: list[str]
    unknowns: list[str]
    recommended_next_actions: list[str]
    unsafe_actions: list[str]
    evidence_refs: list[EvidenceRefSchema]
    created_at: str
    updated_at: str
    resolved_at: Optional[str] = None


class OpsIncidentListResponse(BaseModel):
    items: list[OpsIncidentSchema]
```

Update `__all__` to include the new schema names.

- [ ] **Step 3: Add router helpers and endpoints**

Modify `apps/api/routers/diagnostics.py`:

```python
import json

from apps.api.schemas.diagnostics import (
    EvidenceRefSchema,
    OpsIncidentListResponse,
    OpsIncidentSchema,
)
from javdb.storage.db import REPORTS_DB_PATH
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


def _list_ops_incident_records(
    *,
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).list(
            status=status,
            run_id=run_id,
            session_id=session_id,
            limit=limit,
        )


def _get_ops_incident_record(incident_id: str):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).get(incident_id)


def _ops_record_to_schema(record) -> OpsIncidentSchema:
    return OpsIncidentSchema(
        incident_id=record.incident_id,
        trigger_source=record.trigger_source,
        run_id=record.run_id,
        run_attempt=record.run_attempt,
        session_id=record.session_id,
        incident_type=record.incident_type,
        status=record.status,
        persistence_status=record.persistence_status,
        model_version=record.model_version,
        detector_version=record.detector_version,
        confidence=record.confidence,
        confirmed_findings=json.loads(record.confirmed_findings_json),
        likely_causes=json.loads(record.likely_causes_json),
        unknowns=json.loads(record.unknowns_json),
        recommended_next_actions=json.loads(record.recommended_next_actions_json),
        unsafe_actions=json.loads(record.unsafe_actions_json),
        evidence_refs=[
            EvidenceRefSchema(**item)
            for item in json.loads(record.evidence_refs_json)
        ],
        created_at=record.created_at,
        updated_at=record.updated_at,
        resolved_at=record.resolved_at,
    )


@router.get("/ops-incidents", response_model=OpsIncidentListResponse)
def list_ops_incidents(
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentListResponse:
    items = _list_ops_incident_records(
        status=status,
        run_id=run_id,
        session_id=session_id,
        limit=min(limit, 100),
    )
    return OpsIncidentListResponse(items=[_ops_record_to_schema(item) for item in items])


@router.get("/ops-incidents/{incident_id}", response_model=OpsIncidentSchema)
def get_ops_incident(
    incident_id: str,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentSchema:
    record = _get_ops_incident_record(incident_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _ops_record_to_schema(record)
```

- [ ] **Step 4: Run API tests**

```bash
pytest tests/unit/test_ops_diagnostics_api.py -v
```

Expected: pass.

---

## Task 8: Email And Workflow Integration

**Files:**
- Modify: `javdb/integrations/notify/email.py`
- Modify: `.github/workflows/DailyIngestion.yml`
- Modify: `.github/workflows/AdHocIngestion.yml`
- Modify: `.github/workflows/TestIngestion.yml`
- Create: `tests/unit/test_email_ops_diagnosis.py`
- Modify: `tests/unit/test_workflow_resolve_write_mode.py`

- [ ] **Step 1: Write email advisory tests**

Create `tests/unit/test_email_ops_diagnosis.py`:

```python
from __future__ import annotations

import json

from javdb.integrations.notify import email


def test_build_ops_diagnosis_advisory_from_json_file(tmp_path):
    path = tmp_path / "ops_diagnosis.json"
    path.write_text(json.dumps({
        "incident_id": "opsinc_test",
        "incident_type": "failed_ingestion",
        "confidence": "low",
        "confirmed_findings": ["Workflow result is failure."],
        "recommended_next_actions": ["Inspect failed job logs."],
    }), encoding="utf-8")

    advisory = email._build_ops_diagnosis_advisory(str(path))

    assert "Operations Diagnosis" in advisory
    assert "opsinc_test" in advisory
    assert "failed_ingestion" in advisory
    assert "Inspect failed job logs." in advisory
    assert "Full diagnosis" in advisory


def test_build_ops_diagnosis_advisory_missing_file_is_empty(tmp_path):
    assert email._build_ops_diagnosis_advisory(str(tmp_path / "missing.json")) == ""
```

- [ ] **Step 2: Add email helper**

Modify `javdb/integrations/notify/email.py`:

```python
def _build_ops_diagnosis_advisory(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return ""

    incident_id = payload.get("incident_id", "unknown")
    incident_type = payload.get("incident_type", "unknown")
    confidence = payload.get("confidence", "low")
    findings = payload.get("confirmed_findings") or []
    actions = payload.get("recommended_next_actions") or []
    first_finding = findings[0] if findings else "No confirmed finding recorded."
    first_action = actions[0] if actions else "Review the persisted diagnosis record."

    return f"""
─── Operations Diagnosis ───
Incident: {incident_id}
Type: {incident_type}
Confidence: {confidence}
Finding: {first_finding}
Next action: {first_action}
Full diagnosis: /api/diag/ops-incidents/{incident_id}

"""
```

In the main email body assembly, read `OPS_DIAGNOSIS_JSON` from the environment and prepend or append the returned advisory near existing drift/pending advisories:

```python
ops_advisory = _build_ops_diagnosis_advisory(os.environ.get("OPS_DIAGNOSIS_JSON"))
if ops_advisory:
    body = ops_advisory + body
```

- [ ] **Step 3: Add failed-run diagnosis step to DailyIngestion**

In `.github/workflows/DailyIngestion.yml`, inside `email-notification` before the email send step, add a read-only diagnosis step:

```yaml
      - name: Diagnose failed run (ADR-026)
        id: ops_diagnosis
        if: ${{ steps.status.outputs.has_failure == 'true' }}
        env:
          RUN_ID: ${{ github.run_id }}
          RUN_ATTEMPT: ${{ github.run_attempt }}
          SESSION_ID: ${{ needs.run-pipeline.outputs.session_id }}
          WORKFLOW_RESULT: ${{ needs.run-pipeline.result }}
        run: |
          set +e
          mkdir -p reports/ops
          CMD_ARGS=(
            python3 -m apps.cli.ops.diagnose_run
            --trigger-source workflow_failure
            --run-id "$RUN_ID"
            --attempt "$RUN_ATTEMPT"
            --workflow-name DailyIngestion
            --workflow-result "$WORKFLOW_RESULT"
            --json
          )
          if [ -n "$SESSION_ID" ]; then
            CMD_ARGS+=(--session-id "$SESSION_ID")
          fi
          "${CMD_ARGS[@]}" > reports/ops/latest_ops_diagnosis.json
          DIAG_EXIT=$?
          echo "json_path=reports/ops/latest_ops_diagnosis.json" >> "$GITHUB_OUTPUT"
          if [ $DIAG_EXIT -gt 2 ]; then
            echo "::warning::ADR-026 diagnosis failed with exit code $DIAG_EXIT"
          fi
          exit 0
```

In the email send step environment, add:

```yaml
          OPS_DIAGNOSIS_JSON: ${{ steps.ops_diagnosis.outputs.json_path }}
```

- [ ] **Step 4: Add the same failed-run diagnosis step to AdHocIngestion**

Use the same shape as DailyIngestion, but pass `--workflow-name AdHocIngestion`.

- [ ] **Step 5: Add TestIngestion smoke path**

In `.github/workflows/TestIngestion.yml`, add a manual or always-safe post-run smoke step that runs the CLI with local mock context only when the run has a session id:

```yaml
      - name: Smoke test ops diagnosis CLI (ADR-026)
        if: ${{ always() }}
        run: |
          set +e
          python3 -m apps.cli.ops.diagnose_run \
            --trigger-source workflow_failure \
            --run-id "${{ github.run_id }}" \
            --attempt "${{ github.run_attempt }}" \
            --workflow-name TestIngestion \
            --workflow-result "${{ job.status }}" \
            --json > reports/ops/test_ops_diagnosis.json
          EXIT_CODE=$?
          if [ $EXIT_CODE -gt 2 ]; then
            exit $EXIT_CODE
          fi
```

- [ ] **Step 6: Add workflow tests**

Extend `tests/unit/test_workflow_resolve_write_mode.py` or create a focused workflow YAML test that asserts:

```python
from pathlib import Path


def test_daily_ingestion_runs_ops_diagnosis_before_email():
    text = Path(".github/workflows/DailyIngestion.yml").read_text()
    assert "Diagnose failed run (ADR-026)" in text
    assert "python3 -m apps.cli.ops.diagnose_run" in text
    assert "OPS_DIAGNOSIS_JSON" in text


def test_adhoc_ingestion_runs_ops_diagnosis_before_email():
    text = Path(".github/workflows/AdHocIngestion.yml").read_text()
    assert "Diagnose failed run (ADR-026)" in text
    assert "--workflow-name AdHocIngestion" in text
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/unit/test_email_ops_diagnosis.py tests/unit/test_workflow_resolve_write_mode.py -v
```

Expected: pass.

---

## Task 9: Configuration And Documentation

**Files:**
- Modify: `config.py.example`
- Modify: `javdb/infra/config_generator.py`
- Modify: `docs/handbook/en/ops/troubleshooting.md`
- Modify: `docs/handbook/zh/ops/troubleshooting.md`
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [ ] **Step 1: Add config defaults**

Add to `config.py.example` near the existing GPT/API section:

```python
# AI operations diagnosis assistant (ADR-026)
# Phase 1 remains read-only. When disabled or not configured, the assistant
# still produces deterministic detector-based diagnoses.
OPS_DIAGNOSIS_AI_ENABLED = False
OPS_DIAGNOSIS_API_URL = ''
OPS_DIAGNOSIS_API_KEY = ''
OPS_DIAGNOSIS_MODEL = 'deterministic-fallback-v1'
OPS_DIAGNOSIS_MAX_LOG_SNIPPETS = 20
```

- [ ] **Step 2: Generate config values**

Add matching entries to `javdb/infra/config_generator.py` near the GPT API entries:

```python
('OPS_DIAGNOSIS_AI_ENABLED', 'OPS_DIAGNOSIS_AI_ENABLED', get_env_bool, False, 'AI OPERATIONS DIAGNOSIS'),
('OPS_DIAGNOSIS_API_URL', 'OPS_DIAGNOSIS_API_URL', get_env, '', 'AI OPERATIONS DIAGNOSIS'),
('OPS_DIAGNOSIS_API_KEY', 'OPS_DIAGNOSIS_API_KEY', get_env, '', 'AI OPERATIONS DIAGNOSIS'),
('OPS_DIAGNOSIS_MODEL', 'OPS_DIAGNOSIS_MODEL', get_env, 'deterministic-fallback-v1', 'AI OPERATIONS DIAGNOSIS'),
('OPS_DIAGNOSIS_MAX_LOG_SNIPPETS', 'OPS_DIAGNOSIS_MAX_LOG_SNIPPETS', get_env_int, 20, 'AI OPERATIONS DIAGNOSIS'),
```

- [ ] **Step 3: Document CLI usage in English troubleshooting**

Add to `docs/handbook/en/ops/troubleshooting.md`:

````markdown
## AI Operations Diagnosis

ADR-026 adds a read-only diagnosis assistant for failed ingestion runs and D1
operational incidents. It collects a compact evidence bundle, runs deterministic
detectors, optionally uses an AI synthesis adapter, persists an `OpsIncidents`
record, and prints a structured summary.

Manual run:

```bash
python3 -m apps.cli.ops.diagnose_run \
  --run-id 123456789 \
  --attempt 1 \
  --session-id 20260527T120000.000000Z-abcd-0001 \
  --workflow-name DailyIngestion \
  --workflow-result failure \
  --json
```

The assistant is advisory only. It does not roll back sessions, rerun workflows,
modify D1, or delete qBittorrent tasks.
````

- [ ] **Step 4: Mirror the section in Chinese**

Add to `docs/handbook/zh/ops/troubleshooting.md`:

````markdown
## AI 运维诊断

ADR-026 新增了一个只读诊断助手，用于失败的 ingestion run 和 D1 运维事件。它会收集紧凑的 evidence bundle，运行确定性 detectors，可选使用 AI 综合层，持久化一条 `OpsIncidents` 记录，并输出结构化摘要。

手动运行：

```bash
python3 -m apps.cli.ops.diagnose_run \
  --run-id 123456789 \
  --attempt 1 \
  --session-id 20260527T120000.000000Z-abcd-0001 \
  --workflow-name DailyIngestion \
  --workflow-result failure \
  --json
```

该助手只提供建议。它不会 rollback session、重跑 workflow、修改 D1，或删除 qBittorrent 任务。
````

- [ ] **Step 5: Cross-link from rollback docs**

Add a short paragraph to `docs/handbook/en/ops/d1-rollback.md` near rollback troubleshooting:

````markdown
For a structured advisory before deciding whether rollback is safe, run the
ADR-026 diagnosis assistant:

```bash
python3 -m apps.cli.ops.diagnose_run --run-id <run_id> --attempt <attempt> --session-id <session_id> --workflow-result failure --json
```

The assistant is read-only and does not replace the rollback safety matrix.
````

Mirror the same content in `docs/handbook/zh/ops/d1-rollback.md`.

- [ ] **Step 6: Run documentation checks**

```bash
git diff --check -- config.py.example javdb/infra/config_generator.py docs/handbook/en/ops/troubleshooting.md docs/handbook/zh/ops/troubleshooting.md docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
```

Expected: no output.

---

## Task 10: Verification And Closeout

- [ ] **Step 1: Run focused tests**

```bash
pytest \
  tests/unit/test_ops_diagnosis_models.py \
  tests/unit/test_ops_incident_repo.py \
  tests/unit/test_ops_diagnosis_collectors.py \
  tests/unit/test_ops_diagnosis_detectors.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnose_run_cli.py \
  tests/unit/test_email_ops_diagnosis.py \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run API/workflow tests**

```bash
pytest tests/unit/test_ops_diagnostics_api.py tests/unit/test_workflow_resolve_write_mode.py -v
```

Expected: all pass.

- [ ] **Step 3: Smoke the CLI locally**

```bash
python3 -m apps.cli.ops.diagnose_run \
  --run-id local-test \
  --attempt 1 \
  --workflow-name DailyIngestion \
  --workflow-result failure \
  --json
```

Expected: JSON output with `incident_id`, `incident_type`, `confirmed_findings`, `recommended_next_actions`, `unsafe_actions`, and `persistence_status`.

- [ ] **Step 4: Run formatting and placeholder checks**

```bash
git diff --check
rg -n "TB[D]|TO[D]O|PLACEHOLDE[R]|FIXM[E]|XX[X]" docs/design/ADR-026-AI-Operations-Diagnosis javdb/ops apps/cli/ops/diagnose_run.py
rg -n "ADR-NNN|IMP-ADRNNN" docs/design/ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md docs/design/ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.zh.md
```

Expected: `git diff --check` has no output; `rg` has no matches.

- [ ] **Step 5: Review workflow and docs impact**

Confirm all AGENTS.md automation-change requirements were handled:

- unit tests updated for CLI/service/API/email/workflow behavior;
- `.github/workflows/` reviewed and updated for Daily, AdHoc, and Test ingestion;
- handbook docs updated in English and Chinese;
- README not expanded because usage details belong in handbook ops docs;
- wiki is not edited directly because handbook is the source of truth.

- [ ] **Step 6: Commit**

```bash
git add \
  javdb/migrations/d1/2026_05_27_add_ops_incidents.sql \
  javdb/ops \
  javdb/storage/repos/ops_incident_repo.py \
  apps/cli/ops/diagnose_run.py \
  apps/cli/ops/README.md \
  apps/api/schemas/diagnostics.py \
  apps/api/routers/diagnostics.py \
  javdb/integrations/notify/email.py \
  .github/workflows/DailyIngestion.yml \
  .github/workflows/AdHocIngestion.yml \
  .github/workflows/TestIngestion.yml \
  config.py.example \
  javdb/infra/config_generator.py \
  tests/unit/test_ops_diagnosis_models.py \
  tests/unit/test_ops_incident_repo.py \
  tests/unit/test_ops_diagnosis_collectors.py \
  tests/unit/test_ops_diagnosis_detectors.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnose_run_cli.py \
  tests/unit/test_ops_diagnostics_api.py \
  tests/unit/test_email_ops_diagnosis.py \
  tests/unit/test_workflow_resolve_write_mode.py \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md \
  docs/handbook/en/ops/d1-rollback.md \
  docs/handbook/zh/ops/d1-rollback.md
git commit -m "feat(ops): add read-only AI operations diagnosis"
```

---

## Definition Of Done

| # | Gate | Check |
|---|---|---|
| 1 | D1 schema | `OpsIncidents` exists in D1 `reports` DB and local SQLite mirror. |
| 2 | Safety boundary | No code path in ADR-026 calls rollback apply, workflow rerun, D1 mutation from model output, or qB delete. |
| 3 | Persistence | Diagnosis writes D1 first and falls back to JSONL with `persistence_status=d1_failed_jsonl_written`. |
| 4 | CLI | `python3 -m apps.cli.ops.diagnose_run --run-id ... --json` emits structured diagnosis JSON. |
| 5 | API | `/api/diag/ops-incidents` and `/api/diag/ops-incidents/{id}` return persisted incidents behind auth. |
| 6 | Email | Failed-run emails include only a short operations diagnosis advisory when a diagnosis JSON is available. |
| 7 | Workflows | Daily/AdHoc failed runs invoke the read-only diagnosis CLI; TestIngestion has a smoke path. |
| 8 | Tests | Focused unit/API/workflow tests pass. |
| 9 | Docs | English and Chinese ops docs describe the read-only assistant and safety boundary. |
