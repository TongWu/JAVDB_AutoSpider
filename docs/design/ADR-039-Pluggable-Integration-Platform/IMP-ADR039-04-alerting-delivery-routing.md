# IMP-ADR039-04: ADR-039 Phase 4 - Alert Delivery: Routing, Digest & Web Control Surface

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-039](ADR-039-pluggable-integration-platform.md) (umbrella) — this is **Phase 4** of the roadmap (the reserved Phase 3 — Ecosystem slot is left intact and deferred).

**Goal:** Ship ADR-039 Phase 4 by adding a delivery/routing layer *above* the existing `notify.send` fan-out: a built-in `WebhookNotifyPlugin`, D1-stored routing rules that map `(level, source) → backends` (with the current fan-out preserved as the default), an optional digest queue with a `flush_digest` aggregator, and an operator control surface (Python API + Worker parity + Web panel + test-send).

**Architecture:** The Phase 1 `NotifyPlugin` / `NotifyMessage` / `NotifyResult` contract is **sufficient and is not changed**. Routing and digest are a layer over `notify.dispatch.send`: `dispatch(message, source=None)` consults enabled `NotifyRoutingRule` rows to select backends, falling back to the Phase 1 "fan out to all active backends" behaviour when no rule matches (so every existing caller is unchanged). The digest queue accumulates messages tagged for digest and `flush_digest(window)` composes one summary `NotifyMessage` and dispatches it. The operator surface exposes routing rules, digest settings, the webhook URL config key, and a test-send that runs a `NotifyMessage` through routing.

**Tech Stack:** Python 3.11, Cloudflare D1 (operations DB), FastAPI/Pydantic, `requests` (webhook POST), pytest, TypeScript, Hono, Vue 3, Naive UI, Vitest, Markdown docs.

**Source spec:** [ADR-039](ADR-039-pluggable-integration-platform.md), Implementation Roadmap (Phase 4 row), D2/D3/D4.

**Non-negotiable:** This phase owns **delivery, routing, digest, webhook, and operator config only**. It must NOT detect incidents (that is [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)), must NOT change the `NotifyPlugin` contract, and must NOT add downloader/media-server plugin categories (that is the reserved Phase 3). With zero routing rules configured, behaviour is identical to today: fan-out to all active backends.

## Table of Contents

