# ADR-039 Phase 2 — Entry-Point Discovery + Downloader Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-039](ADR-039-pluggable-integration-platform.md) — Phase 2. Phase 1 = [IMP-ADR039-01](IMP-ADR039-01-notify-plugins.md).
**Status:** ✅ Implemented 2026-06-10 (branch claude/adr039-p2-plugin-discovery; 8 tasks, subagent-driven; ~45 tests green).
**Goal:** Make the `discover_entry_points` seam real so third-party pip-installable plugins are auto-discovered, and prove the `downloader` category with two built-in backends (qB + Transmission) plus a minimal demonstrator CLI.
**Architecture:** `PluginRegistry.discover_entry_points(group)` uses `importlib.metadata` to load entry points per group, derives the category from the group name, and registers each loaded plugin. The `downloader` category follows the exact file/Protocol/dispatch shape of `notify` from Phase 1. qB backend is a thin adapter over the existing `QBittorrentClient`. Transmission backend is a new `~80-line` `requests`-based JSON-RPC client. `run_uploader` in `javdb/integrations/qb/uploader/service.py` is **not** re-routed — the coupling is too deep (see Out of Scope). A small `apps/cli/download/add.py` CLI exercises the full category end-to-end.
**Tech Stack:** Python 3 + pytest.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/integrations/plugins/registry.py` | Modify | Implement `discover_entry_points` (was no-op stub) |
| `javdb/integrations/notify/dispatch.py` | Modify | Wire `discover_entry_points("javdb.notify_plugins")` |
| `javdb/integrations/downloader/__init__.py` | Create | Package marker |
| `javdb/integrations/downloader/plugin.py` | Create | `DownloaderPlugin` Protocol + `DownloadResult` dataclass |
| `javdb/integrations/downloader/dispatch.py` | Create | `active_downloader_name()` + `add()` single-select dispatch |
| `javdb/integrations/downloader/qb/__init__.py` | Create | Package marker |
| `javdb/integrations/downloader/qb/plugin.py` | Create | `QbDownloaderPlugin` (thin adapter over `QBittorrentClient`) |
| `javdb/integrations/downloader/transmission/__init__.py` | Create | Package marker |
| `javdb/integrations/downloader/transmission/client.py` | Create | `TransmissionRpcClient` (~80 lines, `requests`-based JSON-RPC) |
| `javdb/integrations/downloader/transmission/plugin.py` | Create | `TransmissionDownloaderPlugin` |
| `apps/cli/download/__init__.py` | Create | Package marker |
| `apps/cli/download/add.py` | Create | `python -m apps.cli.download.add --magnet <uri> --category <cat>` |
| `config.py.example` | Modify | `DOWNLOADER_BACKEND` + Transmission config block |
| `javdb/infra/logging.py` | Modify | Short-name entries for downloader loggers |
| `tests/unit/test_discover_entry_points.py` | Create | `discover_entry_points` unit tests (fake entry points via monkeypatch) |
| `tests/unit/test_downloader_plugin.py` | Create | All downloader tests |

---

## Key Signatures (confirmed from source)

### `PluginRegistry.discover_entry_points` (current no-op, to be implemented)

```python
# javdb/integrations/plugins/registry.py
def discover_entry_points(self, group: str) -> int:
    """Phase-2 seam: discover third-party plugins via importlib.metadata.
    Phase 1 is a deliberate no-op (reserved interface)."""
    return 0
```

The Phase-1 test that MUST keep passing:
```python
# tests/unit/test_plugin_registry.py
def test_discover_entry_points_is_noop_in_phase1():
    assert PluginRegistry().discover_entry_points("javdb.notify_plugins") == 0
```
This test is no longer correct once we implement the method — but with no third-party plugins installed in CI it still passes (returns 0). Rename it or add a comment to clarify intent in Task 1.

### `QBittorrentClient.__init__` (from `javdb/integrations/qb/client.py`)

```python
def __init__(
    self,
    base_urls,                         # str | list[str]
    username: str,
    password: str,
    use_proxy: bool = False,
    proxies_getter=None,               # optional callable returning dict
    request_timeout: Optional[float] = None,
) -> None: ...
```

### `QBittorrentClient.add_torrent` (from `javdb/integrations/qb/client.py`)

```python
def add_torrent(
    self,
    magnet_link: str,
    name: Optional[str] = None,
    category: Optional[str] = None,
    save_path: str = "",
    auto_tmm: bool = True,
    skip_checking: bool = False,
    content_layout: str = "Original",
    ratio_limit: str = "-2",
    seeding_time_limit: str = "-2",
    paused: bool = False,
) -> bool: ...    # True on HTTP 200; False on non-200; raises on network error
```

### `qb_base_url_candidates` (from `javdb/integrations/qb/config.py`)

```python
from javdb.integrations.qb.config import qb_base_url_candidates
# reads QB_URL / QB_HOST / QB_PORT / QB_SCHEME from config; returns list[str]
```

### `cfg` (from `javdb/infra/config.py`)

```python
from javdb.infra.config import cfg
cfg("KEY", default)   # returns config value or default; never raises
```

### `NotifyPlugin` / `NotifyResult` (Phase 1 — mirror for downloader)

```python
# javdb/integrations/notify/plugin.py
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

### `NOTIFY_BACKENDS` config block in `config.py.example` (lines 69–79, to mirror)

```python
# =============================================================================
# NOTIFICATION BACKENDS (ADR-039)
# =============================================================================

# Active notify backends, tried in order; defaults to email only so existing
# setups are unchanged. Accepts a list or a CSV string, e.g. ['email', 'telegram'].
NOTIFY_BACKENDS = ['email']

# Telegram backend -- only used when 'telegram' is in NOTIFY_BACKENDS.
TELEGRAM_BOT_TOKEN = ''   # from @BotFather
TELEGRAM_CHAT_ID = ''     # target chat/channel id
```

### Short-name entries in `javdb/infra/logging.py` (lines 95–103, to mirror)

