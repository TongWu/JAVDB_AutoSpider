# IMP-ADR039-01: Notify Plugin Registry + Email/Telegram (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-039](ADR-039-pluggable-integration-platform.md) (umbrella) — this is **Phase 1** of three.

**Goal:** A `(category, name)` plugin registry + a `NotifyPlugin` contract, proven on the `notify` category: the existing email wrapped as a built-in plugin, a new Telegram plugin, `NOTIFY_BACKENDS` config selection (default `['email']`, backward-compatible), and a `notify.send()` fan-out with failure isolation.

**Architecture:** `javdb/integrations/plugins/registry.py` holds a `PluginRegistry` (with a reserved `discover_entry_points` seam for Phase 2). `EmailNotifyPlugin` wraps the existing `email/delivery.py` `send_email(subject, body, ...)` primitive (NOT the rich `run_email_notification` report path, which is untouched). `TelegramNotifyPlugin` calls the Telegram Bot API. `notify/dispatch.py` resolves active plugins from `NOTIFY_BACKENDS` and fans out.

**Tech Stack:** Python 3, `requests` (Telegram), `pytest`. Reuses `email/delivery.py::send_email` and `javdb.infra.config.cfg`.

**Seams (confirmed):** `send_email(subject, body, attachments=None, dry_run=False)` (`notify/email/delivery.py`); `cfg(name, default)` (`javdb/infra/config.py`); `notify/__init__.py` is empty (clean).

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/integrations/plugins/__init__.py` | Create | Package marker + `REGISTRY` export |
| `javdb/integrations/plugins/registry.py` | Create | `PluginRegistry` (+ entry-point seam) |
| `javdb/integrations/notify/plugin.py` | Create | `NotifyPlugin` Protocol + `NotifyMessage` + `NotifyResult` |
| `javdb/integrations/notify/email/plugin.py` | Create | `EmailNotifyPlugin` (wraps `send_email`) |
| `javdb/integrations/notify/telegram/__init__.py` | Create | Package marker |
| `javdb/integrations/notify/telegram/plugin.py` | Create | `TelegramNotifyPlugin` (new) |
| `javdb/integrations/notify/dispatch.py` | Create | `send()` fan-out + active resolution |
| `config.py.example` | Modify | `NOTIFY_BACKENDS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `CONTEXT.md`, `docs/handbook/en/...` | Modify | Domain terms + notify-plugin doc |
| `tests/unit/test_plugin_registry.py` … | Create | Registry + plugin + dispatch tests |

**Naming contract (verbatim):** `PluginRegistry` with `register(category, plugin)`,
`get(category, name)`, `list(category)`, `discover_entry_points(group) -> int`;
module-global `REGISTRY`; `NotifyMessage(subject, body, level='info')`;
`NotifyResult(plugin, ok, detail=None)`; `NotifyPlugin` Protocol (`name`,
`is_configured()`, `send(message) -> NotifyResult`); `EmailNotifyPlugin`,
`TelegramNotifyPlugin`; dispatch `send(message) -> list[NotifyResult]`,
`active_names() -> list[str]`.

> **Phase-2-gated:** entry-point discovery (the seam is a no-op stub here); the
> `downloader` category; plugins as ADR-036 consumers / ADR-038 tools.

---

## Task 1: `PluginRegistry`

**Files:**
- Create: `javdb/integrations/plugins/__init__.py`, `javdb/integrations/plugins/registry.py`
- Test: `tests/unit/test_plugin_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_plugin_registry.py
from javdb.integrations.plugins.registry import PluginRegistry


class _Stub:
    name = "stub"


def test_register_get_list():
    reg = PluginRegistry()
    reg.register("notify", _Stub())
    assert reg.get("notify", "stub").name == "stub"
    assert [p.name for p in reg.list("notify")] == ["stub"]


def test_get_unknown_returns_none():
    assert PluginRegistry().get("notify", "nope") is None


def test_discover_entry_points_is_noop_in_phase1():
    assert PluginRegistry().discover_entry_points("javdb.notify_plugins") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_plugin_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the registry**

```python
# javdb/integrations/plugins/__init__.py
"""Integration plugin platform (ADR-039 Phase 1)."""

