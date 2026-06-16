# IMP-ADR026-04: ADR-026 Phase 4 - Proactive Incident Alerting & Operator Config

**Status:** In Progress — backend/server slice complete; frontend Agent F scope pending

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-026 Phase 4 by adding a deterministic alert-trigger decision after incident persistence plus operator-facing alerting config, so that detected incidents proactively notify operators instead of waiting to be read.

**Architecture:** This phase owns the MIDDLE layer between incident detection and notification delivery. After an incident is persisted with status `d1_written`, a deterministic policy decides whether the incident should alert, builds a `NotifyMessage`, and hands it to the EXISTING ADR-039 notify dispatch for delivery. An alert-event audit row records what fired (with deduplication keyed on the incident). Operators tune per-incident-type alert policies (enable toggle + confidence threshold + channels) through API/Web. This phase does NOT implement notification delivery (ADR-039 owns that) and does NOT implement remediation (Phase 3, IMP-ADR026-03 owns that).

**Tech Stack:** Python 3.11, Cloudflare D1, FastAPI/Pydantic, pytest, TypeScript, Hono, Vue 3, Naive UI, Vitest, Markdown docs.

**Source spec:** [ADR-026](ADR-026-ai-operations-diagnosis.md), Phase 4 roadmap, D1-D10.

**Non-negotiable:** Phase 4 only (a) decides whether an incident should alert, (b) builds a `NotifyMessage` and hands it to the EXISTING ADR-039 notify dispatch, (c) records an alert-event audit row, and (d) exposes operator config. It must NOT reimplement backend-specific delivery, routing rules, digests, or any remediation/execution. Notification delivery stays in ADR-039. Remediation stays in Phase 3 (IMP-ADR026-03) and must not be touched.

## Table of Contents