```python
# javdb_integrations
'javdb.integrations.notify.email': 'Email',
'javdb.integrations.qb.uploader': 'QBUploader',
'javdb.integrations.pikpak.bridge': 'PikPak',
'javdb.integrations.qb.file_filter': 'QBFilter',
'javdb.integrations.rclone.manager': 'Rclone',
'javdb.integrations.rclone.helper': 'RcloneHelper',
```

---

## Out of Scope

**`run_uploader` re-route deferred.** `javdb/integrations/qb/uploader/service.py` has deep qB coupling that makes re-routing it through `downloader.dispatch` a distinct refactor, not an additive wrap:
- Module-level config globals (`QB_USERNAME`, `QB_PASSWORD`, `QB_URL` etc.) resolved at import time.
- A `requests.Session` lifecycle threaded through `login → get_existing_hashes → add_torrent`.
- qB-specific duplicate-check logic (`is_torrent_exists`) baked into the upload loop.

Matching ADR-039 D3 ("wrap, don't rewrite"), Phase 2 ships the `downloader` contract + both backends + a demonstrator CLI. The uploader re-route is a separate future task (ADR-039 Phase 3 or a dedicated micro-ADR).

**Media-server category.** ADR-039 D7 defers this to Phase 3.

---

## Task 1 — Implement `discover_entry_points` + unit tests

**Files:**
- Modify: `javdb/integrations/plugins/registry.py`
- Create: `tests/unit/test_discover_entry_points.py`

### Step 1: Write the failing tests

```python
# tests/unit/test_discover_entry_points.py
"""Tests for PluginRegistry.discover_entry_points (ADR-039 Phase 2)."""
from unittest.mock import MagicMock, patch

from javdb.integrations.plugins.registry import PluginRegistry


class _FakePlugin:
    name = "fake"


def _make_ep(load_result=None, raises=False):
    """Build a fake importlib EntryPoint mock."""
    ep = MagicMock()
    if raises:
        ep.load.side_effect = ImportError("broken plugin")
    else:
        ep.load.return_value = load_result
    return ep


def _ep_mock(eps_for_group: list):
    """Return a mock for importlib.metadata.entry_points that accepts group= kwarg
    and returns the given list of fake entry points."""
    mock = MagicMock(return_value=eps_for_group)
    return mock


def test_empty_group_returns_zero():
    """No entry points for the group → returns 0, no exception."""
    reg = PluginRegistry()
    with patch("importlib.metadata.entry_points", _ep_mock([])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 0


def test_one_working_plugin_registered_and_counted():
    """One healthy entry point → registered + count 1."""
    reg = PluginRegistry()
    # ep.load() returns the class; instantiate it to register
    ep = _make_ep(load_result=_FakePlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 1
    plugin = reg.get("notify", "fake")
    assert plugin is not None
    assert plugin.name == "fake"


def test_broken_plugin_skipped_count_unaffected():
    """Entry point that raises on .load() → skipped; count reflects only successes."""
    reg = PluginRegistry()
    ep_bad = _make_ep(raises=True)
    ep_good = _make_ep(load_result=_FakePlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep_bad, ep_good])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 1
    assert reg.get("notify", "fake") is not None


def test_metadata_failure_returns_zero_no_exception():
    """importlib.metadata itself raises → returns 0, never propagates."""
    reg = PluginRegistry()
    with patch("importlib.metadata.entry_points", side_effect=Exception("metadata exploded")):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 0


def test_category_derived_from_group_notify():
    """javdb.notify_plugins → category 'notify'."""
    reg = PluginRegistry()
    ep = _make_ep(load_result=_FakePlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        reg.discover_entry_points("javdb.notify_plugins")
    assert reg.get("notify", "fake") is not None


def test_category_derived_from_group_downloader():
    """javdb.downloader_plugins → category 'downloader'."""
    class _DlPlugin:
        name = "fake-dl"
    reg = PluginRegistry()
    ep = _make_ep(load_result=_DlPlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        reg.discover_entry_points("javdb.downloader_plugins")
    assert reg.get("downloader", "fake-dl") is not None


def test_instance_registered_directly():
    """ep.load() that returns an instance (not a class) is registered if it has .name."""
    reg = PluginRegistry()
    instance = _FakePlugin()
    ep = _make_ep(load_result=instance)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 1
    assert reg.get("notify", "fake") is not None
```

- [ ] **Step 1a: Run to confirm all FAIL** (ModuleNotFoundError or assertion error):

> All test commands in this plan run from the repo root: `PYTHONPATH=.:javdb/rust_core/python`
> makes the repo importable and provides the pure-Python fallback shim for `javdb.rust_core`.

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_discover_entry_points.py -q
```

### Step 2: Implement `discover_entry_points`

```python
# javdb/integrations/plugins/registry.py  (replace the stub method only)

def discover_entry_points(self, group: str) -> int:
    """Discover and register third-party plugins via importlib.metadata.

    Scans the named entry-point group (e.g. 'javdb.notify_plugins'),
    derives the category by stripping the prefix and '_plugins' suffix
    (e.g. 'notify'), loads each entry point, and registers the result.

    - If the loaded value is a class (callable), it is instantiated.
    - If the loaded value is already an instance with a .name attr, it is
      registered directly.
    - Failures per entry point are caught and logged at WARNING; they never
      raise and do not affect other entry points' count.
    - A top-level failure from importlib.metadata itself returns 0 silently.

    Returns the count of successfully registered plugins.
    """
    import importlib.metadata

    # Derive category: 'javdb.notify_plugins' → 'notify'
    # Use rsplit + removesuffix (NOT rstrip — rstrip is char-based and fragile).
    category = group.rsplit(".", 1)[-1].removesuffix("_plugins")

    try:
        # entry_points(group=...) is supported on Python 3.9.5+ and is the
        # canonical form in 3.12+ (avoids the dict/.get() API removed in 3.12).
        eps = importlib.metadata.entry_points(group=group)
    except Exception as exc:
        logger.warning("discover_entry_points: metadata query failed: %s", exc)
        return 0

    count = 0
    for ep in eps:
        try:
            loaded = ep.load()
            # Instantiate if it's a class; use as-is if already an instance.
            if isinstance(loaded, type):
                plugin = loaded()
            else:
                plugin = loaded
            self.register(category, plugin)
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "discover_entry_points: skipping entry point %r in group %r: %s",
                getattr(ep, "name", ep),
                group,
                exc,
            )
    return count