from javdb.integrations.plugins.registry import PluginRegistry, REGISTRY  # noqa: F401
```

```python
# javdb/integrations/plugins/registry.py
"""Plugin registry keyed by (category, name) — built-in now, entry-point-ready."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[tuple[str, str], object] = {}

    def register(self, category: str, plugin) -> None:
        key = (category, plugin.name)
        if key in self._plugins:
            logger.debug("plugin already registered: %s", key)
        self._plugins[key] = plugin

    def get(self, category: str, name: str):
        return self._plugins.get((category, name))

    def list(self, category: str) -> list:
        return [p for (cat, _name), p in self._plugins.items() if cat == category]

    def discover_entry_points(self, group: str) -> int:
        """Phase-2 seam: discover third-party plugins via importlib.metadata.
        Phase 1 is a deliberate no-op (reserved interface)."""
        return 0


# Process-global registry; built-in plugins register into this at import.
REGISTRY = PluginRegistry()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_plugin_registry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/plugins/ tests/unit/test_plugin_registry.py
git commit -m "feat(integrations): add PluginRegistry (entry-point-ready) (ADR-039)"
```

---

## Task 2: `NotifyPlugin` contract

**Files:**
- Create: `javdb/integrations/notify/plugin.py`
- Test: `tests/unit/test_notify_plugin_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_notify_plugin_contract.py
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult


def test_message_defaults():
    m = NotifyMessage(subject="s", body="b")
    assert m.level == "info"


def test_result_ok_flag():
    r = NotifyResult(plugin="email", ok=True)
    assert r.ok is True
    assert r.detail is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_notify_plugin_contract.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the contract**

```python
# javdb/integrations/notify/plugin.py
"""The notify-category plugin contract (ADR-039 D2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class NotifyMessage:
    subject: str
    body: str
    level: str = "info"   # info | warning | error


@dataclass
class NotifyResult:
    plugin: str
    ok: bool
    detail: Optional[str] = None


class NotifyPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def send(self, message: NotifyMessage) -> NotifyResult: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_notify_plugin_contract.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/notify/plugin.py tests/unit/test_notify_plugin_contract.py
git commit -m "feat(notify): add NotifyPlugin contract (ADR-039)"
```

---

## Task 3: `EmailNotifyPlugin` (wraps existing `send_email`)

**Files:**
- Create: `javdb/integrations/notify/email/plugin.py`
- Test: `tests/unit/test_email_notify_plugin.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_email_notify_plugin.py
import javdb.integrations.notify.email.plugin as email_plugin
from javdb.integrations.notify.plugin import NotifyMessage


def test_send_delegates_to_send_email(monkeypatch):
    calls = {}
    monkeypatch.setattr(email_plugin, "send_email",
                        lambda subject, body, **kw: calls.update(subject=subject, body=body))
    plugin = email_plugin.EmailNotifyPlugin()
    result = plugin.send(NotifyMessage(subject="Run failed", body="details"))
    assert calls == {"subject": "Run failed", "body": "details"}
    assert result.plugin == "email"
    assert result.ok is True


def test_send_reports_failure_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(email_plugin, "send_email", _boom)
    result = email_plugin.EmailNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False
    assert "smtp down" in (result.detail or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_email_notify_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the plugin**

```python
# javdb/integrations/notify/email/plugin.py
"""Email notify plugin — wraps the existing send_email primitive (ADR-039 D3).

The rich run_email_notification report path is NOT touched; this is the generic
notification adapter."""

from __future__ import annotations

from javdb.integrations.notify.email.delivery import send_email
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY


class EmailNotifyPlugin:
    name = "email"

    def is_configured(self) -> bool:
        from javdb.integrations.notify.email import _config
        user = getattr(_config, "SMTP_USER", "")
        return bool(user) and "your_email" not in user

    def send(self, message: NotifyMessage) -> NotifyResult:
        try:
            send_email(message.subject, message.body)
            return NotifyResult(plugin=self.name, ok=True)
        except Exception as exc:
            return NotifyResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("notify", EmailNotifyPlugin())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_email_notify_plugin.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/notify/email/plugin.py tests/unit/test_email_notify_plugin.py
git commit -m "feat(notify): add EmailNotifyPlugin wrapping send_email (ADR-039)"
```

---

## Task 4: `TelegramNotifyPlugin` (new)

**Files:**
- Create: `javdb/integrations/notify/telegram/__init__.py`, `javdb/integrations/notify/telegram/plugin.py`
- Test: `tests/unit/test_telegram_notify_plugin.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_telegram_notify_plugin.py
import javdb.integrations.notify.telegram.plugin as tg
from javdb.integrations.notify.plugin import NotifyMessage


def test_is_configured_reads_config(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "t",
                                                          "TELEGRAM_CHAT_ID": "c"}.get(name, default))
    assert tg.TelegramNotifyPlugin().is_configured() is True


def test_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: default)
    assert tg.TelegramNotifyPlugin().is_configured() is False


def test_send_posts_to_bot_api(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "TKN",
                                                          "TELEGRAM_CHAT_ID": "CHAT"}.get(name, default))
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(tg.requests, "post", _post)
    result = tg.TelegramNotifyPlugin().send(NotifyMessage(subject="Sub", body="Body"))
    assert "botTKN/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == "CHAT"
    assert "Sub" in captured["json"]["text"] and "Body" in captured["json"]["text"]
    assert result.ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_telegram_notify_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the plugin**

```python
# javdb/integrations/notify/telegram/__init__.py
"""Telegram notify backend (ADR-039)."""
```

```python
# javdb/integrations/notify/telegram/plugin.py
"""Telegram notify plugin — Bot API sendMessage (ADR-039 D3)."""

from __future__ import annotations

import requests

from javdb.infra.config import cfg
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY


class TelegramNotifyPlugin:
    name = "telegram"

    def is_configured(self) -> bool:
        return bool(cfg("TELEGRAM_BOT_TOKEN", "")) and bool(cfg("TELEGRAM_CHAT_ID", ""))

    def send(self, message: NotifyMessage) -> NotifyResult:
        token = cfg("TELEGRAM_BOT_TOKEN", "")
        chat_id = cfg("TELEGRAM_CHAT_ID", "")
        text = f"*{message.subject}*\n{message.body}"
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
            ok = resp.status_code == 200
            return NotifyResult(plugin=self.name, ok=ok,
                                detail=None if ok else f"status {resp.status_code}")
        except Exception as exc:
            return NotifyResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("notify", TelegramNotifyPlugin())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_telegram_notify_plugin.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/notify/telegram/ tests/unit/test_telegram_notify_plugin.py
git commit -m "feat(notify): add TelegramNotifyPlugin (ADR-039)"
```

---

## Task 5: `dispatch.send` fan-out + active resolution

**Files:**
- Create: `javdb/integrations/notify/dispatch.py`
- Test: `tests/unit/test_notify_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_notify_dispatch.py
import javdb.integrations.notify.dispatch as dispatch
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import PluginRegistry


class _Plugin:
    def __init__(self, name, configured=True, raises=False):
        self.name = name
        self._configured = configured
        self._raises = raises
    def is_configured(self):
        return self._configured
    def send(self, message):
        if self._raises:
            raise RuntimeError("boom")
        return NotifyResult(plugin=self.name, ok=True)


def _registry(*plugins):
    reg = PluginRegistry()
    for p in plugins:
        reg.register("notify", p)
    return reg


def test_active_names_default_email(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: default)
    assert dispatch.active_names() == ["email"]


def test_active_names_csv_string(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: "email, telegram")
    assert dispatch.active_names() == ["email", "telegram"]


def test_send_fans_out_and_isolates_failure(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["email", "telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY",
                        _registry(_Plugin("email", raises=True), _Plugin("telegram")))
    results = {r.plugin: r for r in dispatch.send(NotifyMessage(subject="s", body="b"))}
    assert results["email"].ok is False        # isolated failure
    assert results["telegram"].ok is True       # still delivered


def test_send_skips_unconfigured(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_Plugin("telegram", configured=False)))
    results = dispatch.send(NotifyMessage(subject="s", body="b"))
    assert results[0].ok is False
    assert "not configured" in (results[0].detail or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_notify_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the dispatcher**

```python
# javdb/integrations/notify/dispatch.py
"""Fan-out notify dispatch with failure isolation (ADR-039 D4).

Importing this module registers the built-in plugins (email, telegram)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.notify.email.plugin  # noqa: F401,E402
import javdb.integrations.notify.telegram.plugin  # noqa: F401,E402


def active_names() -> list[str]:
    val = cfg("NOTIFY_BACKENDS", ["email"])
    if isinstance(val, str):
        names = [v.strip() for v in val.split(",") if v.strip()]
    else:
        names = list(val) if val else []
    return names or ["email"]


def send(message: NotifyMessage) -> list[NotifyResult]:
    results: list[NotifyResult] = []
    for name in active_names():
        plugin = REGISTRY.get("notify", name)
        if plugin is None:
            results.append(NotifyResult(plugin=name, ok=False, detail="not registered"))
            continue
        if not plugin.is_configured():
            results.append(NotifyResult(plugin=name, ok=False, detail="not configured"))
            continue
        try:
            results.append(plugin.send(message))
        except Exception as exc:  # failure isolation
            results.append(NotifyResult(plugin=name, ok=False, detail=f"error: {exc}"))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_notify_dispatch.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/notify/dispatch.py tests/unit/test_notify_dispatch.py
git commit -m "feat(notify): add fan-out dispatch with failure isolation (ADR-039)"
```

---

## Task 6: Config, docs, full gate

**Files:**
- Modify: `config.py.example`, `CONTEXT.md`, `docs/handbook/en/self-hoster/configuration.md` (+ zh)

- [ ] **Step 1: Document config** — add to `config.py.example`

```python
# ADR-039 pluggable notify backends. Active list; defaults to email only.
NOTIFY_BACKENDS = ['email']          # e.g. ['email', 'telegram']
TELEGRAM_BOT_TOKEN = ''              # from @BotFather
TELEGRAM_CHAT_ID = ''               # target chat/channel id
```

- [ ] **Step 2: Update CONTEXT.md** — add ADR-039 terms verbatim: *Plugin*, *Plugin registry*, *Plugin contract*, *Built-in plugin*, *Notify backend*.

- [ ] **Step 3: Document notify backends** — add a section to
  `docs/handbook/en/self-hoster/configuration.md` (how to enable Telegram:
  `NOTIFY_BACKENDS`, bot token, chat id); mirror to the paired zh doc.

- [ ] **Step 4: Full gate**

Run:
```bash
pytest tests/unit/test_plugin_registry.py tests/unit/test_notify_plugin_contract.py \
       tests/unit/test_email_notify_plugin.py tests/unit/test_telegram_notify_plugin.py \
       tests/unit/test_notify_dispatch.py -v
```
Expected: all PASS.

- [ ] **Step 5: Backward-compat check** — importing dispatch registers built-ins and
  the default routes to email only:

Run:
```bash
python3 -c "import javdb.integrations.notify.dispatch as d; print(sorted(p.name for p in d.REGISTRY.list('notify'))); print(d.active_names())"
```
Expected: `['email', 'telegram']` registered; `active_names()` is `['email']` (default).

- [ ] **Step 6: Commit**

```bash
git add config.py.example CONTEXT.md docs/handbook
git commit -m "docs(notify): document NOTIFY_BACKENDS + Telegram (ADR-039 Phase 1)"
```

---

## Plan Self-Review

**Spec coverage (ADR-039 Phase 1 row + D-decisions):**
- In-repo registry, entry-point-ready (D1) → Task 1 (`discover_entry_points` no-op stub). ✓
- `NotifyPlugin` contract (D2) → Task 2. ✓
- Email wrapped, not rewritten; Telegram new; `NOTIFY_BACKENDS` default email (D3) → Tasks 3, 4, 6. ✓
- Fan-out + failure isolation (D4) → Task 5. ✓
- Module shape (D5) → Tasks 1-5. ✓
- ADR-036/038 composition deferred (D6) → not built; documented. ✓
- Phasing — only notify, no entry points (D7) → scope held; stub reserved. ✓
- Docs (CONTEXT.md, configuration.md) → Task 6. ✓

**Type consistency:** `PluginRegistry` (`register`/`get`/`list`/`discover_entry_points`),
`NotifyMessage`, `NotifyResult`, `NotifyPlugin`, `EmailNotifyPlugin`,
`TelegramNotifyPlugin`, dispatch `send`/`active_names` are used identically across Tasks 1-6.

**Backward-compat guarantee:** `NOTIFY_BACKENDS` defaults to `['email']`; the email
plugin wraps the unchanged `send_email`; the rich `run_email_notification` report path
is untouched. Task 6 Step 5 verifies the default routes to email only.

**Seam confirmations:** `send_email(subject, body, ...)` and `cfg(name, default)` are
confirmed (grounding). If `_config.SMTP_USER` is not the placeholder-detection point in
this build, adjust `EmailNotifyPlugin.is_configured` (Task 3) — its test pins the behaviour
via monkeypatching `send_email`, not the config.