- [File Map](#file-map)
- [Scope Boundaries](#scope-boundaries)
- [Task 1: D1 Alert Tables](#task-1-d1-alert-tables)
- [Task 2: Alert Models And Deterministic Evaluation](#task-2-alert-models-and-deterministic-evaluation)
- [Task 3: Alert Repository](#task-3-alert-repository)
- [Task 4: Service Integration](#task-4-service-integration)
- [Task 5: Python API Alert Surface](#task-5-python-api-alert-surface)
- [Task 6: Cloudflare Worker API Parity](#task-6-cloudflare-worker-api-parity)
- [Task 7: Web Alerting Config Panel](#task-7-web-alerting-config-panel)
- [Task 8: Documentation](#task-8-documentation)
- [Task 9: Verification And Closeout](#task-9-verification-and-closeout)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql` | D1-first alert policy + alert event ledger for proactive alerting. |
| Modify | `javdb/storage/db/_db_migrations.py` | Include alert policy and alert event tables in local SQLite mirror initialization. |
| Modify | `javdb/ops/diagnosis/models.py` | Add `AlertDecision` and alert-event contracts. |
| Create | `javdb/ops/diagnosis/alerting.py` | Deterministic `evaluate_alert` trigger function (pure, no delivery). |
| Create | `javdb/storage/repos/ops_alert_repo.py` | Repository for alert policy rows and alert event rows. |
| Modify | `javdb/ops/diagnosis/service.py` | Opt-in alert evaluation after incident persistence, build `NotifyMessage`, reuse ADR-039 dispatch, upsert alert event. |
| Modify | `apps/api/schemas/diagnostics.py` | Add alert policy and alert event schemas. |
| Modify | `apps/api/routers/diagnostics.py` | Add alert-policy list/upsert endpoints and alert-event read endpoint. |
| Create | `tests/unit/test_ops_alerting.py` | `evaluate_alert` deterministic trigger tests. |
| Create | `tests/unit/test_ops_alert_repo.py` | Alert repository tests. |
| Modify | `tests/unit/test_ops_diagnosis_service.py` | Service orchestration tests for opt-in alert generation and ADR-039 dispatch reuse. |
| Modify | `tests/unit/test_ops_diagnostics_api.py` | API tests for alert policy and alert event endpoints. |
| Modify | `../../../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts` | Worker alert-policy and alert-event endpoint parity. |
| Modify | `../../../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts` | Worker alert route tests. |
| Modify | `../../../JAVDB_AutoSpider_Web/src/api/diagnostics.ts` | Frontend alert policy/event API types and functions. |
| Create | `../../../JAVDB_AutoSpider_Web/src/components/diagnostics/AlertPolicyPanel.vue` | Operator alerting config panel (per incident_type). |
| Modify | `../../../JAVDB_AutoSpider_Web/src/pages/diagnostics/OpsIncidentsPage.vue` | Mount alerting config panel and show alert-status badge on incident detail. |
| Create | `../../../JAVDB_AutoSpider_Web/tests/unit/ops-alerting-api.spec.ts` | Frontend API client tests. |
| Modify | `docs/handbook/en/ops/troubleshooting.md` | Document alerting policy semantics and fired/suppressed/skipped states. |
| Modify | `docs/handbook/zh/ops/troubleshooting.md` | Chinese mirror. |

## Scope Boundaries

- Alert evaluation is a deterministic trigger decision, not a delivery mechanism.
- Notification delivery (SMTP, channel routing, backend selection) stays in ADR-039's `NotifyPlugin` dispatch. This phase imports and reuses it; it must not reimplement delivery.
- Remediation suggestions and decisions stay in Phase 3 (IMP-ADR026-03). This phase must not touch `javdb/ops/diagnosis/remediation.py` or the proposal ledger.
- Alert generation must be opt-in (a `generate_alerts: bool = False` param mirroring Phase 3's `generate_remediation`) and best-effort: a delivery failure must not crash diagnosis.
- The deterministic policy must not rely on a model to decide whether to alert.
- This phase does not add digests, batching, escalation, or routing rules; those are out of scope and belong to a future ADR-039 phase if needed.

---

## Task 1: D1 Alert Tables

**Files:**
- Create: `javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql`
- Modify: `javdb/storage/db/_db_migrations.py`

- [x] **Step 1: Create alert tables migration**

Create `javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql`:

```sql
-- 2026-06-13: Add ADR-026 Phase 4 proactive alerting tables.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql
--
-- OpsAlertPolicy is operator-tunable. OpsAlertEvent is a dedupe + audit ledger
-- of what alerted. Neither table delivers notifications; delivery stays in
-- ADR-039 NotifyPlugin dispatch.

CREATE TABLE IF NOT EXISTS OpsAlertPolicy (
  policy_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  min_confidence TEXT NOT NULL DEFAULT 'medium'
    CHECK (min_confidence IN ('low', 'medium', 'high')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  channels_json TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_alert_policy_incident_type
  ON OpsAlertPolicy(incident_type);

CREATE TABLE IF NOT EXISTS OpsAlertEvent (
  alert_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  policy_id TEXT,
  status TEXT NOT NULL DEFAULT 'fired'
    CHECK (status IN ('fired', 'suppressed', 'skipped')),
  reason TEXT,
  fired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (incident_id) REFERENCES OpsIncidents(incident_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_alert_event_incident
  ON OpsAlertEvent(incident_id);

CREATE INDEX IF NOT EXISTS idx_ops_alert_event_status
  ON OpsAlertEvent(status);
```

- [x] **Step 2: Add local mirror DDL**

Modify `javdb/storage/db/_db_migrations.py` by adding the same two tables and indexes to the reports DDL block.

- [x] **Step 3: Verify schema syntax locally**

Run:

```bash
python3 -m compileall javdb/storage/db/_db_migrations.py
```

Expected: compile succeeds.

- [x] **Step 4: Defer remote apply**

Record this command for rollout, but do not run it while writing the plan:

```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql
```

Expected during rollout: D1 creates `OpsAlertPolicy` and `OpsAlertEvent`.

---

## Task 2: Alert Models And Deterministic Evaluation

**Files:**
- Modify: `javdb/ops/diagnosis/models.py`
- Create: `javdb/ops/diagnosis/alerting.py`
- Create: `tests/unit/test_ops_alerting.py`

- [x] **Step 1: Write evaluation tests**

Create `tests/unit/test_ops_alerting.py`:

```python
from __future__ import annotations

from javdb.ops.diagnosis.alerting import build_alert_id, evaluate_alert
from javdb.ops.diagnosis.models import OpsAlertPolicy


def _policy(
    incident_type: str = "failed_ingestion",
    *,
    min_confidence: str = "medium",
    enabled: bool = True,
    channels: list[str] | None = None,
) -> OpsAlertPolicy:
    return OpsAlertPolicy.create(
        incident_type=incident_type,
        min_confidence=min_confidence,
        enabled=enabled,
        channels=channels or ["email"],
        updated_by="admin",
    )


class _Record:
    def __init__(self, incident_id: str, incident_type: str, confidence: str) -> None:
        self.incident_id = incident_id
        self.incident_type = incident_type
        self.confidence = confidence


def test_alert_id_is_deterministic_from_incident_id():
    first = build_alert_id("opsinc_abc")
    second = build_alert_id("opsinc_abc")

    assert first == second
    assert first.startswith("opsalert_")


def test_fires_when_policy_matches_and_confidence_meets_threshold():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy(min_confidence="medium")], already_fired=False)

    assert decision.status == "fired"
    assert decision.policy_id is not None
    assert decision.alert_id == build_alert_id("opsinc_abc")


def test_suppressed_when_already_fired():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy()], already_fired=True)

    assert decision.status == "suppressed"
    assert "already" in decision.reason.lower()


def test_skipped_when_confidence_below_threshold():
    record = _Record("opsinc_abc", "failed_ingestion", "low")

    decision = evaluate_alert(record, [_policy(min_confidence="high")], already_fired=False)

    assert decision.status == "skipped"
    assert "confidence" in decision.reason.lower()


def test_skipped_when_no_matching_enabled_policy():
    record = _Record("opsinc_abc", "stale_session", "high")

    decision = evaluate_alert(record, [_policy(incident_type="failed_ingestion")], already_fired=False)

    assert decision.status == "skipped"


def test_skipped_when_matching_policy_is_disabled():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy(enabled=False)], already_fired=False)

    assert decision.status == "skipped"


def test_confidence_ordering_is_deterministic():
    record = _Record("opsinc_abc", "failed_ingestion", "medium")

    fires = evaluate_alert(record, [_policy(min_confidence="medium")], already_fired=False)
    skips = evaluate_alert(record, [_policy(min_confidence="high")], already_fired=False)

    assert fires.status == "fired"
    assert skips.status == "skipped"
```

- [x] **Step 2: Add alert model types**

Add to `javdb/ops/diagnosis/models.py`:

```python
Confidence = Literal["low", "medium", "high"]
AlertStatus = Literal["fired", "suppressed", "skipped"]

_CONFIDENCE_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def confidence_rank(value: str) -> int:
    return _CONFIDENCE_ORDER.get(value, 0)


def build_alert_policy_id(incident_type: str) -> str:
    digest = hashlib.sha256(f"alertpolicy|{incident_type}".encode("utf-8")).hexdigest()[:24]
    return f"opspolicy_{digest}"


@dataclass(frozen=True)
class OpsAlertPolicy:
    policy_id: str
    incident_type: str
    min_confidence: Confidence
    enabled: bool
    channels_json: str
    updated_by: str | None
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        incident_type: str,
        min_confidence: Confidence = "medium",
        enabled: bool = True,
        channels: list[str] | None = None,
        updated_by: str | None = None,
    ) -> "OpsAlertPolicy":
        now = utc_now_iso()
        return cls(
            policy_id=build_alert_policy_id(incident_type),
            incident_type=incident_type,
            min_confidence=min_confidence,
            enabled=enabled,
            channels_json=_json_dumps(channels or []),
            updated_by=updated_by,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True)
class OpsAlertEvent:
    alert_id: str
    incident_id: str
    policy_id: str | None
    status: AlertStatus
    reason: str | None
    fired_at: str


@dataclass(frozen=True)
class AlertDecision:
    alert_id: str
    incident_id: str
    policy_id: str | None
    status: AlertStatus
    reason: str

    def to_event(self) -> OpsAlertEvent:
        return OpsAlertEvent(
            alert_id=self.alert_id,
            incident_id=self.incident_id,
            policy_id=self.policy_id,
            status=self.status,
            reason=self.reason,
            fired_at=utc_now_iso(),
        )
```

Export `OpsAlertPolicy`, `OpsAlertEvent`, `AlertDecision`, `confidence_rank`, and `build_alert_policy_id` from `javdb/ops/diagnosis/__init__.py`.

- [x] **Step 3: Implement deterministic evaluation**

Create `javdb/ops/diagnosis/alerting.py`:

```python
"""Deterministic alert-trigger evaluation for ADR-026 Phase 4.

This module decides WHETHER an incident should alert. It does not deliver
notifications. Delivery is handled by ADR-039 NotifyPlugin dispatch, invoked
from the diagnosis service after a `fired` decision.
"""

from __future__ import annotations

import hashlib

from javdb.ops.diagnosis.models import (
    AlertDecision,
    OpsAlertPolicy,
    confidence_rank,
)


def build_alert_id(incident_id: str) -> str:
    digest = hashlib.sha256(incident_id.encode("utf-8")).hexdigest()[:24]
    return f"opsalert_{digest}"


def evaluate_alert(
    record: object,
    policies: list[OpsAlertPolicy],
    already_fired: bool,
) -> AlertDecision:
    """Return an AlertDecision for an incident record.

    Fires only when a matching enabled policy exists AND
    record.confidence >= policy.min_confidence AND the alert has not already
    fired for this incident. Otherwise returns suppressed/skipped with an
    explicit reason. Pure and deterministic.
    """
    incident_id = record.incident_id
    alert_id = build_alert_id(incident_id)

    policy = next(
        (
            candidate
            for candidate in policies
            if candidate.incident_type == record.incident_type and candidate.enabled
        ),
        None,
    )

    if policy is None:
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=None,
            status="skipped",
            reason="No enabled alert policy matches this incident type.",
        )

    if confidence_rank(record.confidence) < confidence_rank(policy.min_confidence):
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=policy.policy_id,
            status="skipped",
            reason=(
                f"Incident confidence {record.confidence!r} is below policy "
                f"threshold {policy.min_confidence!r}."
            ),
        )

    if already_fired:
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=policy.policy_id,
            status="suppressed",
            reason="An alert has already fired for this incident.",
        )

    return AlertDecision(
        alert_id=alert_id,
        incident_id=incident_id,
        policy_id=policy.policy_id,
        status="fired",
        reason="Matching enabled policy and confidence threshold met.",
    )
```

- [x] **Step 4: Run evaluation tests**

Run:

```bash
pytest tests/unit/test_ops_alerting.py -v
```

Expected: pass.

---

## Task 3: Alert Repository

**Files:**
- Create: `javdb/storage/repos/ops_alert_repo.py`
- Create: `tests/unit/test_ops_alert_repo.py`

- [x] **Step 1: Write repository tests**

Create `tests/unit/test_ops_alert_repo.py`:

```python
from __future__ import annotations

import sqlite3

from javdb.ops.diagnosis.models import OpsAlertPolicy
from javdb.ops.diagnosis.alerting import build_alert_id
from javdb.storage.repos.ops_alert_repo import OpsAlertRepo

DDL = """
CREATE TABLE OpsAlertPolicy (
  policy_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  min_confidence TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  channels_json TEXT NOT NULL,
  updated_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_ops_alert_policy_incident_type
  ON OpsAlertPolicy(incident_type);
CREATE TABLE OpsAlertEvent (
  alert_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  policy_id TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  fired_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_ops_alert_event_incident
  ON OpsAlertEvent(incident_id);
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def _policy(incident_type: str = "failed_ingestion") -> OpsAlertPolicy:
    return OpsAlertPolicy.create(
        incident_type=incident_type,
        min_confidence="medium",
        enabled=True,
        channels=["email"],
        updated_by="admin",
    )


def test_repo_upserts_policy_idempotently():
    repo = OpsAlertRepo(_conn())

    repo.upsert_policy(_policy())
    repo.upsert_policy(_policy())
    policies = repo.list_policies()

    assert len(policies) == 1
    assert policies[0].incident_type == "failed_ingestion"


def test_repo_gets_policy_by_incident_type():
    repo = OpsAlertRepo(_conn())
    repo.upsert_policy(_policy("d1_drift"))

    found = repo.get_policy("d1_drift")
    missing = repo.get_policy("stale_session")

    assert found is not None
    assert found.incident_type == "d1_drift"
    assert missing is None


def test_repo_upserts_and_lists_events_for_incident():
    repo = OpsAlertRepo(_conn())

    decision_event_id = build_alert_id("opsinc_abc")
    from javdb.ops.diagnosis.models import OpsAlertEvent

    repo.upsert_event(
        OpsAlertEvent(
            alert_id=decision_event_id,
            incident_id="opsinc_abc",
            policy_id="opspolicy_x",
            status="fired",
            reason="fired",
            fired_at="2026-06-13T00:00:00Z",
        )
    )
    events = repo.list_events_for_incident("opsinc_abc")

    assert len(events) == 1
    assert events[0].alert_id == decision_event_id
    assert events[0].status == "fired"
```

- [x] **Step 2: Implement repository**

Create `javdb/storage/repos/ops_alert_repo.py`:

```python
"""Repository for ADR-026 Phase 4 alert policy and alert event rows."""

from __future__ import annotations

import sqlite3

from javdb.ops.diagnosis.models import OpsAlertEvent, OpsAlertPolicy

_POLICY_COLUMNS = (
    "policy_id",
    "incident_type",
    "min_confidence",
    "enabled",
    "channels_json",
    "updated_by",
    "created_at",
    "updated_at",
)

_EVENT_COLUMNS = (
    "alert_id",
    "incident_id",
    "policy_id",
    "status",
    "reason",
    "fired_at",
)


def _row_to_policy(row: sqlite3.Row) -> OpsAlertPolicy:
    data = {column: row[column] for column in _POLICY_COLUMNS}
    data["enabled"] = bool(data["enabled"])
    return OpsAlertPolicy(**data)


def _row_to_event(row: sqlite3.Row) -> OpsAlertEvent:
    return OpsAlertEvent(**{column: row[column] for column in _EVENT_COLUMNS})


class OpsAlertRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert_policy(self, policy: OpsAlertPolicy) -> None:
        values = [
            policy.policy_id,
            policy.incident_type,
            policy.min_confidence,
            1 if policy.enabled else 0,
            policy.channels_json,
            policy.updated_by,
            policy.created_at,
            policy.updated_at,
        ]
        columns = ", ".join(_POLICY_COLUMNS)
        placeholders = ", ".join(["?"] * len(_POLICY_COLUMNS))
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in _POLICY_COLUMNS
            if column not in ("policy_id", "incident_type", "created_at")
        )
        self._conn.execute(
            f"""
            INSERT INTO OpsAlertPolicy ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_type) DO UPDATE SET {updates}
            """,
            values,
        )

    def get_policy(self, incident_type: str) -> OpsAlertPolicy | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_POLICY_COLUMNS)} FROM OpsAlertPolicy WHERE incident_type = ?",
            [incident_type],
        ).fetchone()
        return None if row is None else _row_to_policy(row)

    def list_policies(self) -> list[OpsAlertPolicy]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_POLICY_COLUMNS)} FROM OpsAlertPolicy ORDER BY incident_type ASC"
        ).fetchall()
        return [_row_to_policy(row) for row in rows]

    def upsert_event(self, event: OpsAlertEvent) -> None:
        values = [getattr(event, column) for column in _EVENT_COLUMNS]
        columns = ", ".join(_EVENT_COLUMNS)
        placeholders = ", ".join(["?"] * len(_EVENT_COLUMNS))
        updates = ", ".join(f"{column}=excluded.{column}" for column in _EVENT_COLUMNS)
        self._conn.execute(
            f"""
            INSERT INTO OpsAlertEvent ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_id) DO UPDATE SET {updates}
            WHERE status != 'fired'
            """,
            values,
        )

    def list_events_for_incident(self, incident_id: str) -> list[OpsAlertEvent]:
        rows = self._conn.execute(
            f"""
            SELECT {', '.join(_EVENT_COLUMNS)}
            FROM OpsAlertEvent
            WHERE incident_id = ?
            ORDER BY fired_at ASC
            """,
            [incident_id],
        ).fetchall()
        return [_row_to_event(row) for row in rows]
```

- [x] **Step 3: Run repository tests**

Run:

```bash
pytest tests/unit/test_ops_alert_repo.py -v
```

Expected: pass.

---

## Task 4: Service Integration

**Files:**
- Modify: `javdb/ops/diagnosis/service.py`
- Modify: `tests/unit/test_ops_diagnosis_service.py`

- [x] **Step 1: Add service tests**

Add to `tests/unit/test_ops_diagnosis_service.py`:

```python
def test_service_fires_alert_and_reuses_adr039_dispatch(monkeypatch):
    from javdb.ops.diagnosis import service as service_module
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    sent = []

    def fake_dispatch(message):
        sent.append(message)

    # Reuse the EXISTING ADR-039 notify dispatch entry point.
    monkeypatch.setattr(service_module, "dispatch_notify_message", fake_dispatch)

    class AlertRepo:
        def __init__(self):
            self.events = []

        def list_policies(self):
            return [
                OpsAlertPolicy.create(
                    incident_type="failed_ingestion",
                    min_confidence="low",
                    enabled=True,
                    channels=["email"],
                )
            ]

        def list_events_for_incident(self, incident_id):
            return []

        def upsert_event(self, event):
            self.events.append(event)

    incident_repo = CapturingRepo()
    alert_repo = AlertRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_result="failure",
        session_id="20260613T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(
        bundle,
        repo=incident_repo,
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert record.incident_type == "failed_ingestion"
    assert len(sent) == 1  # delivery delegated to ADR-039, called exactly once
    assert alert_repo.events
    assert alert_repo.events[0].status == "fired"


def test_service_alert_delivery_failure_is_best_effort(monkeypatch):
    from javdb.ops.diagnosis import service as service_module
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    def boom(_message):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(service_module, "dispatch_notify_message", boom)

    class AlertRepo:
        def __init__(self):
            self.events = []

        def list_policies(self):
            return [OpsAlertPolicy.create(incident_type="failed_ingestion", min_confidence="low")]

        def list_events_for_incident(self, incident_id):
            return []

        def upsert_event(self, event):
            self.events.append(event)

    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_result="failure",
        session_id="20260613T120000.000000Z-0001-0001",
    )

    # Must not raise even though delivery failed.
    record = diagnose_incident(
        bundle,
        repo=CapturingRepo(),
        alert_repo=AlertRepo(),
        generate_alerts=True,
    )

    assert record is not None


def test_service_does_not_reimplement_delivery():
    """Guard: the service must delegate delivery to ADR-039, not send directly."""
    import inspect

    from javdb.ops.diagnosis import service as service_module

    source = inspect.getsource(service_module)
    # The alert path must go through the ADR-039 dispatch symbol, and must not
    # construct SMTP/email transport itself.
    assert "dispatch_notify_message" in source
    assert "smtplib" not in source
```

- [x] **Step 2: Wire opt-in alert evaluation**

Modify `javdb/ops/diagnosis/service.py`:

```python
import logging

from javdb.integrations.notify import dispatch as notify_dispatch
from javdb.integrations.notify.plugin import NotifyMessage
from javdb.ops.diagnosis.alerting import evaluate_alert
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_alert_repo import OpsAlertRepo

logger = logging.getLogger(__name__)

dispatch_notify_message = notify_dispatch.send

_CONFIDENCE_TO_LEVEL = {"low": "info", "medium": "warning", "high": "error"}


def _build_alert_message(record: OpsIncidentRecord) -> NotifyMessage:
    level = _CONFIDENCE_TO_LEVEL.get(record.confidence, "warning")
    subject = f"[ops-alert] {record.incident_type} ({record.confidence})"
    body = (
        f"Incident {record.incident_id} ({record.incident_type}) was detected with "
        f"confidence {record.confidence}.\n"
        f"Session: {record.session_id or 'n/a'}  Run: {record.run_id or 'n/a'}"
    )
    return NotifyMessage(subject=subject, body=body, level=level)


def _maybe_alert(record: OpsIncidentRecord, alert_repo: object | None) -> None:
    """Best-effort: evaluate, deliver via ADR-039 dispatch, and audit.

    A delivery failure must not crash diagnosis. This function never executes
    remediation and never performs its own SMTP/channel delivery.
    """
    def evaluate_with_repo(repo: object) -> None:
        policies = repo.list_policies()
        already_fired = any(
            event.status == "fired"
            for event in repo.list_events_for_incident(record.incident_id)
        )
        decision = evaluate_alert(record, policies, already_fired=already_fired)
        if decision.status == "fired":
            try:
                dispatch_notify_message(_build_alert_message(record))
            except Exception:  # best-effort: delivery must not crash diagnosis
                logger.warning("Alert delivery failed for %s", record.incident_id, exc_info=True)
        repo.upsert_event(decision.to_event())

    try:
        if alert_repo is not None:
            evaluate_with_repo(alert_repo)
            return
        with get_db(REPORTS_DB_PATH) as conn:
            evaluate_with_repo(OpsAlertRepo(conn))
    except Exception:
        logger.exception(
            "Failed to evaluate or persist alert event (non-critical); "
            "continuing diagnosis: incident_id=%s",
            record.incident_id,
        )
```

Extend `diagnose_incident(...)`:

```python
def diagnose_incident(
    bundle: IncidentBundle,
    *,
    synthesizer: Synthesizer | None = None,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
    remediation_repo: object | None = None,
    generate_remediation: bool = False,
    alert_repo: object | None = None,
    generate_alerts: bool = False,
) -> OpsIncidentRecord:
    detector_result = detect_incident(bundle)
    result = (synthesizer or synthesize_with_configured_ai)(bundle, detector_result)
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result)
    persisted = persist_incident(record, repo=repo, jsonl_path=jsonl_path)
    if generate_remediation and persisted.persistence_status == "d1_written":
        _persist_proposals(persisted, remediation_repo)
    if generate_alerts and persisted.persistence_status == "d1_written":
        _maybe_alert(persisted, alert_repo)
    return persisted
```

> Note: import ADR-039 dispatch as `from javdb.integrations.notify import dispatch as notify_dispatch`, then alias `dispatch_notify_message = notify_dispatch.send`. Do NOT reimplement delivery. Keep the service code and the guard test in Step 1 stable through the alias.

- [x] **Step 3: Keep workflow default unchanged**

Do not change Phase 1 workflow steps to pass `generate_alerts=True` in this task. Alert generation should be enabled explicitly after the alert ledger exists in D1 and operators have configured at least one policy.

- [x] **Step 4: Run service tests**

Run:

```bash
pytest tests/unit/test_ops_diagnosis_service.py -v
```

Expected: pass.

---

## Task 5: Python API Alert Surface

**Files:**
- Modify: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/routers/diagnostics.py`
- Modify: `tests/unit/test_ops_diagnostics_api.py`

- [x] **Step 1: Add API tests**

Add to `tests/unit/test_ops_diagnostics_api.py`:

```python
def test_ops_alert_policies_returns_items(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    policy = OpsAlertPolicy.create(
        incident_type="failed_ingestion",
        min_confidence="medium",
        enabled=True,
        channels=["email"],
    )
    monkeypatch.setattr(diagnostics, "_list_alert_policies", lambda: [policy])

    response = admin_client.get("/api/diag/alert-policies")

    assert response.status_code == 200
    assert response.json()["items"][0]["incident_type"] == "failed_ingestion"
    assert response.json()["items"][0]["channels"] == ["email"]


def test_ops_alert_policy_upsert_requires_admin(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    policy = OpsAlertPolicy.create(
        incident_type="failed_ingestion",
        min_confidence="high",
        enabled=True,
        channels=["email"],
    )
    monkeypatch.setattr(diagnostics, "_upsert_alert_policy", lambda *_a, **_k: policy)

    response = admin_client.put(
        "/api/diag/alert-policies/failed_ingestion",
        json={"min_confidence": "high", "enabled": True, "channels": ["email"]},
    )

    assert response.status_code == 200
    assert response.json()["min_confidence"] == "high"


def test_ops_alert_events_returns_items(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertEvent

    event = OpsAlertEvent(
        alert_id="opsalert_test",
        incident_id="opsinc_test",
        policy_id="opspolicy_test",
        status="fired",
        reason="fired",
        fired_at="2026-06-13T00:00:00Z",
    )
    monkeypatch.setattr(diagnostics, "_list_alert_events", lambda _incident_id: [event])

    response = admin_client.get("/api/diag/ops-incidents/opsinc_test/alert-events")

    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "fired"
```

- [x] **Step 2: Add schemas**

Add to `apps/api/schemas/diagnostics.py`:

```python
class OpsAlertPolicySchema(BaseModel):
    policy_id: str
    incident_type: str
    min_confidence: str
    enabled: bool
    channels: list[str]
    updated_by: Optional[str] = None
    created_at: str
    updated_at: str


class OpsAlertPolicyListResponse(BaseModel):
    items: list[OpsAlertPolicySchema]


class OpsAlertPolicyUpsertRequest(BaseModel):
    min_confidence: Literal["low", "medium", "high"] = "medium"
    enabled: bool = True
    channels: list[str] = Field(default_factory=list)


class OpsAlertEventSchema(BaseModel):
    alert_id: str
    incident_id: str
    policy_id: Optional[str] = None
    status: str
    reason: Optional[str] = None
    fired_at: str


class OpsAlertEventListResponse(BaseModel):
    items: list[OpsAlertEventSchema]
```

Add the schema names to `__all__`.

- [x] **Step 3: Add router helpers**

Add to `apps/api/routers/diagnostics.py`:

```python
from javdb.ops.diagnosis.models import OpsAlertPolicy
from javdb.storage.repos.ops_alert_repo import OpsAlertRepo


def _policy_to_schema(policy) -> OpsAlertPolicySchema:
    return OpsAlertPolicySchema(
        policy_id=policy.policy_id,
        incident_type=policy.incident_type,
        min_confidence=policy.min_confidence,
        enabled=policy.enabled,
        channels=[
            item for item in _json_list_field(policy.channels_json)
            if isinstance(item, str)
        ],
        updated_by=policy.updated_by,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _event_to_schema(event) -> OpsAlertEventSchema:
    return OpsAlertEventSchema(
        alert_id=event.alert_id,
        incident_id=event.incident_id,
        policy_id=event.policy_id,
        status=event.status,
        reason=event.reason,
        fired_at=event.fired_at,
    )


def _list_alert_policies():
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsAlertRepo(conn).list_policies()


def _upsert_alert_policy(
    incident_type: str,
    *,
    min_confidence: str,
    enabled: bool,
    channels: list[str],
    updated_by: str,
):
    policy = OpsAlertPolicy.create(
        incident_type=incident_type,
        min_confidence=min_confidence,
        enabled=enabled,
        channels=channels,
        updated_by=updated_by,
    )
    with get_db(REPORTS_DB_PATH) as conn:
        repo = OpsAlertRepo(conn)
        repo.upsert_policy(policy)
        return repo.get_policy(incident_type)


def _list_alert_events(incident_id: str):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsAlertRepo(conn).list_events_for_incident(incident_id)
```

- [x] **Step 4: Add endpoints**

Add to `apps/api/routers/diagnostics.py`:

```python
@router.get("/alert-policies", response_model=OpsAlertPolicyListResponse)
def list_ops_alert_policies(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsAlertPolicyListResponse:
    return OpsAlertPolicyListResponse(
        items=[_policy_to_schema(item) for item in _list_alert_policies()]
    )


@router.put("/alert-policies/{incident_type}", response_model=OpsAlertPolicySchema)
def upsert_ops_alert_policy(
    incident_type: str,
    body: OpsAlertPolicyUpsertRequest,
    current: Dict[str, Any] = Depends(require_role("admin")),
) -> OpsAlertPolicySchema:
    policy = _upsert_alert_policy(
        incident_type,
        min_confidence=body.min_confidence,
        enabled=body.enabled,
        channels=body.channels,
        updated_by=str(current.get("sub") or "unknown"),
    )
    if policy is None:
        raise HTTPException(status_code=500, detail="Failed to persist alert policy")
    return _policy_to_schema(policy)


@router.get(
    "/ops-incidents/{incident_id}/alert-events",
    response_model=OpsAlertEventListResponse,
)
def list_ops_alert_events(
    incident_id: str,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsAlertEventListResponse:
    return OpsAlertEventListResponse(
        items=[_event_to_schema(item) for item in _list_alert_events(incident_id)]
    )
```

These endpoints only read policies/events and upsert a policy. They must not trigger delivery, remediation, or any execution.

- [x] **Step 5: Run API tests**

Run:

```bash
pytest tests/unit/test_ops_diagnostics_api.py -v
```

Expected: pass.

- [x] **Step 6: Add contract-first capability and OpenAPI coverage**

Add `features.ops_alerting` to the Python capabilities schema/router. The
capability must return `true` only when both `OpsAlertPolicy` and
`OpsAlertEvent` are queryable in `REPORTS_DB`; any probe exception returns
`false`.

Regenerate `docs/api/openapi.json` with:

```bash
python -m apps.cli.ops.dump_openapi
```

The published schema includes `ops_alerting`, `OpsAlertPolicy*`,
`OpsAlertEvent*`, `GET /api/diag/alert-policies`,
`PUT /api/diag/alert-policies/{incident_type}`, and
`GET /api/diag/ops-incidents/{incident_id}/alert-events`.

---

## Task 6: Cloudflare Worker API Parity

**Files:**
- Modify: `../../../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts`
- Modify: `../../../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts`
- Modify: `../../../JAVDB_AutoSpider_Web/server/routes/capabilities.ts`
- Modify: `../../../JAVDB_AutoSpider_Web/server/contract/sql-contract.gen.ts`
- Modify: `javdb/storage/contract/fragments.py`
- Modify: `docs/api/contract/sql-contract.gen.ts`

- [x] **Step 1: Add Worker tests**

Add to `server/__tests__/diagnostics-routes.test.ts` in the Web repo:

```ts
async function seedAlertTables(db: D1Database) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS OpsAlertPolicy (
      policy_id TEXT PRIMARY KEY,
      incident_type TEXT NOT NULL,
      min_confidence TEXT NOT NULL,
      enabled INTEGER NOT NULL,
      channels_json TEXT NOT NULL,
      updated_by TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
  `).run();
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS OpsAlertEvent (
      alert_id TEXT PRIMARY KEY,
      incident_id TEXT NOT NULL,
      policy_id TEXT,
      status TEXT NOT NULL,
      reason TEXT,
      fired_at TEXT NOT NULL
    )
  `).run();
  await db.prepare("DELETE FROM OpsAlertPolicy").run();
  await db.prepare("DELETE FROM OpsAlertEvent").run();
}

it("GET /api/diag/alert-policies returns mapped policies without channels_json", async () => {
  await seedAlertTables(env.REPORTS_DB);
  const token = await getToken();

  const res = await app.request("/api/diag/alert-policies", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.items[0].channels).toEqual(["email", "github_issue"]);
  expect(data.items[0]).not.toHaveProperty("channels_json");
});

it("PUT /api/diag/alert-policies/:incident_type upserts as admin and returns the mapped policy", async () => {
  await seedAlertTables(env.REPORTS_DB);
  const { token, csrfToken, csrfCookie } = await getCsrf();

  const res = await app.request("/api/diag/alert-policies/failed_ingestion", {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      Cookie: csrfCookie,
    },
    body: JSON.stringify({ min_confidence: "high", enabled: true, channels: ["email"] }),
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.min_confidence).toBe("high");
  expect(data.channels).toEqual(["email"]);
});

it("PUT /api/diag/alert-policies/:incident_type rejects invalid min_confidence with 422", async () => {
  // Seed table, call PUT with min_confidence='urgent', assert 422.
});

it("PUT /api/diag/alert-policies/:incident_type rejects readonly users", async () => {
  // Log in as readonly, include CSRF headers, assert 403.
});

it("GET /api/diag/ops-incidents/:id/alert-events returns events", async () => {
  await seedAlertTables(env.REPORTS_DB);
  const token = await getToken();

  const res = await app.request("/api/diag/ops-incidents/opsinc_test/alert-events", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.items[0].alert_id).toBe("opsalert_test");
});
```

- [x] **Step 2: Add ADR-055 SQL fragments and Worker mapping helpers**

Register the Worker-shared alert SQL in `javdb/storage/contract/fragments.py`
and regenerate `docs/api/contract/sql-contract.gen.ts`, then vendor the
generated artifact to `server/contract/sql-contract.gen.ts`.

The Worker route must import and call generated helpers:

```ts
import {
  prepareOpsAlertEventsListByIncident,
  prepareOpsAlertPoliciesList,
  prepareOpsAlertPolicyGetByIncidentType,
  prepareOpsAlertPolicyUpsert,
} from "../contract/sql-contract.gen";
```

Required generated fragments:

- `ops_alert_policy_upsert`
- `ops_alert_policy_get_by_incident_type`
- `ops_alert_policies_list`
- `ops_alert_events_list_by_incident`
- `ops_alert_policy_probe`
- `ops_alert_event_probe`

The upsert fragment uses `REPORTS_DB`, `ON CONFLICT(incident_type)`, preserves
`policy_id`, `incident_type`, and `created_at`, and sets timestamps with D1 SQL
`strftime('%Y-%m-%dT%H:%M:%fZ','now')`. Do not hand-copy these static alert
queries into Worker routes.

Modify `server/routes/diagnostics.ts`:

```ts
function mapAlertPolicy(row: any) {
  return {
    policy_id: row.policy_id,
    incident_type: row.incident_type,
    min_confidence: row.min_confidence,
    enabled: row.enabled === 1 || row.enabled === true,
    channels: parseJsonArray(row.channels_json),
    updated_by: row.updated_by ?? null,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

function mapAlertEvent(row: any) {
  return {
    alert_id: row.alert_id,
    incident_id: row.incident_id,
    policy_id: row.policy_id ?? null,
    status: row.status,
    reason: row.reason ?? null,
    fired_at: row.fired_at,
  };
}

function buildAlertPolicyId(incidentType: string): string {
  // Mirror javdb.ops.diagnosis.models.build_alert_policy_id (sha256 of
  // "alertpolicy|<incident_type>", first 24 hex chars).
  return `opspolicy_${sha256Hex(`alertpolicy|${incidentType}`).slice(0, 24)}`;
}
```

> The Worker already needs a sha256 hex helper for parity with Python id derivation. If one does not exist, add a small `sha256Hex` helper (Web Crypto `crypto.subtle.digest`) alongside the other helpers and reuse it.

- [x] **Step 3: Add Worker alert routes**

Add to `server/routes/diagnostics.ts`:

```ts
diagnosticsRoutes.get("/alert-policies", async (c) => {
  const rows = await prepareOpsAlertPoliciesList(c.env.REPORTS_DB, {}).all<OpsAlertPolicyRow>();
  return c.json({ items: rows.results.map(mapAlertPolicy) });
});

diagnosticsRoutes.put("/alert-policies/:incident_type", requireRole("admin"), async (c) => {
  const incidentType = c.req.param("incident_type");
  const body = await c.req.json<{ min_confidence?: string; enabled?: boolean; channels?: string[] }>();
  const minConfidence = body.min_confidence ?? "medium";
  if (!["low", "medium", "high"].includes(minConfidence)) {
    throw new HTTPException(422, { message: "min_confidence must be low, medium, or high" });
  }
  const user = c.get("user");
  const policyId = await buildAlertPolicyId(incidentType);
  await prepareOpsAlertPolicyUpsert(c.env.REPORTS_DB, {
    policyId,
    incidentType,
    minConfidence,
    enabled: body.enabled === false ? 0 : 1,
    channelsJson: JSON.stringify(body.channels ?? []),
    updatedBy: user.sub,
  }).run();
  const row = await prepareOpsAlertPolicyGetByIncidentType(c.env.REPORTS_DB, { incidentType }).first();
  if (!row) throw new HTTPException(500, { message: "Failed to persist alert policy" });
  return c.json(mapAlertPolicy(row));
});

diagnosticsRoutes.get("/ops-incidents/:incident_id/alert-events", async (c) => {
  const incidentId = c.req.param("incident_id");
  const rows = await prepareOpsAlertEventsListByIncident(c.env.REPORTS_DB, { incidentId }).all();
  return c.json({ items: rows.results.map(mapAlertEvent) });
});
```

Do not call notify delivery, GitHub Actions, remediation, or any execution from these routes. The Worker only reads/writes alert config and reads alert events.

- [x] **Step 4: Add Worker capability probe**

Add `features.ops_alerting` to the Worker capabilities response. It returns
`true` only when both `OpsAlertPolicy` and `OpsAlertEvent` are queryable in
`REPORTS_DB`; any probe exception returns `false`. The two probe SELECTs are
also generated from the ADR-055 contract registry, not hand-copied into the
Worker route.

- [x] **Step 5: Run Worker tests**

Run from the Web repo:

```bash
SQL_CONTRACT_PATH=/Users/tedwu/.codex/worktrees/2139/JAVDB_AutoSpider_CICD/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
npm run typecheck:server
```

Expected: pass.

---

## Task 7: Web Alerting Config Panel

> Backend-agent handoff status (2026-06-15): this task is intentionally handed
> to Agent F because the backend agent is scoped to MAIN plus Web `server/`
> only. Do not implement or modify Web `src/` in this backend slice. The
> frontend contract is already published via `docs/api/openapi.json`, Python
> `/api/diag/*` endpoints, `features.ops_alerting`, and the Cloudflare Worker
> `server/` mirror.

**Files:**
- Modify: `../../../JAVDB_AutoSpider_Web/src/api/diagnostics.ts`
- Create: `../../../JAVDB_AutoSpider_Web/src/components/diagnostics/AlertPolicyPanel.vue`
- Modify: `../../../JAVDB_AutoSpider_Web/src/pages/diagnostics/OpsIncidentsPage.vue`
- Create: `../../../JAVDB_AutoSpider_Web/tests/unit/ops-alerting-api.spec.ts`

- [x] **Step 0: Backend contract handoff to frontend agent**

Published for Agent F:

- OpenAPI: `docs/api/openapi.json`
- Capability: `features.ops_alerting`
- Python routes: `GET /api/diag/alert-policies`,
  `PUT /api/diag/alert-policies/{incident_type}`,
  `GET /api/diag/ops-incidents/{incident_id}/alert-events`
- Worker mirror: matching Web `server/` routes and generated SQL-contract
  helpers.

The remaining Task 7 `src/` UI/API-client implementation is out of this
backend-agent scope and remains with the frontend agent.

- [ ] **Step 1: Add frontend API tests**

Create `tests/unit/ops-alerting-api.spec.ts` in the Web repo:

```ts
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import {
  listAlertEvents,
  listAlertPolicies,
  upsertAlertPolicy,
} from '@/api/diagnostics'

describe('ops alerting config API', () => {
  it('lists alert policies', async () => {
    const spy = vi.spyOn(http, 'get').mockResolvedValueOnce({ data: { items: [] } })

    await listAlertPolicies()

    expect(spy).toHaveBeenCalledWith('/api/diag/alert-policies')
  })

  it('upserts an alert policy', async () => {
    const spy = vi.spyOn(http, 'put').mockResolvedValueOnce({
      data: { incident_type: 'failed_ingestion', min_confidence: 'high', enabled: true, channels: ['email'] },
    })

    const result = await upsertAlertPolicy('failed_ingestion', {
      min_confidence: 'high',
      enabled: true,
      channels: ['email'],
    })

    expect(result.min_confidence).toBe('high')
    expect(spy).toHaveBeenCalledWith('/api/diag/alert-policies/failed_ingestion', {
      min_confidence: 'high',
      enabled: true,
      channels: ['email'],
    })
  })

  it('lists alert events for an incident', async () => {
    const spy = vi.spyOn(http, 'get').mockResolvedValueOnce({ data: { items: [] } })

    await listAlertEvents('opsinc_test')

    expect(spy).toHaveBeenCalledWith('/api/diag/ops-incidents/opsinc_test/alert-events')
  })
})
```

- [ ] **Step 2: Add frontend API contracts**

Modify `src/api/diagnostics.ts` in the Web repo:

```ts
export interface OpsAlertPolicy {
  policy_id: string
  incident_type: string
  min_confidence: 'low' | 'medium' | 'high'
  enabled: boolean
  channels: string[]
  updated_by?: string | null
  created_at: string
  updated_at: string
}

export interface OpsAlertPolicyListResponse {
  items: OpsAlertPolicy[]
}

export interface AlertPolicyUpsertRequest {
  min_confidence: 'low' | 'medium' | 'high'
  enabled: boolean
  channels: string[]
}

export interface OpsAlertEvent {
  alert_id: string
  incident_id: string
  policy_id?: string | null
  status: 'fired' | 'suppressed' | 'skipped'
  reason?: string | null
  fired_at: string
}

export interface OpsAlertEventListResponse {
  items: OpsAlertEvent[]
}

export async function listAlertPolicies(): Promise<OpsAlertPolicyListResponse> {
  const { data } = await http.get<OpsAlertPolicyListResponse>('/api/diag/alert-policies')
  return data
}

export async function upsertAlertPolicy(
  incidentType: string,
  body: AlertPolicyUpsertRequest,
): Promise<OpsAlertPolicy> {
  const { data } = await http.put<OpsAlertPolicy>(`/api/diag/alert-policies/${incidentType}`, body)
  return data
}

export async function listAlertEvents(incidentId: string): Promise<OpsAlertEventListResponse> {
  const { data } = await http.get<OpsAlertEventListResponse>(
    `/api/diag/ops-incidents/${incidentId}/alert-events`,
  )
  return data
}
```

- [ ] **Step 3: Build the alerting config panel**

Create `src/components/diagnostics/AlertPolicyPanel.vue`:

- Load alert policies via `listAlertPolicies()` on mount.
- Render one row per incident type with: an enable toggle, a confidence-threshold select (`low`/`medium`/`high`), and a channels multi-select.
- On save, call `upsertAlertPolicy(incidentType, { min_confidence, enabled, channels })`, then reload.
- The panel only configures policies. It must not trigger delivery, send a test notification, or call any execution endpoint.

- [ ] **Step 4: Show alert status on incident detail**

Modify `src/pages/diagnostics/OpsIncidentsPage.vue`:

- Mount `AlertPolicyPanel` in the page (for example, in a settings drawer or a panel at the top of the incidents view).
- When the selected incident changes, load its alert events via `listAlertEvents(incidentId)`.
- Render an alert-status badge on the incident detail derived from the latest event: `fired` (e.g. success/info), `suppressed` (neutral), `skipped` (neutral), or "no alert" when there are no events.
- Alert-event consumption is read-only. Do not add controls that send or re-send notifications from this page.

- [ ] **Step 5: Run Web unit test**

Run from the Web repo:

```bash
npm run test:unit -- tests/unit/ops-alerting-api.spec.ts
```

Expected: pass.

---

## Task 8: Documentation

**Files:**
- Modify: `docs/handbook/en/ops/troubleshooting.md`
- Modify: `docs/handbook/zh/ops/troubleshooting.md`

- [x] **Step 1: Document alerting policy semantics in English**

Add to `docs/handbook/en/ops/troubleshooting.md`:

````markdown
### Proactive Incident Alerting

ADR-026 Phase 4 can proactively alert operators when an incident is detected. After an incident is persisted to D1, a deterministic policy decides whether to alert. Delivery itself is handled by the existing ADR-039 notify dispatch; this layer only decides whether to fire and records what happened. It does not run remediation.

Alert policies are operator-tunable per incident type:

- `enabled` - whether this incident type alerts at all.
- `min_confidence` - alert only when the diagnosis confidence is at least this level (`low` < `medium` < `high`).
- `channels` - optional ADR-039 backend names to allow for this policy. Empty means use the normal ADR-039 active backend list; non-empty values filter the dispatch through ADR-039's existing backend selection.

Alert event states:

- `fired` - a matching enabled policy existed, confidence met the threshold, and no alert had already fired for this incident; delivery was handed to ADR-039.
- `suppressed` - an alert had already fired for this incident (deduplicated on `incident_id`).
- `skipped` - no matching enabled policy, or the incident confidence was below the policy threshold.

Alerting is opt-in and best-effort: a delivery failure is logged but does not crash diagnosis, and the alert event is still recorded for audit.
````

- [x] **Step 2: Mirror alerting policy semantics in Chinese**

Add to `docs/handbook/zh/ops/troubleshooting.md`:

````markdown
### 主动 incident 告警

ADR-026 Phase 4 可以在检测到 incident 时主动告警 operator。incident 持久化到 D1 之后，一个确定性 policy 决定是否告警。投递本身由现有的 ADR-039 notify dispatch 负责；这一层只决定是否触发并记录结果。它不执行 remediation。

Alert policy 由 operator 按 incident type 调整：

- `enabled` - 该 incident type 是否告警。
- `min_confidence` - 仅当诊断 confidence 至少达到该级别时才告警（`low` < `medium` < `high`）。
- `channels` - 该 policy 允许使用的 ADR-039 backend 名称。为空表示使用 ADR-039 常规 active backend 列表；非空时通过 ADR-039 现有 backend 选择逻辑过滤投递目标。

Alert event 状态：

- `fired` - 存在匹配且启用的 policy、confidence 达到阈值，且该 incident 此前未告警；投递已交给 ADR-039。
- `suppressed` - 该 incident 此前已经告警过（按 `incident_id` 去重）。
- `skipped` - 没有匹配且启用的 policy，或 incident confidence 低于 policy 阈值。

告警是 opt-in 且尽力而为：投递失败会记录日志但不会让诊断崩溃，alert event 仍会被记录以供审计。
````

- [x] **Step 3: Run documentation checks**

Run:

```bash
git diff --check -- \
  docs/design/ADR-026-AI-Operations-Diagnosis/IMP-ADR026-04-proactive-incident-alerting.md \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md
```

Expected: no output.

---

## Task 9: Verification And Closeout

- [x] **Step 1: Run Python tests**

Run:

```bash
pytest \
  tests/unit/test_ops_alerting.py \
  tests/unit/test_ops_alert_repo.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnostics_api.py \
  -v
```

Expected: all pass.

Verified on 2026-06-15:

```bash
PYTHONPATH=/Users/tedwu/.codex/worktrees/2139/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
  /opt/anaconda3/bin/python3 -m pytest --continue-on-collection-errors \
  tests/unit/test_ops_alerting.py \
  tests/unit/test_ops_alert_repo.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnostics_api.py \
  -v
```

Result: 54 passed.

Also verified contract/capability/OpenAPI coverage:

```bash
PYTHONPATH=/Users/tedwu/.codex/worktrees/2139/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
  /opt/anaconda3/bin/python3 -m pytest --continue-on-collection-errors \
  tests/unit/test_ops_alerting_capability_probe.py \
  tests/unit/test_contract_types.py \
  tests/unit/test_contract_fragments.py \
  tests/unit/test_dump_sql_contract.py \
  tests/unit/test_sql_contract_freshness.py \
  tests/integration/test_capabilities_endpoint.py \
  tests/integration/test_openapi_response_shapes.py \
  -q
```

Result: 32 passed.

- [x] **Step 2: Run Web server tests**

Run from `../../../JAVDB_AutoSpider_Web`:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
```

Expected: pass.

Verified on 2026-06-15 with the backend-owned server mirror suite:

```bash
npm run test:server -- \
  server/__tests__/diagnostics-routes.test.ts \
  server/__tests__/capabilities-ops-alerting.test.ts \
  server/__tests__/contract-compliance.test.ts \
  server/__tests__/actor-subscription-contract.test.ts \
  server/__tests__/system-state-contract.test.ts \
  server/__tests__/content-filter-contract.test.ts
```

Result: 6 files / 44 tests passed.

- [ ] **Step 2b: Run frontend tests (Agent F scope)**

Run from `../../../JAVDB_AutoSpider_Web` after Agent F implements the Web `src/`
alerting UI/API client:

```bash
npm run test:unit -- tests/unit/ops-alerting-api.spec.ts
```

Expected: all pass.

- [x] **Step 3: Run static checks**

Run from the main repo:

```bash
python3 -m compileall javdb/ops/diagnosis javdb/storage/repos apps/api/routers/diagnostics.py apps/api/schemas/diagnostics.py
git diff --check
```

Run from the Web repo:

```bash
npm run typecheck
npm run lint
```

Expected: no failures.

Verified on 2026-06-15 for the backend/server-owned scope:

```bash
python3 -m compileall \
  javdb/ops/diagnosis \
  javdb/storage/repos \
  apps/api/routers/diagnostics.py \
  apps/api/schemas/diagnostics.py \
  apps/api/routers/capabilities.py \
  apps/api/schemas/capabilities_payloads.py \
  apps/cli/ops/dump_sql_contract.py
git diff --check -- . ':!reports/**'
python3 scripts/ci/validate_d1_write_class.py --paths \
  javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql
```

Result: compileall passed; diff check had no output; D1 Write-Class validation
passed.

```bash
npm run typecheck:server
npx eslint server --ext .ts,.tsx,.js,.mjs
git diff --check -- server
```

Result: server typecheck, server-scoped lint, and server diff check passed.

Full Web `npm run lint` was not used as the backend gate because it traverses
pre-existing `.worktrees/` and unrelated frontend files outside this backend
slice.

- [ ] **Step 4: Manual safety smoke**

Open the Web UI at:

```text
http://localhost:5173/diag/ops-incidents
```

Expected:

- The alerting config panel lists incident types with enable toggle, confidence threshold, and channels.
- Saving a policy persists it and reloads.
- Incident detail shows an alert-status badge derived from alert events (`fired` / `suppressed` / `skipped` / no alert).
- No UI element sends or re-sends a notification directly; delivery stays in ADR-039.
- No UI element executes remediation, rollback, rerun, drift apply, qB cleanup, or recovery resolve.

- [x] **Step 5: Commit**

Commit only the Phase 4 source, tests, and docs. Do not commit `reports/` data files.

```bash
git add \
  javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql \
  javdb/storage/db/_db_migrations.py \
  javdb/ops/diagnosis/models.py \
  javdb/ops/diagnosis/alerting.py \
  javdb/ops/diagnosis/service.py \
  javdb/ops/diagnosis/__init__.py \
  javdb/storage/repos/ops_alert_repo.py \
  javdb/storage/contract/types.py \
  javdb/storage/contract/fragments.py \
  javdb/storage/contract/__init__.py \
  apps/cli/ops/dump_sql_contract.py \
  apps/api/schemas/diagnostics.py \
  apps/api/routers/diagnostics.py \
  apps/api/schemas/capabilities_payloads.py \
  apps/api/routers/capabilities.py \
  docs/api/contract/sql-contract.gen.ts \
  docs/api/openapi.json \
  tests/unit/test_contract_types.py \
  tests/unit/test_contract_fragments.py \
  tests/unit/test_dump_sql_contract.py \
  tests/unit/test_ops_alerting_capability_probe.py \
  tests/integration/test_capabilities_endpoint.py \
  tests/integration/test_openapi_response_shapes.py \
  tests/unit/test_ops_alerting.py \
  tests/unit/test_ops_alert_repo.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnostics_api.py \
  docs/design/ADR-026-AI-Operations-Diagnosis/IMP-ADR026-04-proactive-incident-alerting.md \
  docs/design/ADR-054-User-Intent-Discovery-Layer/EXECUTION-TRACKER.md \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md
git commit -m "feat(ops): add proactive incident alerting and operator config"
```

Commit the backend-owned Web `server/` mirror changes separately:

```bash
cd ../../../JAVDB_AutoSpider_Web
git add \
  server/routes/diagnostics.ts \
  server/routes/capabilities.ts \
  server/contract/sql-contract.gen.ts \
  server/__tests__/diagnostics-routes.test.ts \
  server/__tests__/capabilities-ops-alerting.test.ts \
  server/__tests__/contract-compliance.test.ts
git commit -m "feat(api): mirror proactive incident alerting routes"
```