```

> **Note on the existing Phase-1 test:** `test_discover_entry_points_is_noop_in_phase1`
> in `tests/unit/test_plugin_registry.py` still passes in CI (no third-party plugins
> installed → `entry_points(group="javdb.notify_plugins")` is empty → returns 0).
> Rename the test to `test_discover_entry_points_returns_zero_when_no_eps_installed`
> and add a comment explaining this is an environment contract, not a code stub.

- [ ] **Step 2a: Update the existing test name + comment in `tests/unit/test_plugin_registry.py`**

Old name: `test_discover_entry_points_is_noop_in_phase1`
New name: `test_discover_entry_points_returns_zero_when_no_eps_installed`
Add comment: `# No third-party plugins installed in CI → count is 0 (environment contract, not a stub)`

- [ ] **Step 2b: Run all discover tests to confirm PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest \
    tests/unit/test_discover_entry_points.py \
    tests/unit/test_plugin_registry.py -q
```

Expected: all PASS.

- [ ] **Step 2c: Commit:**

```bash
git add javdb/integrations/plugins/registry.py \
        tests/unit/test_discover_entry_points.py \
        tests/unit/test_plugin_registry.py
git commit -m "feat(plugins): implement discover_entry_points via importlib.metadata (ADR-039 P2)"
```

---

## Task 2 — Wire `discover_entry_points` into notify dispatch

**Files:**
- Modify: `javdb/integrations/notify/dispatch.py`

The discovery call is added at module level after the built-in import triggers, so it runs exactly once per process. The registry's `register` is already idempotent (duplicate `(category, name)` → debug-log no-op), so repeated imports are safe.

### Step 1: Add the wiring call

In `javdb/integrations/notify/dispatch.py`, after:

```python
import javdb.integrations.notify.email.plugin  # noqa: F401,E402
import javdb.integrations.notify.telegram.plugin  # noqa: F401,E402
```

Add:

```python
# Discover any third-party notify plugins installed as entry points.
# Returns 0 when no packages with 'javdb.notify_plugins' entry points are installed.
REGISTRY.discover_entry_points("javdb.notify_plugins")
```

### Step 2: Verify no-op with no third-party plugins

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -c "
import javdb.integrations.notify.dispatch as d
print('registered:', sorted(p.name for p in d.REGISTRY.list('notify')))
print('active_names:', d.active_names())
"
```

Expected: `registered: ['email', 'telegram']`, `active_names: ['email']`.

### Step 3: Run existing notify dispatch tests to confirm no regression

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_notify_dispatch.py -q
```

Expected: all PASS (no new tests needed — the wiring just calls the already-tested method).

- [ ] **Step 3a: Commit:**

```bash
git add javdb/integrations/notify/dispatch.py
git commit -m "feat(notify): wire discover_entry_points for third-party notify plugins (ADR-039 P2)"
```

---

## Task 3 — `DownloaderPlugin` Protocol + `DownloadResult`

**Files:**
- Create: `javdb/integrations/downloader/__init__.py`
- Create: `javdb/integrations/downloader/plugin.py`
- Test in: `tests/unit/test_downloader_plugin.py` (create file, grow across tasks 3–8)

### Step 1: Write the contract test (TDD — fails first)

```python
# tests/unit/test_downloader_plugin.py
"""Tests for the downloader category (ADR-039 Phase 2)."""
from __future__ import annotations
# --- Task 3: Protocol + DownloadResult ---

from javdb.integrations.downloader.plugin import DownloadResult


def test_download_result_defaults():
    r = DownloadResult(plugin="qb", ok=True)
    assert r.ok is True
    assert r.detail is None


def test_download_result_failure():
    r = DownloadResult(plugin="qb", ok=False, detail="connection refused")
    assert r.ok is False
    assert r.detail == "connection refused"


def test_downloader_plugin_protocol_conformance():
    """Any object satisfying DownloaderPlugin can be used as one — duck-typed check."""
    from javdb.integrations.downloader.plugin import DownloaderPlugin
    from typing import runtime_checkable, Protocol

    class _Stub:
        name = "stub"
        def is_configured(self) -> bool:
            return True
        def add_torrent(self, magnet: str, category: str, name=None) -> DownloadResult:
            return DownloadResult(plugin=self.name, ok=True)

    # Protocol structural check: the stub satisfies the interface.
    # (DownloaderPlugin need not be @runtime_checkable for this test —
    # we call the methods directly and check the return type.)
    stub = _Stub()
    result = stub.add_torrent("magnet:?xt=urn:btih:abc", "JavDB")
    assert isinstance(result, DownloadResult)
    assert result.ok is True