- [File Map](#file-map)
- [Scope Boundaries](#scope-boundaries)
- [Task 1: WebhookNotifyPlugin](#task-1-webhooknotifyplugin)
- [Task 2: D1 Routing-Rule And Digest Tables](#task-2-d1-routing-rule-and-digest-tables)
- [Task 3: Routing Model, Store And Dispatch Integration](#task-3-routing-model-store-and-dispatch-integration)
- [Task 4: Digest Queue And flush_digest](#task-4-digest-queue-and-flush_digest)
- [Task 5: Registry And Config Wiring](#task-5-registry-and-config-wiring)
- [Task 6: Python API Notify-Config Surface](#task-6-python-api-notify-config-surface)
- [Task 7: Cloudflare Worker API Parity](#task-7-cloudflare-worker-api-parity)
- [Task 8: Web Notify/Alerting Config Panel](#task-8-web-notifyalerting-config-panel)
- [Task 9: Documentation](#task-9-documentation)
- [Task 10: Verification And Closeout](#task-10-verification-and-closeout)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/integrations/notify/webhook/__init__.py` | Package marker. |
| Create | `javdb/integrations/notify/webhook/plugin.py` | `WebhookNotifyPlugin` — POSTs `NotifyMessage` as JSON to a configured URL. |
| Create | `javdb/migrations/d1/2026_06_13_add_notify_routing_digest.sql` | D1-first `NotifyRoutingRule` + `NotifyDigestQueue` tables (operations DB). |
| Modify | `javdb/storage/db/_db_migrations.py` | Mirror the two tables into the local SQLite operations DDL block. |
| Create | `javdb/integrations/notify/routing.py` | `NotifyRoutingRule` model + deterministic backend selection (`select_backends`). |
| Create | `javdb/storage/repos/notify_routing_repo.py` | Repository for routing-rule rows. |
| Modify | `javdb/integrations/notify/dispatch.py` | Add `source=`/routing-aware backend selection with default fan-out fallback. |
| Create | `javdb/integrations/notify/digest.py` | `NotifyDigestEntry` model + `flush_digest(window)` aggregator. |
| Create | `javdb/storage/repos/notify_digest_repo.py` | Repository for the digest queue. |
| Modify | `javdb/integrations/notify/__init__.py` | Re-export routing/digest entry points (no contract change). |
| Modify | `config.py.example` | `NOTIFY_WEBHOOK_URL`, `NOTIFY_DIGEST_ENABLED`, `NOTIFY_DIGEST_WINDOW_MINUTES`. |
| Create | `apps/api/schemas/notify.py` | Routing-rule, digest-settings, and test-send schemas. |
| Create | `apps/api/routers/notify.py` | GET/PUT routing rules; GET/PUT digest settings; `POST /notify/test-send`. |
| Modify | `apps/api/server.py` | Register the notify router. |
| Create | `tests/unit/test_webhook_notify_plugin.py` | Webhook plugin configured/unconfigured + POST shape + contract conformance. |
| Create | `tests/unit/test_notify_routing.py` | Routing selection: rule match, wildcard, default fan-out fallback. |
| Create | `tests/unit/test_notify_routing_repo.py` | Routing-rule repository tests. |
| Create | `tests/unit/test_notify_digest.py` | Digest enqueue + `flush_digest` composition/marking tests. |
| Create | `tests/unit/test_notify_dispatch_routing.py` | `dispatch(message, source=…)` integration + backward-compat fan-out. |
| Create | `tests/unit/test_notify_config_api.py` | API tests for routing/digest/test-send endpoints. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/notify.ts` | Worker parity endpoints + SQL shapes (admin guard on writes). |
| Modify | `../JAVDB_AutoSpider_Web/server/index.ts` | Mount the notify routes. |
| Create | `../JAVDB_AutoSpider_Web/server/__tests__/notify-routes.test.ts` | Worker notify-config route tests. |
| Create | `../JAVDB_AutoSpider_Web/src/api/notify.ts` | Frontend notify-config API types + functions. |
| Create | `../JAVDB_AutoSpider_Web/src/pages/operations/NotifyRoutingPanel.vue` | Routing rules + digest toggle/window + webhook URL + test-send. |
| Modify | `../JAVDB_AutoSpider_Web/src/pages/operations/EmailPage.vue` | Mount the routing/alerting panel under the existing notify area. |
| Create | `../JAVDB_AutoSpider_Web/tests/unit/notify-config-api.spec.ts` | Frontend API client unit test. |
| Modify | `docs/handbook/en/self-hoster/configuration.md` | Document the three new config keys. |
| Modify | `docs/handbook/zh/self-hoster/configuration.md` | Chinese mirror. |
| Create | `docs/handbook/en/ops/notifications.md` | Routing/digest/webhook operator guide. |
| Create | `docs/handbook/zh/ops/notifications.md` | Chinese mirror. |

## Scope Boundaries

- Routing and digest sit **above** `notify.send`; the `NotifyPlugin` contract (`name`, `is_configured()`, `send(message)`) is unchanged.
- Default/backward-compat behaviour: with **zero** routing rules, `dispatch(message)` fans out to all active backends exactly as Phase 1 does.
- This phase does not detect incidents, raise alerts, or decide *whether* an alert is warranted — it only decides *where/how* an already-raised `NotifyMessage` is delivered. Incident detection stays in [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md).
- No downloader/media-server plugin categories — those are the reserved Phase 3 (`IMP-ADR039-03`), which this IMP must not touch or renumber.
- The digest flush is driven by a scheduled job; this IMP records the cron/CLI command but does **not** add a new GitHub Actions workflow.
- Webhook delivery is a plain JSON POST of `{subject, body, level}`; no per-backend templating in this phase.

---

## Task 1: WebhookNotifyPlugin

**Files:**
- Create: `javdb/integrations/notify/webhook/__init__.py`, `javdb/integrations/notify/webhook/plugin.py`
- Create: `tests/unit/test_webhook_notify_plugin.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_webhook_notify_plugin.py`:

```python
import javdb.integrations.notify.webhook.plugin as wh
from javdb.integrations.notify.plugin import NotifyMessage


def test_is_configured_reads_url(monkeypatch):
    monkeypatch.setattr(wh, "cfg", lambda name, default: {"NOTIFY_WEBHOOK_URL": "https://hook.example/x"}.get(name, default))
    assert wh.WebhookNotifyPlugin().is_configured() is True


def test_is_configured_false_when_url_missing(monkeypatch):
    monkeypatch.setattr(wh, "cfg", lambda name, default: default)
    assert wh.WebhookNotifyPlugin().is_configured() is False


def test_send_posts_message_as_json(monkeypatch):
    monkeypatch.setattr(wh, "cfg", lambda name, default: {"NOTIFY_WEBHOOK_URL": "https://hook.example/x"}.get(name, default))
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(wh.requests, "post", _post)
    result = wh.WebhookNotifyPlugin().send(NotifyMessage(subject="Sub", body="Body", level="error"))
    assert captured["url"] == "https://hook.example/x"
    assert captured["json"] == {"subject": "Sub", "body": "Body", "level": "error"}
    assert result.plugin == "webhook"
    assert result.ok is True


def test_send_reports_failure_on_non_2xx(monkeypatch):
    monkeypatch.setattr(wh, "cfg", lambda name, default: {"NOTIFY_WEBHOOK_URL": "https://hook.example/x"}.get(name, default))

    class _Resp:
        status_code = 500

    monkeypatch.setattr(wh.requests, "post", lambda url, **kw: _Resp())
    result = wh.WebhookNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False
    assert result.detail == "status 500"


def test_send_reports_failure_on_exception(monkeypatch):
    monkeypatch.setattr(wh, "cfg", lambda name, default: {"NOTIFY_WEBHOOK_URL": "https://hook.example/x"}.get(name, default))

    def _boom(url, **kw):
        raise RuntimeError("conn refused")

    monkeypatch.setattr(wh.requests, "post", _boom)
    result = wh.WebhookNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False
    assert "conn refused" in (result.detail or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_webhook_notify_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the plugin**

Create `javdb/integrations/notify/webhook/__init__.py`:

```python
"""Webhook notify backend (ADR-039 Phase 4)."""
```

Create `javdb/integrations/notify/webhook/plugin.py`:

```python
"""Webhook notify plugin — POSTs a NotifyMessage as JSON (ADR-039 Phase 4).

Honours the Phase 1 NotifyPlugin contract exactly; routing/digest live above this.
"""

from __future__ import annotations

import requests

from javdb.infra.config import cfg
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY


class WebhookNotifyPlugin:
    name = "webhook"

    def is_configured(self) -> bool:
        return bool(cfg("NOTIFY_WEBHOOK_URL", ""))

    def send(self, message: NotifyMessage) -> NotifyResult:
        url = cfg("NOTIFY_WEBHOOK_URL", "")
        payload = {"subject": message.subject, "body": message.body, "level": message.level}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            ok = 200 <= resp.status_code < 300
            return NotifyResult(plugin=self.name, ok=ok,
                                detail=None if ok else f"status {resp.status_code}")
        except Exception as exc:
            return NotifyResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("notify", WebhookNotifyPlugin())
```

The plugin satisfies the unchanged `NotifyPlugin` Protocol (`name`, `is_configured()`, `send(message) -> NotifyResult`). Do not alter `javdb/integrations/notify/plugin.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_webhook_notify_plugin.py -v`
Expected: PASS.

---

## Task 2: D1 Routing-Rule And Digest Tables

**Files:**
- Create: `javdb/migrations/d1/2026_06_13_add_notify_routing_digest.sql`
- Modify: `javdb/storage/db/_db_migrations.py`

- [ ] **Step 1: Create the D1 migration**

Create `javdb/migrations/d1/2026_06_13_add_notify_routing_digest.sql`:

```sql
-- 2026-06-13: Add ADR-039 Phase 4 notify routing rules + digest queue.
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_06_13_add_notify_routing_digest.sql
--
-- These tables route an already-raised NotifyMessage to backends and aggregate
-- digest entries. They do not detect incidents and do not change the
-- NotifyPlugin contract.

CREATE TABLE IF NOT EXISTS NotifyRoutingRule (
  rule_id TEXT PRIMARY KEY,
  match_level TEXT,
  match_source TEXT,
  backends_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notify_routing_enabled
  ON NotifyRoutingRule(enabled);

CREATE TABLE IF NOT EXISTS NotifyDigestQueue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT,
  subject TEXT,
  body TEXT,
  source TEXT,
  created_at TEXT NOT NULL,
  flushed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notify_digest_unflushed
  ON NotifyDigestQueue(flushed_at);
```

`NULL match_level` / `NULL match_source` are wildcards.

- [ ] **Step 2: Add local mirror DDL**

Modify `javdb/storage/db/_db_migrations.py` by appending the same two tables and indexes to `_OPERATIONS_DDL` (the operations DB block), so a force-overwrite from D1 stays aligned.

- [ ] **Step 3: Verify schema syntax locally**

Run: `python3 -m compileall javdb/storage/db/_db_migrations.py`
Expected: compile succeeds.

- [ ] **Step 4: Defer remote apply**

Record this command for rollout, but do not run it while writing the plan:

```bash
wrangler d1 execute javdb-operations --remote \
  --file=javdb/migrations/d1/2026_06_13_add_notify_routing_digest.sql
```

Expected during rollout: D1 creates `NotifyRoutingRule` and `NotifyDigestQueue`.

---

## Task 3: Routing Model, Store And Dispatch Integration

**Files:**
- Create: `javdb/integrations/notify/routing.py`
- Create: `javdb/storage/repos/notify_routing_repo.py`
- Modify: `javdb/integrations/notify/dispatch.py`
- Create: `tests/unit/test_notify_routing.py`, `tests/unit/test_notify_routing_repo.py`, `tests/unit/test_notify_dispatch_routing.py`

- [ ] **Step 1: Write routing-selection tests (backward-compat is the load-bearing case)**

Create `tests/unit/test_notify_routing.py`:

```python
from javdb.integrations.notify.routing import NotifyRoutingRule, select_backends


def _rule(rule_id, level, source, backends, enabled=True):
    return NotifyRoutingRule.create(
        rule_id=rule_id, match_level=level, match_source=source, backends=backends, enabled=enabled
    )


def test_no_rules_returns_none_meaning_default_fan_out():
    # None is the sentinel for "no rule matched -> caller keeps Phase 1 fan-out".
    assert select_backends([], level="error", source="pipeline") is None


def test_exact_level_and_source_match():
    rules = [_rule("r1", "error", "pipeline", ["email", "telegram"])]
    assert select_backends(rules, level="error", source="pipeline") == ["email", "telegram"]


def test_wildcard_level_matches_any_level():
    rules = [_rule("r1", None, "pipeline", ["webhook"])]
    assert select_backends(rules, level="info", source="pipeline") == ["webhook"]


def test_wildcard_source_matches_any_source():
    rules = [_rule("r1", "error", None, ["telegram"])]
    assert select_backends(rules, level="error", source="anything") == ["telegram"]


def test_disabled_rule_is_ignored():
    rules = [_rule("r1", "error", "pipeline", ["telegram"], enabled=False)]
    assert select_backends(rules, level="error", source="pipeline") is None


def test_more_specific_rule_wins_over_wildcard():
    rules = [
        _rule("wild", None, None, ["email"]),
        _rule("exact", "error", "pipeline", ["telegram"]),
    ]
    assert select_backends(rules, level="error", source="pipeline") == ["telegram"]
```

- [ ] **Step 2: Implement the routing model + selection**

Create `javdb/integrations/notify/routing.py`:

```python
"""Deterministic notify routing (ADR-039 Phase 4).

A NotifyRoutingRule maps (level, source) -> backend names. select_backends()
returns the backends for the best-matching enabled rule, or None when no rule
matches -- None tells dispatch to keep the Phase 1 fan-out to all active backends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from javdb.infra.config import utc_now_iso  # adjust import to the repo's iso-now helper


@dataclass(frozen=True)
class NotifyRoutingRule:
    rule_id: str
    match_level: str | None
    match_source: str | None
    backends_json: str
    enabled: bool
    created_at: str
    updated_at: str

    @classmethod
    def create(cls, *, rule_id, match_level, match_source, backends, enabled=True):
        now = utc_now_iso()
        return cls(
            rule_id=rule_id,
            match_level=match_level,
            match_source=match_source,
            backends_json=json.dumps(list(backends)),
            enabled=bool(enabled),
            created_at=now,
            updated_at=now,
        )

    @property
    def backends(self) -> list[str]:
        value = json.loads(self.backends_json or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []

    def matches(self, *, level: str, source: str | None) -> bool:
        if not self.enabled:
            return False
        if self.match_level is not None and self.match_level != level:
            return False
        if self.match_source is not None and self.match_source != source:
            return False
        return True

    def _specificity(self) -> int:
        return (1 if self.match_level is not None else 0) + (1 if self.match_source is not None else 0)


def select_backends(rules, *, level: str, source: str | None) -> list[str] | None:
    """Return backends for the most specific enabled matching rule, else None."""
    matching = [r for r in rules if r.matches(level=level, source=source)]
    if not matching:
        return None
    best = max(matching, key=lambda r: r._specificity())
    return best.backends
```

> Confirm the project's UTC-ISO helper import path during implementation (e.g. `from javdb.infra.<module> import utc_now_iso`); if none exists, inline `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")` to match the SessionId timestamp convention.

- [ ] **Step 3: Write + implement the routing repository**

Create `tests/unit/test_notify_routing_repo.py`:

```python
import sqlite3

from javdb.integrations.notify.routing import NotifyRoutingRule
from javdb.storage.repos.notify_routing_repo import NotifyRoutingRepo

DDL = """
CREATE TABLE NotifyRoutingRule (
  rule_id TEXT PRIMARY KEY,
  match_level TEXT,
  match_source TEXT,
  backends_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(DDL)
    return conn


def test_upsert_and_list_enabled():
    repo = NotifyRoutingRepo(_conn())
    repo.upsert(NotifyRoutingRule.create(rule_id="r1", match_level="error", match_source="pipeline", backends=["telegram"]))
    repo.upsert(NotifyRoutingRule.create(rule_id="r2", match_level=None, match_source=None, backends=["email"], enabled=False))

    all_rules = repo.list_all()
    assert {r.rule_id for r in all_rules} == {"r1", "r2"}
    assert [r.rule_id for r in repo.list_enabled()] == ["r1"]


def test_replace_all_rewrites_rule_set():
    repo = NotifyRoutingRepo(_conn())
    repo.upsert(NotifyRoutingRule.create(rule_id="old", match_level="info", match_source=None, backends=["email"]))
    repo.replace_all([NotifyRoutingRule.create(rule_id="new", match_level="error", match_source=None, backends=["webhook"])])
    assert [r.rule_id for r in repo.list_all()] == ["new"]
```

Create `javdb/storage/repos/notify_routing_repo.py` with `upsert`, `list_all`, `list_enabled`, and `replace_all(rules)` (used by the PUT endpoint to atomically rewrite the rule set inside one transaction). Map rows to `NotifyRoutingRule` (coerce `enabled` 0/1 ↔ bool).

- [ ] **Step 4: Wire routing into dispatch (default fan-out preserved)**

Add to `tests/unit/test_notify_dispatch_routing.py`:

```python
import javdb.integrations.notify.dispatch as dispatch
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import PluginRegistry


class _Plugin:
    def __init__(self, name):
        self.name = name
    def is_configured(self):
        return True
    def send(self, message):
        return NotifyResult(plugin=self.name, ok=True)


def _registry(*names):
    reg = PluginRegistry()
    for n in names:
        reg.register("notify", _Plugin(n))
    return reg


def test_dispatch_without_rules_fans_out_to_all_active(monkeypatch):
    # Backward-compat: zero routing rules => identical to Phase 1 fan-out.
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["email", "telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry("email", "telegram"))
    monkeypatch.setattr(dispatch, "_load_routing_rules", lambda: [])
    results = {r.plugin for r in dispatch.dispatch(NotifyMessage(subject="s", body="b"), source="pipeline")}
    assert results == {"email", "telegram"}


def test_dispatch_with_matching_rule_restricts_backends(monkeypatch):
    from javdb.integrations.notify.routing import NotifyRoutingRule
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["email", "telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry("email", "telegram"))
    monkeypatch.setattr(dispatch, "_load_routing_rules",
                        lambda: [NotifyRoutingRule.create(rule_id="r1", match_level="error", match_source="pipeline", backends=["telegram"])])
    results = {r.plugin for r in dispatch.dispatch(NotifyMessage(subject="s", body="b", level="error"), source="pipeline")}
    assert results == {"telegram"}


def test_plain_send_is_unchanged(monkeypatch):
    # The Phase 1 send() entry point keeps its exact behaviour (no source arg).
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["email"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry("email"))
    results = dispatch.send(NotifyMessage(subject="s", body="b"))
    assert [r.plugin for r in results] == ["email"]
```

Modify `javdb/integrations/notify/dispatch.py`:

- Keep the existing `send(message)` and `active_names()` exactly as-is (Phase 1 callers unchanged).
- Add a private `_load_routing_rules()` that reads enabled rows via `NotifyRoutingRepo` over `get_db(OPERATIONS_DB_PATH)`, returning `[]` on any error (notify must never crash the run).
- Add `dispatch(message, source=None)`:

```python
def dispatch(message: NotifyMessage, source: str | None = None) -> list[NotifyResult]:
    """Route a NotifyMessage to backends. Falls back to Phase 1 fan-out
    (active_names) when no enabled routing rule matches."""
    selected = select_backends(_load_routing_rules(), level=message.level, source=source)
    names = selected if selected is not None else active_names()
    results: list[NotifyResult] = []
    for name in names:
        plugin = REGISTRY.get("notify", name)
        if plugin is None:
            results.append(NotifyResult(plugin=name, ok=False, detail="not registered"))
            continue
        try:
            if not plugin.is_configured():
                results.append(NotifyResult(plugin=name, ok=False, detail="not configured"))
                continue
            results.append(plugin.send(message))
        except Exception as exc:
            results.append(NotifyResult(plugin=name, ok=False, detail=f"error: {exc}"))
    return results
```

Factor the shared per-name fan-out loop so `send()` and `dispatch()` do not duplicate the failure-isolation logic.

- [ ] **Step 5: Run the routing tests**

Run:

```bash
pytest tests/unit/test_notify_routing.py tests/unit/test_notify_routing_repo.py tests/unit/test_notify_dispatch_routing.py -v
```

Expected: all pass, including the backward-compat fan-out test.

---

## Task 4: Digest Queue And flush_digest

**Files:**
- Create: `javdb/integrations/notify/digest.py`
- Create: `javdb/storage/repos/notify_digest_repo.py`
- Create: `tests/unit/test_notify_digest.py`

- [ ] **Step 1: Write digest tests**

Create `tests/unit/test_notify_digest.py`:

```python
import sqlite3

from javdb.integrations.notify.digest import enqueue_digest, flush_digest
from javdb.storage.repos.notify_digest_repo import NotifyDigestRepo

DDL = """
CREATE TABLE NotifyDigestQueue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT,
  subject TEXT,
  body TEXT,
  source TEXT,
  created_at TEXT NOT NULL,
  flushed_at TEXT
)
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(DDL)
    return conn


def test_flush_composes_single_message_and_marks_rows():
    conn = _conn()
    repo = NotifyDigestRepo(conn)
    enqueue_digest(repo, level="error", subject="A failed", body="ctx-a", source="pipeline")
    enqueue_digest(repo, level="warning", subject="B slow", body="ctx-b", source="proxy")

    sent = []
    composed = flush_digest(repo, window_minutes=60, dispatcher=lambda msg, source=None: sent.append(msg) or [])

    assert composed is not None          # one summary NotifyMessage
    assert len(sent) == 1
    assert "A failed" in sent[0].body and "B slow" in sent[0].body
    assert repo.count_unflushed() == 0   # rows marked flushed


def test_flush_noop_when_queue_empty():
    repo = NotifyDigestRepo(_conn())
    sent = []
    composed = flush_digest(repo, window_minutes=60, dispatcher=lambda msg, source=None: sent.append(msg) or [])
    assert composed is None
    assert sent == []
```

- [ ] **Step 2: Implement the digest queue + flush**

Create `javdb/storage/repos/notify_digest_repo.py` with: `enqueue(level, subject, body, source)`, `list_unflushed(window_minutes)`, `mark_flushed(ids, flushed_at)`, `count_unflushed()`.

Create `javdb/integrations/notify/digest.py`:

```python
"""Notify digest aggregation (ADR-039 Phase 4).

Accumulates messages tagged for digest, then composes ONE summary NotifyMessage
and dispatches it. Driven by a scheduled job (see ops/notifications.md).
"""

from __future__ import annotations

from javdb.integrations.notify.plugin import NotifyMessage


def enqueue_digest(repo, *, level, subject, body, source=None) -> None:
    repo.enqueue(level=level, subject=subject, body=body, source=source)


def flush_digest(repo, *, window_minutes: int, dispatcher) -> NotifyMessage | None:
    rows = repo.list_unflushed(window_minutes)
    if not rows:
        return None
    lines = [f"[{r.level or 'info'}] {r.subject}\n{r.body}" for r in rows]
    summary = NotifyMessage(
        subject=f"Notify digest ({len(rows)} item(s))",
        body="\n\n".join(lines),
        level="info",
    )
    dispatcher(summary, source="digest")
    repo.mark_flushed([r.id for r in rows], flushed_at=_now())
    return summary
```

`dispatcher` defaults to `notify.dispatch.dispatch` in production wiring (injected in tests). The digest does not introduce a new delivery mechanism — it composes one `NotifyMessage` and routes it like any other.

- [ ] **Step 3: Document the scheduled flush command (no new workflow)**

Record (do not add a workflow) the CLI/cron entry that a self-hoster wires up, driven by `NOTIFY_DIGEST_WINDOW_MINUTES`:

```bash
# Cron-driven digest flush (every NOTIFY_DIGEST_WINDOW_MINUTES); wired by the operator.
python3 -m apps.cli.notify.flush_digest
```

Note in Task 9 docs that the flush CLI is the intended driver and that this IMP does not add a GitHub Actions workflow for it.

- [ ] **Step 4: Run digest tests**

Run: `pytest tests/unit/test_notify_digest.py -v`
Expected: pass.

---

## Task 5: Registry And Config Wiring

**Files:**
- Modify: `javdb/integrations/notify/dispatch.py` (import to self-register webhook)
- Modify: `config.py.example`

- [ ] **Step 1: Register the webhook built-in at import**

In `javdb/integrations/notify/dispatch.py`, add the webhook import alongside the existing email/telegram self-registration imports so the registry includes `webhook`:

```python
import javdb.integrations.notify.webhook.plugin  # noqa: F401,E402
```

Do not change the `NotifyPlugin` contract; `webhook` joins `email`/`telegram` as a built-in.

- [ ] **Step 2: Add the new config keys**

Add to `config.py.example`, near the existing `NOTIFY_BACKENDS` block (keep `NOTIFY_BACKENDS` as-is):

```python
# ADR-039 Phase 4 alert delivery. Webhook backend URL (used when 'webhook' is in
# NOTIFY_BACKENDS or selected by a routing rule).
NOTIFY_WEBHOOK_URL = ''               # POST {subject, body, level} as JSON
# Optional digest aggregation: queue messages tagged for digest and flush one
# summary per window via `python3 -m apps.cli.notify.flush_digest`.
NOTIFY_DIGEST_ENABLED = False
NOTIFY_DIGEST_WINDOW_MINUTES = 60
```

- [ ] **Step 3: Backward-compat check**

Run:

```bash
python3 -c "import javdb.integrations.notify.dispatch as d; print(sorted(p.name for p in d.REGISTRY.list('notify'))); print(d.active_names())"
```

Expected: registered names include `email`, `telegram`, `webhook`; `active_names()` is `['email']` (default unchanged).

---

## Task 6: Python API Notify-Config Surface

**Files:**
- Create: `apps/api/schemas/notify.py`
- Create: `apps/api/routers/notify.py`
- Modify: `apps/api/server.py`
- Create: `tests/unit/test_notify_config_api.py`

- [ ] **Step 1: Write API tests**

Create `tests/unit/test_notify_config_api.py`:

```python
def test_get_routing_rules_returns_items(admin_client):
    response = admin_client.get("/api/notify/routing-rules")
    assert response.status_code == 200
    assert "items" in response.json()


def test_put_routing_rules_requires_admin(user_client):
    response = user_client.put("/api/notify/routing-rules", json={"rules": []})
    assert response.status_code in (401, 403)


def test_put_routing_rules_replaces_set(monkeypatch, admin_client):
    from apps.api.routers import notify as notify_router
    captured = {}
    monkeypatch.setattr(notify_router, "_replace_routing_rules", lambda rules: captured.update(rules=rules))
    response = admin_client.put("/api/notify/routing-rules", json={
        "rules": [{"rule_id": "r1", "match_level": "error", "match_source": "pipeline", "backends": ["telegram"], "enabled": True}],
    })
    assert response.status_code == 200
    assert captured["rules"][0].rule_id == "r1"


def test_digest_settings_round_trip(admin_client):
    put = admin_client.put("/api/notify/digest-settings", json={"enabled": True, "window_minutes": 30})
    assert put.status_code == 200
    get = admin_client.get("/api/notify/digest-settings")
    assert get.json()["window_minutes"] == 30


def test_test_send_routes_message(monkeypatch, admin_client):
    from apps.api.routers import notify as notify_router
    sent = {}
    monkeypatch.setattr(notify_router, "_dispatch", lambda message, source=None: sent.update(subject=message.subject, source=source) or [])
    response = admin_client.post("/api/notify/test-send", json={"subject": "ping", "body": "hi", "level": "info", "source": "manual"})
    assert response.status_code == 200
    assert sent["subject"] == "ping"
    assert sent["source"] == "manual"
```

- [ ] **Step 2: Add schemas**

Create `apps/api/schemas/notify.py`:

```python
from typing import Literal, Optional
from pydantic import BaseModel


class RoutingRuleSchema(BaseModel):
    rule_id: str
    match_level: Optional[str] = None
    match_source: Optional[str] = None
    backends: list[str]
    enabled: bool = True


class RoutingRulesResponse(BaseModel):
    items: list[RoutingRuleSchema]


class RoutingRulesRequest(BaseModel):
    rules: list[RoutingRuleSchema]


class DigestSettingsSchema(BaseModel):
    enabled: bool
    window_minutes: int


class TestSendRequest(BaseModel):
    subject: str
    body: str
    level: Literal["info", "warning", "error"] = "info"
    source: Optional[str] = None


class TestSendResult(BaseModel):
    results: list[dict]
```

- [ ] **Step 3: Add the router**

Create `apps/api/routers/notify.py` with `router = APIRouter(prefix="/api/notify", tags=["notify"])` and:

- `GET /routing-rules` (`_require_auth`) → `RoutingRulesResponse` from `NotifyRoutingRepo.list_all`.
- `PUT /routing-rules` (`require_role("admin")`) → `_replace_routing_rules(rules)` (maps schema → `NotifyRoutingRule.create`, calls `NotifyRoutingRepo.replace_all`).
- `GET /digest-settings` (`_require_auth`) → reads `NOTIFY_DIGEST_ENABLED` / `NOTIFY_DIGEST_WINDOW_MINUTES` config.
- `PUT /digest-settings` (`require_role("admin")`) → persists via the existing config-update path.
- `POST /test-send` (`require_role("admin")`) → builds a `NotifyMessage` and calls `_dispatch(message, source=body.source)` (module-level alias of `notify.dispatch.dispatch`), returning per-backend results.

The `_dispatch`, `_replace_routing_rules`, and config helpers are module-level so tests can monkeypatch them. Webhook URL is read/written as a config key through the existing `/api/config` path; it is documented here, not re-implemented.

- [ ] **Step 4: Register the router**

Modify `apps/api/server.py` to `include_router(notify.router)` alongside the existing routers.

- [ ] **Step 5: Run API tests**

Run: `pytest tests/unit/test_notify_config_api.py -v`
Expected: pass.

---

## Task 7: Cloudflare Worker API Parity

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/server/routes/notify.ts`, `../JAVDB_AutoSpider_Web/server/index.ts`
- Create: `../JAVDB_AutoSpider_Web/server/__tests__/notify-routes.test.ts`

- [ ] **Step 1: Add Worker tests**

Create `server/__tests__/notify-routes.test.ts` (mirror `diagnostics-routes.test.ts` setup). Seed `NotifyRoutingRule` in the operations DB and assert:

```ts
it("GET /api/notify/routing-rules returns rules", async () => {
  await seedRoutingRule(env.OPERATIONS_DB);
  const token = await getToken();
  const res = await app.request("/api/notify/routing-rules", { headers: { Authorization: `Bearer ${token}` } }, env);
  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.items[0].rule_id).toBe("r1");
});

it("PUT /api/notify/routing-rules requires admin", async () => {
  const token = await getNonAdminToken();
  const res = await app.request("/api/notify/routing-rules", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ rules: [] }),
  }, env);
  expect(res.status).toBe(403);
});
```

- [ ] **Step 2: Add Worker routes (same SQL shapes, admin guard on writes)**

Modify `server/routes/notify.ts`:

```ts
notifyRoutes.get("/routing-rules", async (c) => {
  const rows = await c.env.OPERATIONS_DB
    .prepare("SELECT * FROM NotifyRoutingRule ORDER BY created_at ASC")
    .all();
  return c.json({ items: rows.results.map(mapRoutingRule) });
});

notifyRoutes.put("/routing-rules", requireRole("admin"), async (c) => {
  const { rules } = await c.req.json<{ rules: RoutingRuleInput[] }>();
  const now = new Date().toISOString();
  const stmts = [c.env.OPERATIONS_DB.prepare("DELETE FROM NotifyRoutingRule")];
  for (const r of rules) {
    stmts.push(
      c.env.OPERATIONS_DB
        .prepare(`INSERT INTO NotifyRoutingRule
          (rule_id, match_level, match_source, backends_json, enabled, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?)`)
        .bind(r.rule_id, r.match_level ?? null, r.match_source ?? null,
              JSON.stringify(r.backends ?? []), r.enabled ? 1 : 0, now, now),
    );
  }
  await c.env.OPERATIONS_DB.batch(stmts);   // atomic replace
  return c.json({ ok: true });
});
```

Add `GET/PUT /digest-settings` (config-backed) and `POST /test-send` parity. The Worker `test-send` dispatches via GH Actions parity / D1 read of active backends consistent with how the Worker already mirrors notify (no Python subprocess). Do not detect incidents and do not change the `NotifyMessage` shape.

- [ ] **Step 3: Mount the routes**

Modify `server/index.ts` to mount `notifyRoutes` under `/api/notify`.

- [ ] **Step 4: Run Worker tests**

Run from the Web repo: `npm run test:server -- server/__tests__/notify-routes.test.ts`
Expected: pass.

---

## Task 8: Web Notify/Alerting Config Panel

**Files:**
- Create: `../JAVDB_AutoSpider_Web/src/api/notify.ts`
- Create: `../JAVDB_AutoSpider_Web/src/pages/operations/NotifyRoutingPanel.vue`
- Modify: `../JAVDB_AutoSpider_Web/src/pages/operations/EmailPage.vue`
- Create: `../JAVDB_AutoSpider_Web/tests/unit/notify-config-api.spec.ts`

- [ ] **Step 1: Add frontend API tests**

Create `tests/unit/notify-config-api.spec.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { getRoutingRules, putRoutingRules, getDigestSettings, putDigestSettings, testSend } from '@/api/notify'

describe('notify config API', () => {
  it('lists routing rules', async () => {
    const spy = vi.spyOn(http, 'get').mockResolvedValueOnce({ data: { items: [] } })
    await getRoutingRules()
    expect(spy).toHaveBeenCalledWith('/api/notify/routing-rules')
  })

  it('replaces routing rules', async () => {
    const spy = vi.spyOn(http, 'put').mockResolvedValueOnce({ data: { ok: true } })
    await putRoutingRules({ rules: [] })
    expect(spy).toHaveBeenCalledWith('/api/notify/routing-rules', { rules: [] })
  })

  it('runs a test send through routing', async () => {
    const spy = vi.spyOn(http, 'post').mockResolvedValueOnce({ data: { results: [] } })
    await testSend({ subject: 'ping', body: 'hi', level: 'info', source: 'manual' })
    expect(spy).toHaveBeenCalledWith('/api/notify/test-send', { subject: 'ping', body: 'hi', level: 'info', source: 'manual' })
  })
})
```

- [ ] **Step 2: Add the frontend API client**

Create `src/api/notify.ts` with the `RoutingRule`, `DigestSettings`, and `TestSendRequest` types plus `getRoutingRules`, `putRoutingRules`, `getDigestSettings`, `putDigestSettings`, and `testSend` functions against `/api/notify/*`.

- [ ] **Step 3: Build the panel**

Create `src/pages/operations/NotifyRoutingPanel.vue`:

- A table of routing rules (level, source, backends multi-select, enabled toggle), add/remove rows, and a single Save that calls `putRoutingRules`.
- A digest section: enable toggle + window-minutes input bound to `getDigestSettings`/`putDigestSettings`.
- A webhook URL input (read/write via the existing config API).
- A "Send test" button that posts a `NotifyMessage` through `testSend` and renders per-backend results.

Mount it in `EmailPage.vue` under the existing notify/email area (do not create a new route).

- [ ] **Step 4: Run Web unit tests**

Run from the Web repo: `npm run test:unit -- tests/unit/notify-config-api.spec.ts`
Expected: pass.

---

## Task 9: Documentation

**Files:**
- Modify: `docs/handbook/en/self-hoster/configuration.md`, `docs/handbook/zh/self-hoster/configuration.md`
- Create: `docs/handbook/en/ops/notifications.md`, `docs/handbook/zh/ops/notifications.md`

- [ ] **Step 1: Document the new config keys (en + zh)**

Add `NOTIFY_WEBHOOK_URL`, `NOTIFY_DIGEST_ENABLED`, `NOTIFY_DIGEST_WINDOW_MINUTES` to `docs/handbook/en/self-hoster/configuration.md` (near the existing `NOTIFY_BACKENDS` / Telegram rows) and mirror verbatim key names into the paired zh file. Note that `NOTIFY_BACKENDS` is unchanged.

- [ ] **Step 2: Write the ops notifications guide (en + zh)**

Create `docs/handbook/en/ops/notifications.md` covering:

- **Routing rules** — `(level, source) → backends`; `NULL`/empty match is a wildcard; **with zero rules the system fans out to all active backends (unchanged default)**.
- **Webhook backend** — set `NOTIFY_WEBHOOK_URL`; payload is `{subject, body, level}` JSON.
- **Digest** — enable `NOTIFY_DIGEST_ENABLED`, set `NOTIFY_DIGEST_WINDOW_MINUTES`, and drive the flush with `python3 -m apps.cli.notify.flush_digest` from cron. State explicitly that **this phase adds no GitHub Actions workflow** for the flush.
- **Test-send** — the Web panel / `POST /api/notify/test-send` runs a message through routing without raising a real incident.
- A note that routing/digest sit **above** `notify.send` and that incident detection is owned by ADR-026.

Mirror the section structure into `docs/handbook/zh/ops/notifications.md` (translate prose; keep keys/paths/commands verbatim).

- [ ] **Step 3: Documentation whitespace check**

Run:

```bash
git diff --check -- \
  docs/handbook/en/self-hoster/configuration.md \
  docs/handbook/zh/self-hoster/configuration.md \
  docs/handbook/en/ops/notifications.md \
  docs/handbook/zh/ops/notifications.md
```

Expected: no output.

---

## Task 10: Verification And Closeout

- [ ] **Step 1: Run Python tests**

Run:

```bash
pytest \
  tests/unit/test_webhook_notify_plugin.py \
  tests/unit/test_notify_routing.py \
  tests/unit/test_notify_routing_repo.py \
  tests/unit/test_notify_digest.py \
  tests/unit/test_notify_dispatch_routing.py \
  tests/unit/test_notify_config_api.py \
  tests/unit/test_notify_dispatch.py \
  -v
```

Expected: all pass (including the pre-existing `test_notify_dispatch.py`, proving Phase 1 fan-out is unregressed).

- [ ] **Step 2: Run Web tests**

Run from `../JAVDB_AutoSpider_Web`:

```bash
npm run test:server -- server/__tests__/notify-routes.test.ts
npm run test:unit -- tests/unit/notify-config-api.spec.ts
```

Expected: all pass.

- [ ] **Step 3: Run static checks**

Run from the main repo:

```bash
python3 -m compileall \
  javdb/integrations/notify \
  javdb/storage/repos/notify_routing_repo.py \
  javdb/storage/repos/notify_digest_repo.py \
  apps/api/routers/notify.py \
  apps/api/schemas/notify.py \
  javdb/storage/db/_db_migrations.py
git diff --check
```

Run from the Web repo:

```bash
npm run typecheck
npm run lint
```

Expected: no failures.

- [ ] **Step 4: Backward-compat smoke**

Run:

```bash
python3 -c "import javdb.integrations.notify.dispatch as d; from javdb.integrations.notify.plugin import NotifyMessage; print(sorted(p.name for p in d.REGISTRY.list('notify')))"
```

Expected: `['email', 'telegram', 'webhook']` registered; with no routing rules, `dispatch(...)` still selects `active_names()` (`['email']` by default). The `NotifyPlugin` contract is unchanged.

- [ ] **Step 5: Commit**

Commit only the Phase 4 source, tests, and docs. Do not commit `reports/` data files. This block is illustrative — do not run it as part of writing the plan.

```bash
git add \
  javdb/integrations/notify/webhook/ \
  javdb/migrations/d1/2026_06_13_add_notify_routing_digest.sql \
  javdb/storage/db/_db_migrations.py \
  javdb/integrations/notify/routing.py \
  javdb/integrations/notify/digest.py \
  javdb/integrations/notify/dispatch.py \
  javdb/integrations/notify/__init__.py \
  javdb/storage/repos/notify_routing_repo.py \
  javdb/storage/repos/notify_digest_repo.py \
  config.py.example \
  apps/api/schemas/notify.py \
  apps/api/routers/notify.py \
  apps/api/server.py \
  tests/unit/test_webhook_notify_plugin.py \
  tests/unit/test_notify_routing.py \
  tests/unit/test_notify_routing_repo.py \
  tests/unit/test_notify_digest.py \
  tests/unit/test_notify_dispatch_routing.py \
  tests/unit/test_notify_config_api.py \
  docs/handbook/en/self-hoster/configuration.md \
  docs/handbook/zh/self-hoster/configuration.md \
  docs/handbook/en/ops/notifications.md \
  docs/handbook/zh/ops/notifications.md
git commit -m "feat(notify): add alert routing, digest, and webhook delivery (ADR-039 Phase 4)"
```

Commit the Web repo changes separately:

```bash
cd ../JAVDB_AutoSpider_Web
git add \
  server/routes/notify.ts \
  server/index.ts \
  server/__tests__/notify-routes.test.ts \
  src/api/notify.ts \
  src/pages/operations/NotifyRoutingPanel.vue \
  src/pages/operations/EmailPage.vue \
  tests/unit/notify-config-api.spec.ts
git commit -m "feat(notify): add alerting routing/digest config panel (ADR-039 Phase 4)"
```