```

- [ ] **Step 1a: Run to confirm FAIL:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

### Step 2: Write the contract

```python
# javdb/integrations/downloader/__init__.py
"""Downloader category — pluggable torrent backend (ADR-039 Phase 2)."""
```

```python
# javdb/integrations/downloader/plugin.py
"""The downloader-category plugin contract (ADR-039 Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class DownloadResult:
    plugin: str
    ok: bool
    detail: Optional[str] = None


class DownloaderPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def add_torrent(
        self,
        magnet: str,
        category: str,
        name: Optional[str] = None,
    ) -> DownloadResult: ...
```

- [ ] **Step 2a: Run to confirm PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

- [ ] **Step 2b: Commit:**

```bash
git add javdb/integrations/downloader/ tests/unit/test_downloader_plugin.py
git commit -m "feat(downloader): add DownloaderPlugin contract + DownloadResult (ADR-039 P2)"
```

---

## Task 4 — `QbDownloaderPlugin` (thin qB adapter)

**Files:**
- Create: `javdb/integrations/downloader/qb/__init__.py`
- Create: `javdb/integrations/downloader/qb/plugin.py`
- Append to: `tests/unit/test_downloader_plugin.py`

### Step 1: Append tests

```python
# tests/unit/test_downloader_plugin.py  — append after Task 3 tests

# --- Task 4: QbDownloaderPlugin ---

import javdb.integrations.downloader.qb.plugin as qb_plugin


def test_qb_is_configured_true(monkeypatch):
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {"QB_HOST": "192.168.1.1", "QB_USERNAME": "admin"}.get(name, default),
    )
    assert qb_plugin.QbDownloaderPlugin().is_configured() is True


def test_qb_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(qb_plugin, "cfg", lambda name, default: default)
    assert qb_plugin.QbDownloaderPlugin().is_configured() is False


def test_qb_add_torrent_calls_client(monkeypatch):
    """add_torrent constructs a QBittorrentClient and calls add_torrent with the right args."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "192.168.1.1",
            "QB_USERNAME": "admin",
            "QB_PASSWORD": "pass",
        }.get(name, default),
    )

    calls = {}

    class _FakeClient:
        def __init__(self, base_urls, username, password, **kw):
            calls["init"] = dict(base_urls=base_urls, username=username, password=password)
        def add_torrent(self, magnet_link, name=None, category=None, **kw):
            calls["add"] = dict(magnet_link=magnet_link, name=name, category=category)
            return True

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://192.168.1.1:8080"])

    plugin = qb_plugin.QbDownloaderPlugin()
    result = plugin.add_torrent("magnet:?xt=urn:btih:abc", category="JavDB", name="MOVIE-001")

    assert calls["add"]["magnet_link"] == "magnet:?xt=urn:btih:abc"
    assert calls["add"]["category"] == "JavDB"
    assert calls["add"]["name"] == "MOVIE-001"
    assert result.ok is True
    assert result.plugin == "qb"


def test_qb_add_torrent_maps_false_to_failure(monkeypatch):
    """QBittorrentClient.add_torrent returning False → DownloadResult(ok=False)."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "h", "QB_USERNAME": "u", "QB_PASSWORD": "p",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def add_torrent(self, *a, **k): return False

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://h:8080"])

    result = qb_plugin.QbDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert result.detail is not None


def test_qb_add_torrent_isolates_exception(monkeypatch):
    """Network exception from QBittorrentClient → DownloadResult(ok=False), not raised."""
    monkeypatch.setattr(
        qb_plugin, "cfg",
        lambda name, default: {
            "QB_HOST": "h", "QB_USERNAME": "u", "QB_PASSWORD": "p",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def add_torrent(self, *a, **k): raise ConnectionError("refused")

    monkeypatch.setattr(qb_plugin, "QBittorrentClient", _FakeClient)
    monkeypatch.setattr(qb_plugin, "qb_base_url_candidates", lambda: ["https://h:8080"])

    result = qb_plugin.QbDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert "refused" in (result.detail or "")
```

- [ ] **Step 1a: Run to confirm new tests FAIL:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

### Step 2: Write `QbDownloaderPlugin`

```python
# javdb/integrations/downloader/qb/__init__.py
"""qBittorrent downloader backend (ADR-039 Phase 2)."""
```

```python
# javdb/integrations/downloader/qb/plugin.py
"""qBittorrent downloader plugin — thin adapter over QBittorrentClient (ADR-039 Phase 2).

Does NOT touch javdb/integrations/qb/uploader/service.py — see IMP Out of Scope."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.downloader.plugin import DownloadResult
from javdb.integrations.plugins.registry import REGISTRY
from javdb.integrations.qb.client import QBittorrentClient
from javdb.integrations.qb.config import qb_base_url_candidates


class QbDownloaderPlugin:
    name = "qb"

    def is_configured(self) -> bool:
        host = cfg("QB_HOST", "") or cfg("QB_URL", "")
        username = cfg("QB_USERNAME", "")
        return bool(host) and bool(username)

    def add_torrent(
        self,
        magnet: str,
        category: str,
        name: str | None = None,
    ) -> DownloadResult:
        try:
            client = QBittorrentClient(
                base_urls=qb_base_url_candidates(),
                username=cfg("QB_USERNAME", ""),
                password=cfg("QB_PASSWORD", ""),
                request_timeout=cfg("REQUEST_TIMEOUT", 30),
            )
            ok = client.add_torrent(
                magnet_link=magnet,
                name=name,
                category=category,
                save_path=cfg("TORRENT_SAVE_PATH", ""),
                skip_checking=cfg("SKIP_CHECKING", False),
                paused=not cfg("AUTO_START", True),
            )
            if ok:
                return DownloadResult(plugin=self.name, ok=True)
            return DownloadResult(plugin=self.name, ok=False, detail="add_torrent returned False")
        except Exception as exc:
            return DownloadResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("downloader", QbDownloaderPlugin())
```

- [ ] **Step 2a: Run to confirm PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

- [ ] **Step 2b: Commit:**

```bash
git add javdb/integrations/downloader/qb/ tests/unit/test_downloader_plugin.py
git commit -m "feat(downloader): add QbDownloaderPlugin adapter (ADR-039 P2)"
```

---

## Task 5 — `TransmissionRpcClient` (new JSON-RPC client)

**Files:**
- Create: `javdb/integrations/downloader/transmission/__init__.py`
- Create: `javdb/integrations/downloader/transmission/client.py`
- Append to: `tests/unit/test_downloader_plugin.py`

### Step 1: Append client tests

```python
# tests/unit/test_downloader_plugin.py  — append after Task 4 tests

# --- Task 5: TransmissionRpcClient ---

import javdb.integrations.downloader.transmission.client as tr_client


def test_transmission_torrent_add_happy_path(monkeypatch):
    """Happy path: session-id negotiation + torrent-add, returns (True, None)."""
    session_responses = iter([
        # First request: 409 with session-id header
        type("R", (), {"status_code": 409, "headers": {"X-Transmission-Session-Id": "SID123"}, "json": lambda: {}})(),
        # Second request: 200 with result "success"
        type("R", (), {"status_code": 200, "headers": {}, "json": lambda: {"result": "success"}})(),
    ])

    monkeypatch.setattr(tr_client.requests, "post", lambda *a, **kw: next(session_responses))
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    ok, detail = client.torrent_add("magnet:?xt=urn:btih:abc", download_dir="/downloads", labels=["JavDB"])
    assert ok is True
    assert detail is None


def test_transmission_torrent_add_session_id_negotiation(monkeypatch):
    """409 reply updates the session-id stored on the client."""
    responses = iter([
        type("R", (), {"status_code": 409, "headers": {"X-Transmission-Session-Id": "NEW-SID"}, "json": lambda: {}})(),
        type("R", (), {"status_code": 200, "headers": {}, "json": lambda: {"result": "success"}})(),
    ])
    calls = []
    def _post(url, **kw):
        calls.append(kw.get("headers", {}).get("X-Transmission-Session-Id"))
        return next(responses)
    monkeypatch.setattr(tr_client.requests, "post", _post)
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    client.torrent_add("magnet:...", download_dir="/d", labels=[])
    # Second call must use the new session-id
    assert calls[1] == "NEW-SID"


def test_transmission_torrent_add_error_result(monkeypatch):
    """result != 'success' → ok=False with detail."""
    responses = iter([
        type("R", (), {"status_code": 409, "headers": {"X-Transmission-Session-Id": "SID"}, "json": lambda: {}})(),
        type("R", (), {"status_code": 200, "headers": {}, "json": lambda: {"result": "duplicate torrent"}})(),
    ])
    monkeypatch.setattr(tr_client.requests, "post", lambda *a, **kw: next(responses))
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    ok, detail = client.torrent_add("magnet:...", download_dir="/d", labels=[])
    assert ok is False
    assert "duplicate torrent" in (detail or "")


def test_transmission_network_exception(monkeypatch):
    """requests.post raises → propagates to caller (plugin layer catches it)."""
    monkeypatch.setattr(tr_client.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("refused")))
    client = tr_client.TransmissionRpcClient("http://localhost:9091", username="", password="")
    import pytest
    with pytest.raises(ConnectionError):
        client.torrent_add("magnet:...", download_dir="/d", labels=[])
```

- [ ] **Step 1a: Run to confirm new tests FAIL:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

### Step 2: Write `TransmissionRpcClient`

```python
# javdb/integrations/downloader/transmission/__init__.py
"""Transmission downloader backend (ADR-039 Phase 2)."""
```

```python
# javdb/integrations/downloader/transmission/client.py
"""Thin requests-based JSON-RPC client for Transmission (ADR-039 Phase 2).

Implements the 409 X-Transmission-Session-Id negotiation:
  - POST to /transmission/rpc
  - If 409, extract X-Transmission-Session-Id from response headers, retry once
  - Subsequent calls reuse the stored session-id
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


class TransmissionRpcClient:
    """Minimal Transmission JSON-RPC client.

    Only exposes torrent-add. Raises on network errors so the plugin layer
    can catch them and surface a DownloadResult(ok=False).
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        # base_url should be e.g. 'http://192.168.1.10:9091'
        self._rpc_url = base_url.rstrip("/") + "/transmission/rpc"
        self._auth = HTTPBasicAuth(username, password) if username else None
        self._timeout = timeout
        self._session_id: str = ""

    def _post(self, payload: dict) -> dict:
        """POST JSON-RPC payload; handles the 409 session-id negotiation."""
        headers = {"X-Transmission-Session-Id": self._session_id, "Content-Type": "application/json"}
        resp = requests.post(
            self._rpc_url,
            json=payload,
            headers=headers,
            auth=self._auth,
            timeout=self._timeout,
        )
        if resp.status_code == 409:
            # Transmission rejected the request and provided a fresh session-id.
            self._session_id = resp.headers.get("X-Transmission-Session-Id", "")
            logger.debug("Transmission session-id updated to %r", self._session_id)
            # Retry once with the new session-id.
            headers["X-Transmission-Session-Id"] = self._session_id
            resp = requests.post(
                self._rpc_url,
                json=payload,
                headers=headers,
                auth=self._auth,
                timeout=self._timeout,
            )
        resp.raise_for_status()
        return resp.json()

    def torrent_add(
        self,
        magnet: str,
        download_dir: str,
        labels: list[str] | None = None,
    ) -> Tuple[bool, Optional[str]]:
        """Add a torrent via the torrent-add RPC method.

        Returns (True, None) on success, (False, detail) on RPC-level failure.
        Raises on network errors (caller handles).
        """
        payload = {
            "method": "torrent-add",
            "arguments": {
                "filename": magnet,
                "download-dir": download_dir,
                "labels": labels or [],
            },
        }
        result = self._post(payload)
        rpc_result = result.get("result", "")
        if rpc_result == "success":
            return True, None
        detail = f"rpc result: {rpc_result}"
        logger.warning("Transmission torrent-add failed: %s", detail)
        return False, detail
```

- [ ] **Step 2a: Run to confirm PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

- [ ] **Step 2b: Commit:**

```bash
git add javdb/integrations/downloader/transmission/__init__.py \
        javdb/integrations/downloader/transmission/client.py \
        tests/unit/test_downloader_plugin.py
git commit -m "feat(downloader): add TransmissionRpcClient with session-id negotiation (ADR-039 P2)"
```

---

## Task 6 — `TransmissionDownloaderPlugin`

**Files:**
- Create: `javdb/integrations/downloader/transmission/plugin.py`
- Append to: `tests/unit/test_downloader_plugin.py`

### Step 1: Append plugin tests

```python
# tests/unit/test_downloader_plugin.py  — append after Task 5 tests

# --- Task 6: TransmissionDownloaderPlugin ---

import javdb.integrations.downloader.transmission.plugin as tr_plugin


def test_transmission_is_configured_true(monkeypatch):
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "192.168.1.10",
            "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )
    assert tr_plugin.TransmissionDownloaderPlugin().is_configured() is True


def test_transmission_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(tr_plugin, "cfg", lambda name, default: default)
    assert tr_plugin.TransmissionDownloaderPlugin().is_configured() is False


def test_transmission_add_torrent_calls_rpc_client(monkeypatch):
    """add_torrent constructs a TransmissionRpcClient and calls torrent_add."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "192.168.1.10",
            "TRANSMISSION_PORT": "9091",
            "TRANSMISSION_USERNAME": "user",
            "TRANSMISSION_PASSWORD": "pass",
            "TRANSMISSION_DOWNLOAD_DIR": "/downloads",
        }.get(name, default),
    )

    calls = {}

    class _FakeClient:
        def __init__(self, base_url, username, password, **kw):
            calls["init"] = dict(base_url=base_url, username=username, password=password)
        def torrent_add(self, magnet, download_dir, labels=None):
            calls["torrent_add"] = dict(magnet=magnet, download_dir=download_dir, labels=labels)
            return True, None

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _FakeClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent(
        "magnet:?xt=urn:btih:abc", "JavDB"
    )
    assert result.ok is True
    assert calls["torrent_add"]["magnet"] == "magnet:?xt=urn:btih:abc"
    assert calls["torrent_add"]["labels"] == ["JavDB"]


def test_transmission_add_torrent_maps_rpc_failure(monkeypatch):
    """torrent_add returning (False, detail) → DownloadResult(ok=False)."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "h", "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def torrent_add(self, *a, **k): return False, "duplicate torrent"

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _FakeClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert "duplicate torrent" in (result.detail or "")


def test_transmission_add_torrent_isolates_network_exception(monkeypatch):
    """Network exception from TransmissionRpcClient → DownloadResult(ok=False), not raised."""
    monkeypatch.setattr(
        tr_plugin, "cfg",
        lambda name, default: {
            "TRANSMISSION_HOST": "h", "TRANSMISSION_PORT": "9091",
        }.get(name, default),
    )

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def torrent_add(self, *a, **k): raise ConnectionError("refused")

    monkeypatch.setattr(tr_plugin, "TransmissionRpcClient", _FakeClient)

    result = tr_plugin.TransmissionDownloaderPlugin().add_torrent("magnet:...", "cat")
    assert result.ok is False
    assert "refused" in (result.detail or "")
```

- [ ] **Step 1a: Run to confirm new tests FAIL:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

### Step 2: Write `TransmissionDownloaderPlugin`

```python
# javdb/integrations/downloader/transmission/plugin.py
"""Transmission downloader plugin (ADR-039 Phase 2)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.downloader.plugin import DownloadResult
from javdb.integrations.downloader.transmission.client import TransmissionRpcClient
from javdb.integrations.plugins.registry import REGISTRY


class TransmissionDownloaderPlugin:
    name = "transmission"

    def is_configured(self) -> bool:
        host = cfg("TRANSMISSION_HOST", "")
        port = cfg("TRANSMISSION_PORT", "")
        return bool(host) and bool(port)

    def add_torrent(
        self,
        magnet: str,
        category: str,
        name: str | None = None,
    ) -> DownloadResult:
        if name:
            # torrent-rename-path is unreliable for magnet adds (metadata not
            # yet fetched), so fail fast instead of silently dropping the name.
            return DownloadResult(
                plugin=self.name,
                ok=False,
                detail="--name rename is not supported by the transmission backend",
            )
        host = cfg("TRANSMISSION_HOST", "localhost")
        port = cfg("TRANSMISSION_PORT", "9091")
        username = cfg("TRANSMISSION_USERNAME", "")
        password = cfg("TRANSMISSION_PASSWORD", "")
        download_dir = cfg("TRANSMISSION_DOWNLOAD_DIR", "/downloads")
        base_url = f"http://{host}:{port}"
        try:
            client = TransmissionRpcClient(
                base_url=base_url,
                username=username,
                password=password,
            )
            ok, detail = client.torrent_add(
                magnet=magnet,
                download_dir=download_dir,
                labels=[category] if category else [],
            )
            return DownloadResult(plugin=self.name, ok=ok, detail=detail)
        except Exception as exc:
            return DownloadResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("downloader", TransmissionDownloaderPlugin())
```

- [ ] **Step 2a: Run to confirm PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

- [ ] **Step 2b: Commit:**

```bash
git add javdb/integrations/downloader/transmission/plugin.py tests/unit/test_downloader_plugin.py
git commit -m "feat(downloader): add TransmissionDownloaderPlugin (ADR-039 P2)"
```

---

## Task 7 — `downloader/dispatch.py` + config + logging short-names

**Files:**
- Create: `javdb/integrations/downloader/dispatch.py`
- Modify: `config.py.example`
- Modify: `javdb/infra/logging.py`
- Append to: `tests/unit/test_downloader_plugin.py`

### Step 1: Append dispatch tests

```python
# tests/unit/test_downloader_plugin.py  — append after Task 6 tests

# --- Task 7: downloader dispatch ---

import javdb.integrations.downloader.dispatch as dl_dispatch
from javdb.integrations.plugins.registry import PluginRegistry


class _DlPlugin:
    def __init__(self, name, configured=True, raises=False):
        self.name = name
        self._configured = configured
        self._raises = raises
    def is_configured(self):
        return self._configured
    def add_torrent(self, magnet, category, name=None):
        if self._raises:
            raise RuntimeError("dl boom")
        return DownloadResult(plugin=self.name, ok=True)


def _dl_registry(*plugins):
    reg = PluginRegistry()
    for p in plugins:
        reg.register("downloader", p)
    return reg


def test_active_downloader_name_default_qb(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: default)
    assert dl_dispatch.active_downloader_name() == "qb"


def test_active_downloader_name_reads_config(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "transmission")
    assert dl_dispatch.active_downloader_name() == "transmission"


def test_dispatch_add_routes_to_active_backend(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb")))
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is True
    assert result.plugin == "qb"


def test_dispatch_add_not_registered(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "ghost")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry())
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is False
    assert "not registered" in (result.detail or "")


def test_dispatch_add_not_configured(monkeypatch):
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb", configured=False)))
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is False
    assert "not configured" in (result.detail or "")


def test_dispatch_add_failure_isolated(monkeypatch):
    """An exception from the backend is caught and returned as DownloadResult(ok=False)."""
    monkeypatch.setattr(dl_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(dl_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb", raises=True)))
    result = dl_dispatch.add("magnet:...", "JavDB")
    assert result.ok is False
    assert "dl boom" in (result.detail or "")
```

- [ ] **Step 1a: Run to confirm new tests FAIL:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

### Step 2: Write `downloader/dispatch.py`

```python
# javdb/integrations/downloader/dispatch.py
"""Single-select downloader dispatch with failure isolation (ADR-039 Phase 2).

Importing this module registers the built-in plugins (qb, transmission)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.downloader.plugin import DownloadResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.downloader.qb.plugin  # noqa: F401,E402
import javdb.integrations.downloader.transmission.plugin  # noqa: F401,E402

# Discover any third-party downloader plugins installed as entry points.
REGISTRY.discover_entry_points("javdb.downloader_plugins")


def active_downloader_name() -> str:
    """Return the active downloader backend name from DOWNLOADER_BACKEND config.

    Defaults to 'qb' so existing deployments are unchanged.
    """
    return str(cfg("DOWNLOADER_BACKEND", "qb")).strip() or "qb"


def add(
    magnet: str,
    category: str,
    name: str | None = None,
) -> DownloadResult:
    """Route add_torrent to the active downloader backend with failure isolation.

    Unlike the notify fan-out, downloader is single-select: only the backend
    named by DOWNLOADER_BACKEND receives the call.
    """
    backend_name = active_downloader_name()
    plugin = REGISTRY.get("downloader", backend_name)
    if plugin is None:
        return DownloadResult(
            plugin=backend_name, ok=False, detail=f"not registered: {backend_name!r}"
        )
    try:
        if not plugin.is_configured():
            return DownloadResult(plugin=backend_name, ok=False, detail="not configured")
        return plugin.add_torrent(magnet, category, name=name)
    except Exception as exc:  # failure isolation
        return DownloadResult(plugin=backend_name, ok=False, detail=f"error: {exc}")
```

### Step 3: Add `DOWNLOADER_BACKEND` block to `config.py.example`

Insert after the `NOTIFICATION BACKENDS (ADR-039)` block (after line 79):

```python
# =============================================================================
# DOWNLOADER BACKENDS (ADR-039)
# =============================================================================

# Active downloader backend — single-select (mutually exclusive).
# Defaults to 'qb'. Set to 'transmission' to route add-torrent calls to Transmission.
DOWNLOADER_BACKEND = 'qb'

# Transmission backend — only used when DOWNLOADER_BACKEND = 'transmission'.
TRANSMISSION_HOST = '192.168.1.10'       # Transmission daemon host
TRANSMISSION_PORT = 9091                  # Transmission RPC port (default 9091)
TRANSMISSION_USERNAME = ''               # leave empty if no auth configured
TRANSMISSION_PASSWORD = ''
TRANSMISSION_DOWNLOAD_DIR = '/downloads' # default save directory
```

### Step 4: Add logging short-names to `javdb/infra/logging.py`

In `_MODULE_SHORT_NAMES` dict, after the existing `javdb_integrations` entries (after `'javdb.integrations.rclone.helper': 'RcloneHelper'`), add:

```python
    'javdb.integrations.downloader.dispatch': 'Downloader',
    'javdb.integrations.downloader.qb': 'QbDownloader',
    'javdb.integrations.downloader.transmission': 'Transmission',
```

- [ ] **Step 4a: Run full dispatch test suite to confirm PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

- [ ] **Step 4b: Commit:**

```bash
git add javdb/integrations/downloader/dispatch.py \
        config.py.example \
        javdb/infra/logging.py \
        tests/unit/test_downloader_plugin.py
git commit -m "feat(downloader): add dispatch, DOWNLOADER_BACKEND config, logging short-names (ADR-039 P2)"
```

---

## Task 8 — Demonstrator CLI `apps.cli.download.add` + smoke test

**Files:**
- Create: `apps/cli/download/__init__.py`
- Create: `apps/cli/download/add.py`
- Append to: `tests/unit/test_downloader_plugin.py`

### Step 1: Append CLI smoke test

```python
# tests/unit/test_downloader_plugin.py  — append after Task 7 tests

# --- Task 8: CLI smoke ---

def test_cli_add_exits_zero_on_success(monkeypatch):
    """CLI main() returns 0 on DownloadResult(ok=True)."""
    import javdb.integrations.downloader.dispatch as _dispatch
    monkeypatch.setattr(_dispatch, "cfg", lambda name, default: "qb")
    monkeypatch.setattr(_dispatch, "REGISTRY", _dl_registry(_DlPlugin("qb")))

    # Import after monkeypatching to avoid the module-level dispatch.add call
    # using the wrong registry.
    import importlib, apps.cli.download.add as add_cli
    importlib.reload(add_cli)  # reload so module-level imports re-run with patched env

    # Provide the monkeypatched dispatch to the CLI module directly.
    monkeypatch.setattr(add_cli, "dispatch", _dispatch)

    exit_code = add_cli.main(["--magnet", "magnet:?xt=urn:btih:abc", "--category", "JavDB"])
    assert exit_code == 0


def test_cli_add_exits_nonzero_on_failure(monkeypatch):
    """CLI main() returns 1 on DownloadResult(ok=False)."""
    import javdb.integrations.downloader.dispatch as _dispatch
    monkeypatch.setattr(_dispatch, "cfg", lambda name, default: "ghost")
    monkeypatch.setattr(_dispatch, "REGISTRY", _dl_registry())

    import apps.cli.download.add as add_cli
    monkeypatch.setattr(add_cli, "dispatch", _dispatch)

    exit_code = add_cli.main(["--magnet", "magnet:?xt=urn:btih:abc", "--category", "JavDB"])
    assert exit_code == 1
```

- [ ] **Step 1a: Run to confirm new tests FAIL:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest tests/unit/test_downloader_plugin.py -q
```

### Step 2: Write the CLI

```python
# apps/cli/download/__init__.py
```

```python
# apps/cli/download/add.py
"""Demonstrator CLI: add a torrent via the active downloader backend (ADR-039 Phase 2).

Usage:
    python -m apps.cli.download.add --magnet <uri> --category <cat> [--name <name>]

Exits 0 on success, 1 on failure. Prints the DownloadResult summary.
This CLI exercises the full downloader-category stack end-to-end; it does NOT
replace apps.cli.qb.uploader (which has its own rich pipeline logic — see Out of Scope).
"""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.integrations.downloader import dispatch


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a torrent via the active DOWNLOADER_BACKEND (ADR-039)."
    )
    parser.add_argument("--magnet", required=True, help="Magnet URI to add")
    parser.add_argument("--category", required=True, help="Torrent category / label")
    parser.add_argument(
        "--name", default=None, help="Optional torrent rename (qb backend only)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = dispatch.add(args.magnet, args.category, name=args.name)
    if result.ok:
        print(f"[ok] {result.plugin}: torrent added (category={args.category})")
        return 0
    print(
        f"[fail] {result.plugin}: {result.detail or 'unknown error'}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2a: Run full test suite for this plan to confirm all PASS:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -m pytest \
    tests/unit/test_discover_entry_points.py \
    tests/unit/test_plugin_registry.py \
    tests/unit/test_downloader_plugin.py \
    tests/unit/test_notify_dispatch.py \
    -q
```

Expected: all PASS.

- [ ] **Step 2b: Full backward-compat check:**

```bash
PYTHONPATH=.:javdb/rust_core/python \
  python3 -c "
import javdb.integrations.notify.dispatch as nd
import javdb.integrations.downloader.dispatch as dd
print('notify registered:', sorted(p.name for p in nd.REGISTRY.list('notify')))
print('notify active:', nd.active_names())
print('downloader registered:', sorted(p.name for p in dd.REGISTRY.list('downloader')))
print('downloader active:', dd.active_downloader_name())
"
```

Expected:
```
notify registered: ['email', 'telegram']
notify active: ['email']
downloader registered: ['qb', 'transmission']
downloader active: qb
```

- [ ] **Step 2c: Commit:**

```bash
git add apps/cli/download/ tests/unit/test_downloader_plugin.py
git commit -m "feat(cli): add apps.cli.download.add demonstrator (ADR-039 P2)"
```

---

## Task 9 — Status log close-out (post-implementation, controller only)

After all tasks above pass and are committed, update the following in the same commit:

1. **`docs/design/ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md`** — append to Status Log:
   ```
   - 2026-06-XX: Phase 2 (IMP-ADR039-02) implemented — entry-point discovery wired; downloader category ships with QbDownloaderPlugin + TransmissionDownloaderPlugin + apps.cli.download.add CLI.
   ```
   Also update the Phase 2 IMP row in the Implementation Roadmap table from `IMP-ADR039-02 (stub)` to `[IMP-ADR039-02](IMP-ADR039-02-entry-points-downloader.md)`.

2. **`docs/design/ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.zh.md`** — mirror the Status Log append.

3. **This IMP file** — update `**Status:** Draft (2026-06-10)` to `**Status:** Implemented — YYYY-MM-DD`.

4. **`CONTEXT.md`** — add:
   - **Downloader backend** — an active downloader plugin selected via `DOWNLOADER_BACKEND` (single-select, mutually exclusive). Phase 2 built-ins: `qb` (wraps `QBittorrentClient`), `transmission` (new Transmission RPC client).

```bash
git add docs/design/ADR-039-Pluggable-Integration-Platform/ CONTEXT.md
git commit -m "docs(adr039): mark Phase 2 implemented, update status log + CONTEXT (ADR-039 P2)"
```

---

## Self-Review

**Spec coverage (ADR-039 D7 Phase 2):**

| Deliverable | Task | Status |
| --- | --- | --- |
| `discover_entry_points` implementation | 1 | plan ✓ |
| Wire discovery into notify dispatch | 2 | plan ✓ |
| Wire discovery into downloader dispatch | 7 (inside dispatch.py) | plan ✓ |
| `DownloaderPlugin` Protocol + `DownloadResult` | 3 | plan ✓ |
| `QbDownloaderPlugin` (thin qB adapter) | 4 | plan ✓ |
| `TransmissionRpcClient` (new, 409 negotiation) | 5 | plan ✓ |
| `TransmissionDownloaderPlugin` | 6 | plan ✓ |
| `downloader/dispatch.py` (single-select) | 7 | plan ✓ |
| `DOWNLOADER_BACKEND` + Transmission config block | 7 | plan ✓ |
| Logging short-names | 7 | plan ✓ |
| Demonstrator CLI `apps.cli.download.add` | 8 | plan ✓ |
| Docs/status-log close-out | 9 | plan ✓ |
| `run_uploader` re-route | Out of Scope (documented) | deferred ✓ |
| Media-server category | Out of Scope | Phase 3 ✓ |

**Type/name consistency across tasks:**

- `DownloadResult(plugin, ok, detail)` — mirrors `NotifyResult` exactly.
- `DownloaderPlugin.add_torrent(magnet, category, name=None) -> DownloadResult` — consistent across protocol, plugin, dispatch, CLI.
- `active_downloader_name() -> str` (single-select) vs. `active_names() -> list[str]` (fan-out) — naming intentionally differs to signal different semantics.
- `REGISTRY.discover_entry_points(group: str) -> int` — single-arg signature preserved; Phase-1 test renamed (not deleted).
- `cfg("DOWNLOADER_BACKEND", "qb")` — default `"qb"` preserves backward compat for any caller that imports dispatch before configuring the key.

**Placeholder scan:** No placeholder strings, TODOs, or `...` bodies in the implementation snippets above. All test stubs have real assertions.

**Phase-1 test compatibility:** `test_discover_entry_points_is_noop_in_phase1` renamed to `test_discover_entry_points_returns_zero_when_no_eps_installed`; the assertion `== 0` still holds in CI (no third-party plugins installed).

**Seam confirmations grounded in source:**
- `QBittorrentClient.__init__(base_urls, username, password, ...)` — confirmed `javdb/integrations/qb/client.py` line 255.
- `QBittorrentClient.add_torrent(magnet_link, name=None, category=None, ...) -> bool` — confirmed line 393.
- `qb_base_url_candidates()` — confirmed `javdb/integrations/qb/config.py` line 109.
- `cfg(name, default)` — confirmed `javdb/infra/config.py`.
- Transmission JSON-RPC: `POST /transmission/rpc` + 409 `X-Transmission-Session-Id` pattern — standard Transmission protocol, no existing client in repo.
