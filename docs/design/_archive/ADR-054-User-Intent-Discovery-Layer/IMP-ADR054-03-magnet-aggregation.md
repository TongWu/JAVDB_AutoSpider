# Multi-Source Magnet Aggregation (ADR-054 WS3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch + dedup + ADR-024-score magnets across two external indexers (JAVBUS + Sukebei) server-side, surfaced as extra `source`-tagged rows in the existing Browse/detail magnet table, gated by a `magnet_aggregation` capability flag. v1 is **ephemeral** — no D1 cache table.

**Architecture:** A net-new ADR-039 plugin **`indexer`** category (Domain Language working name `magnet-source`), modelled on the **notify fan-out** dispatcher (NOT the downloader single-select). Each source is a plugin (`IndexerPlugin` Protocol + `IndexerResult` dataclass) reading its own base-URL config and self-registering at import. A source-agnostic fetch helper reuses the `RequestHandler` proxy pool + curl_cffi impersonation but **bypasses the javdb-specific guards** (`use_cf_bypass=False`, no `is_ban_page`/over18). An aggregator dedups on normalized BitTorrent info-hash (primary) + normalized `video_code` (secondary grouping) and runs ADR-024 `score_torrent` **live** per surviving magnet (file-list signals degrade to `probe_unavailable`). A new sibling endpoint `POST /api/explore/aggregate-magnets` does the real work in Python; the Worker mirrors the route but **501s in cloudflare mode** (exactly like `/download-magnet`), preserving `/resolve` byte-parity. The `magnet_aggregation` capability flag is `bool(MAGNET_SOURCES)` on Python and **hardcoded `false`** on the Worker (capability-honest, mirroring index-status `has_uncensored=false`). The frontend calls aggregate only when the flag is on and renders a gated **Source** column in `ResolveMagnetTable.vue`.

**Tech Stack:** Python 3 / FastAPI / pydantic / pytest, importlib.metadata entry points (ADR-039), curl_cffi + proxy pool (`RequestHandler`), ADR-024 `score_torrent`/`extract_file_features`, TypeScript / Hono / `@cloudflare/workers-types` / Vitest (`cloudflare:test`), Vue 3 `<script setup>` / Naive UI / vue-i18n.

**Cross-repo note.** Two git repos are involved:
- **[MAIN]** = `/Users/tedwu/JAVDB_AutoSpider_CICD` — indexer plugins, Python fetch/aggregate, router/service, capability, `openapi.json`, `config.py.example`, Python tests. This IMP and ADR-054 live here. Run `git` with `git -C /Users/tedwu/JAVDB_AutoSpider_CICD ...`.
- **[WEB]** = the `javdb-autospider-web` working directory (current cwd) — TS Worker 501 mirror, capability flag, `ParsedMagnet`/`MagnetRow` shapes, the Browse Source column, i18n, `src/types/api.gen.ts`, Worker tests. Run `git` from cwd.

Work the phases in order: **A (indexer plugin category + dedup/score)** → **B (Python API surface)** → **C (TS Worker 501 mirror)** → **D (frontend)**. Each repo should be on its own feature branch.

---

## Design decisions locked by ADR-054 WS3-D1..D5 (do not re-litigate)

- **WS3-D1 — ephemeral v1, no D1 table.** Fetch + dedup + score on each request, per-source timeout + per-source failure isolation. The cache table `MagnetSourceResult` (keyed `(info_hash, video_code, source)` + `fetched_at` TTL) is the roadmap-blessed **deferred** follow-up — do **not** add a migration in this IMP.
- **WS3-D2 — single `magnet_aggregation` flag, backend-asymmetric.** Python: `bool(MAGNET_SOURCES)` (config-presence, no probe table in v1). Worker: hardcoded `false`. This reuses the existing index-status `has_uncensored=false` Python-only-capability precedent (`server/routes/explore.ts:263`).
- **WS3-D3 — exactly two plugins: JAVBUS + Sukebei.** Ship the `indexer` category + these two built-ins. BTdig/BTSOW are the deferred "more sources" follow-up. Two sources is the minimum that genuinely exercises cross-source infohash dedup.
- **WS3-D4 — sibling endpoint, not enriched `/resolve`.** `POST /api/explore/aggregate-magnets` (`{video_code}`). Python implements; Worker 501s in cloudflare mode. **Do NOT** fold external magnets into `/resolve` — its dual-backend payload must stay byte-parallel.
- **WS3-D5 — run ADR-024 `score_torrent` LIVE per magnet.** File-list-dependent signals degrade to `probe_unavailable`; name/tag/size signals drive the score. The stored-evaluation join is the deferred Option-B follow-up (needs the D1 cache table).
- **Fetch caveat.** Indexer hosts (JAVBUS/Sukebei) need the proxy pool + curl_cffi but `use_cf_bypass=False` and source-specific success validation — NOT the javdb `is_ban_page`/over18/Turnstile checks (which false-trigger on non-javdb hosts).
- **No `user_id`** (single-operator). Mutations stay `require_role("admin")` where they write; the aggregate endpoint is a read so it uses `_require_auth` like `/resolve`.

---

## File Structure

**[MAIN] create:**
- `javdb/integrations/indexer/__init__.py` — package marker.
- `javdb/integrations/indexer/plugin.py` — `IndexerPlugin` Protocol + `IndexerResult` + `IndexerMagnet` dataclasses.
- `javdb/integrations/indexer/dispatch.py` — `active_sources()` (reads `MAGNET_SOURCES` list) + `aggregate()` (fan-out + per-source isolation) + built-in self-registration + entry-point discovery.
- `javdb/integrations/indexer/fetch.py` — source-agnostic fetch helper (proxy pool + curl_cffi, `use_cf_bypass=False`, no javdb guards).
- `javdb/integrations/indexer/javbus/__init__.py`, `javdb/integrations/indexer/javbus/plugin.py` — JAVBUS built-in.
- `javdb/integrations/indexer/sukebei/__init__.py`, `javdb/integrations/indexer/sukebei/plugin.py` — Sukebei built-in.
- `javdb/integrations/indexer/aggregate.py` — `aggregate_magnets()` dedup + ADR-024 live-score merge.
- `apps/api/schemas/aggregate.py` — pydantic request/response models.
- `tests/unit/test_indexer_dispatch.py` — fan-out + isolation (clones `test_notify_dispatch`).
- `tests/unit/test_indexer_fetch.py` — fetch helper bypasses javdb CF/over18 path.
- `tests/unit/test_indexer_javbus.py`, `tests/unit/test_indexer_sukebei.py` — fixture-HTML parse + `is_configured()` honesty.
- `tests/unit/test_indexer_aggregate.py` — infohash dedup + cross-source merge + `probe_unavailable` (the headline).
- `tests/unit/test_aggregate_magnets_router.py` — router smoke + 422.
- `tests/fixtures/indexer/javbus_ABC-001.html`, `tests/fixtures/indexer/sukebei_ABC-001.html` — saved source HTML.

**[MAIN] modify:**
- `apps/api/routers/explore.py` — register `POST /api/explore/aggregate-magnets`.
- `apps/api/services/explore_service.py` — `aggregate_magnets_payload()`.
- `apps/api/routers/capabilities.py` — add `_magnet_aggregation_enabled()` + wire it.
- `apps/api/schemas/capabilities_payloads.py` — add `magnet_aggregation: bool` to `Features`.
- `config.py.example` — document `MAGNET_SOURCES` + per-source base-URL keys.
- `docs/api/openapi.json` — regenerated (mechanical).

**[WEB] create:**
- `server/__tests__/explore-aggregate.test.ts` — asserts the Worker route returns 501.

**[WEB] modify:**
- `server/routes/explore.ts` — mirror `/aggregate-magnets` route → 501 in cloudflare mode.
- `server/services/explore-parser.ts` — add `source?` + `quality_score?` to `ParsedMagnet`.
- `server/routes/capabilities.ts` — add `magnet_aggregation: false` to `features:`.
- `src/api/explore.ts` — add `apiAggregateMagnets(videoCode)`.
- `src/stores/browse.ts` — extend `MagnetRow` with `source` + `quality_score`; merge aggregated rows when the flag is on.
- `src/components/browse/ResolveMagnetTable.vue` — gated Source NTag column.
- `src/i18n/locales/en.json` + `src/i18n/locales/zh-CN.json` — new strings (en/zh parity).
- `src/types/api.gen.ts` — regenerated (mechanical) so `Features.magnet_aggregation` is typed.

---

## Phase A — Indexer plugin category [MAIN]

### Task 1: `indexer` category contract + dispatcher (TDD, fan-out)

**Files:**
- Create: `javdb/integrations/indexer/__init__.py`, `javdb/integrations/indexer/plugin.py`, `javdb/integrations/indexer/dispatch.py`
- Test: `tests/unit/test_indexer_dispatch.py`

The dispatcher clones the **notify fan-out** (`javdb/integrations/notify/dispatch.py`): `active_sources()` reads a **list** config `MAGNET_SOURCES` (like `active_names()` reads `NOTIFY_BACKENDS`), and `aggregate()` iterates every active source with per-source try/except isolation (like `send()`). It does **not** clone the downloader single-select.

- [x] **Step 1: Write the package marker**

Create `javdb/integrations/indexer/__init__.py`:

```python
"""ADR-039 `indexer` plugin category — multi-source magnet aggregation (ADR-054 WS3)."""
```

- [x] **Step 2: Write the contract**

Create `javdb/integrations/indexer/plugin.py` (mirrors `downloader/plugin.py:9-24` — a result dataclass + a `Protocol` with `name` + `is_configured()` + a domain method; adds an `IndexerMagnet` row dataclass since indexers return rows, not a single ok/detail):

```python
"""The indexer-category plugin contract (ADR-039 / ADR-054 WS3).

An indexer plugin searches ONE external source (JAVBUS, Sukebei, ...) for a
video code and returns the magnets it found. Mirrors the downloader contract
shape (a result dataclass + a Protocol) but is consumed via the notify-style
FAN-OUT dispatcher (every active source is queried), not single-select.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass
class IndexerMagnet:
    """A single magnet row from an external indexer.

    Field names mirror the TS ``ParsedMagnet`` shape (``magnet_uri``, not the
    javdb-internal ``href``) so the dual-backend Browse table folds the rows in
    without a key-rename. ``info_hash`` is the lowercased btih (set by the
    plugin via ``extract_hash_from_magnet``); ``source`` is the plugin name.
    External indexers expose no per-file list, so ``file_count`` is best-effort
    and ``info_hash`` may be ``None`` for a malformed/v2-only magnet.
    """

    magnet_uri: str
    name: str
    source: str
    info_hash: Optional[str] = None
    size: str = ""
    tags: List[str] = field(default_factory=list)
    file_count: int = 0


@dataclass
class IndexerResult:
    """The outcome of querying one source (per-source isolation envelope)."""

    source: str
    ok: bool
    magnets: List[IndexerMagnet] = field(default_factory=list)
    detail: Optional[str] = None


class IndexerPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def search(self, video_code: str) -> IndexerResult: ...
```

- [x] **Step 3: Write the failing dispatcher test**

Create `tests/unit/test_indexer_dispatch.py` (clones `tests/unit/test_notify_dispatch.py`: monkeypatch `cfg` + `REGISTRY`, assert list parsing + fan-out + failure isolation):

```python
"""Fan-out + isolation tests for the indexer dispatcher (ADR-054 WS3)."""

import javdb.integrations.indexer.dispatch as dispatch
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.plugins.registry import PluginRegistry


class _Plugin:
    def __init__(self, name, configured=True, raises=False, magnets=None):
        self.name = name
        self._configured = configured
        self._raises = raises
        self._magnets = magnets or []

    def is_configured(self):
        return self._configured

    def search(self, video_code):
        if self._raises:
            raise RuntimeError("boom")
        return IndexerResult(source=self.name, ok=True, magnets=self._magnets)


def _registry(*plugins):
    reg = PluginRegistry()
    for p in plugins:
        reg.register("indexer", p)
    return reg


def _mag(name, source):
    return IndexerMagnet(magnet_uri=f"magnet:?xt=urn:btih:{name}", name=name, source=source)


def test_active_sources_default_empty(monkeypatch):
    # Default empty list = feature OFF; no implicit fallback (unlike notify's email).
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: default)
    assert dispatch.active_sources() == []


def test_active_sources_csv_string(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: "javbus, sukebei")
    assert dispatch.active_sources() == ["javbus", "sukebei"]


def test_aggregate_fans_out_and_isolates_failure(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["javbus", "sukebei"])
    monkeypatch.setattr(
        dispatch, "REGISTRY",
        _registry(
            _Plugin("javbus", raises=True),
            _Plugin("sukebei", magnets=[_mag("ABC", "sukebei")]),
        ),
    )
    results = {r.source: r for r in dispatch.aggregate("ABC-001")}
    assert results["javbus"].ok is False          # isolated failure
    assert results["sukebei"].ok is True           # still delivered
    assert results["sukebei"].magnets[0].source == "sukebei"


def test_aggregate_skips_unconfigured(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["javbus"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_Plugin("javbus", configured=False)))
    results = dispatch.aggregate("ABC-001")
    assert results[0].ok is False
    assert "not configured" in (results[0].detail or "")


def test_aggregate_reports_not_registered(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["ghost"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry())
    results = dispatch.aggregate("ABC-001")
    assert results[0].ok is False
    assert "not registered" in (results[0].detail or "")


def test_active_sources_sanitizes_invalid_list_items(monkeypatch):
    monkeypatch.setattr(
        dispatch, "cfg",
        lambda name, default: ["javbus", {}, "", 123, " sukebei "],
    )
    assert dispatch.active_sources() == ["javbus", "sukebei"]
```

- [x] **Step 4: Run to verify it fails**

Run: `python3 -m pytest tests/unit/test_indexer_dispatch.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.integrations.indexer.dispatch'`

- [x] **Step 5: Implement the dispatcher**

Create `javdb/integrations/indexer/dispatch.py` (mirrors `notify/dispatch.py`: imports built-ins to self-register, discovers entry points, list-parses config, fan-out with per-source isolation). Note the **deliberate divergence** from notify: an empty `MAGNET_SOURCES` returns `[]` (feature off) — there is no implicit default source.

```python
"""Fan-out indexer dispatch with per-source failure isolation (ADR-039 / ADR-054 WS3).

Importing this module registers the built-in plugins (javbus, sukebei)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.indexer.plugin import IndexerResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.indexer.javbus.plugin  # noqa: F401,E402
import javdb.integrations.indexer.sukebei.plugin  # noqa: F401,E402

# Discover any third-party indexer plugins installed as entry points. The
# registry derives the category 'indexer' by stripping '_plugins' from the
# group name (registry.py:46). Returns 0 when none are installed.
REGISTRY.discover_entry_points("javdb.indexer_plugins")


def active_sources() -> list[str]:
    val = cfg("MAGNET_SOURCES", [])
    if isinstance(val, str):
        names = [v.strip() for v in val.split(",") if v.strip()]
    elif isinstance(val, (list, tuple)):
        # Keep only non-empty strings: a stray non-string element (config typo)
        # would otherwise reach the REGISTRY lookup and raise. Fan-out must never
        # crash on a config typo.
        names = [v.strip() for v in val if isinstance(v, str) and v.strip()]
    else:
        # A non-iterable / unexpected value degrades to empty (feature off).
        names = []
    # NOTE: deliberately NO ["email"]-style default. Empty == feature off.
    return names


def aggregate(video_code: str) -> list[IndexerResult]:
    """Query every active source for ``video_code`` with per-source isolation."""
    results: list[IndexerResult] = []
    for name in active_sources():
        plugin = REGISTRY.get("indexer", name)
        if plugin is None:
            results.append(IndexerResult(source=name, ok=False, detail="not registered"))
            continue
        try:
            if not plugin.is_configured():
                results.append(IndexerResult(source=name, ok=False, detail="not configured"))
                continue
            results.append(plugin.search(video_code))
        except Exception as exc:  # failure isolation (incl. is_configured errors)
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
    return results
```

> The Task-1 test monkeypatches `REGISTRY`/`cfg` and never imports a real source, but importing `dispatch` triggers the JAVBUS/Sukebei imports. Those plugin modules are created in Task 3 — if you are running TDD strictly task-by-task, stub the two plugin files as empty `pass` modules now (the imports just need to resolve) and fill them in Task 3, OR implement Task 3 plugins before re-running this test. The recommended order is: write `dispatch.py` + the two `plugin.py` skeletons (Task 3 Step 1), then run this test green.

- [x] **Step 6: Run to verify it passes**

Run: `python3 -m pytest tests/unit/test_indexer_dispatch.py -q`
Expected: PASS (6 passed)

(Commit together with Task 3.)

---

### Task 2: Source-agnostic fetch helper (TDD — no javdb guards)

**Files:**
- Create: `javdb/integrations/indexer/fetch.py`
- Test: `tests/unit/test_indexer_fetch.py`

The helper reuses the `RequestHandler` proxy pool + curl_cffi impersonation but bypasses the javdb-specific guards. Concretely it calls `handler.get_page(url, use_cf_bypass=False, module_name="indexer", ...)` — `use_cf_bypass=False` means the CF-bypass cascade (`_fetch_with_cf_bypass`, over18, Turnstile, `is_ban_page`) is never entered (see `request.py:1117-1190`). It builds the handler via `create_request_handler_from_config` exactly as `explore_service._new_request_handler` does, but **never** passes the javdb session cookie (those hosts don't use it).

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_indexer_fetch.py`:

```python
"""The indexer fetch helper must reuse proxy/curl_cffi but skip javdb guards (ADR-054 WS3)."""

from javdb.integrations.indexer import fetch as indexer_fetch


class _FakeHandler:
    def __init__(self):
        self.calls = []

    def get_page(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return "<html>ok</html>"


def test_fetch_disables_cf_bypass_and_uses_indexer_module(monkeypatch):
    fake = _FakeHandler()
    monkeypatch.setattr(indexer_fetch, "_handler", lambda config: fake)
    html = indexer_fetch.fetch_source_html(
        "https://www.javbus.com/ABC-001", {"PROXY_POOL": []}, use_proxy=True
    )
    assert html == "<html>ok</html>"
    (url, kwargs) = fake.calls[0]
    assert url == "https://www.javbus.com/ABC-001"
    # The javdb CF-bypass / over18 / ban-page cascade must NOT be engaged.
    assert kwargs["use_cf_bypass"] is False
    # module_name is 'indexer', not 'spider' (so javdb proxy-module logic is not assumed).
    assert kwargs["module_name"] == "indexer"


def test_fetch_returns_none_on_empty(monkeypatch):
    class _Empty(_FakeHandler):
        def get_page(self, url, **kwargs):
            return None

    monkeypatch.setattr(indexer_fetch, "_handler", lambda config: _Empty())
    assert indexer_fetch.fetch_source_html("https://x", {}, use_proxy=False) is None
```

- [x] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/unit/test_indexer_fetch.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.integrations.indexer.fetch'`

- [x] **Step 3: Implement the fetch helper**

Create `javdb/integrations/indexer/fetch.py`:

```python
"""Source-agnostic HTML fetch for external indexers (ADR-054 WS3).

Reuses the RequestHandler proxy pool + curl_cffi TLS impersonation, but
DELIBERATELY bypasses the javdb-specific anti-bot path:
  * ``use_cf_bypass=False`` — the CF-bypass cascade (over18 / Turnstile /
    ``is_ban_page``) in request.py:1117+ is javdb-centric and false-triggers on
    non-javdb hosts, so it is never entered.
  * ``module_name='indexer'`` — not 'spider'; we don't assume the javdb
    PROXY_MODULES semantics.
Source-specific success validation lives in each plugin's parser, not here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from javdb.infra.request import create_request_handler_from_config
from javdb.proxy.pool import create_proxy_pool_from_config


def _proxy_pool(config: Dict[str, Any]):
    raw = config.get("PROXY_POOL", [])
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return create_proxy_pool_from_config(
            raw, max_failures=int(config.get("PROXY_POOL_MAX_FAILURES", 3) or 3)
        )
    except Exception:
        return None


def _handler(config: Dict[str, Any]):
    # Mirrors explore_service._new_request_handler, MINUS the javdb session
    # cookie (external indexers do not use _jdb_session). 'indexer' is added to
    # proxy_modules so should_proxy_module honours the proxy pool for these hosts.
    return create_request_handler_from_config(
        proxy_pool=_proxy_pool(config),
        cf_bypass_enabled=False,
        proxy_http=config.get("PROXY_HTTP"),
        proxy_https=config.get("PROXY_HTTPS"),
        proxy_modules=config.get("PROXY_MODULES", ["spider"]) or ["spider"],
        proxy_mode=str(config.get("PROXY_MODE", "pool")),
    )


def fetch_source_html(
    url: str,
    config: Dict[str, Any],
    *,
    use_proxy: bool = True,
    max_retries: int = 2,
) -> Optional[str]:
    """Fetch an external-indexer page. Returns HTML or ``None`` on failure.

    Never raises the javdb CF/over18 path — ``use_cf_bypass=False``.
    """
    handler = _handler(config)
    return handler.get_page(
        url,
        use_proxy=use_proxy,
        use_cookie=False,
        module_name="indexer",
        max_retries=max_retries,
        use_cf_bypass=False,
    )
```

- [x] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/unit/test_indexer_fetch.py -q`
Expected: PASS (2 passed)

(Commit together with Task 3.)

---

### Task 3: JAVBUS + Sukebei plugins (TDD, fixture HTML)

**Files:**
- Create: `javdb/integrations/indexer/javbus/__init__.py`, `javdb/integrations/indexer/javbus/plugin.py`
- Create: `javdb/integrations/indexer/sukebei/__init__.py`, `javdb/integrations/indexer/sukebei/plugin.py`
- Create: `tests/fixtures/indexer/javbus_ABC-001.html`, `tests/fixtures/indexer/sukebei_ABC-001.html`
- Test: `tests/unit/test_indexer_javbus.py`, `tests/unit/test_indexer_sukebei.py`

Each plugin reads its own base-URL config, fetches via the Task-2 helper, parses its source HTML into `IndexerMagnet` rows (computing `info_hash` via the existing `extract_hash_from_magnet` in `javdb/integrations/qb/client.py:552`), and ends with `REGISTRY.register("indexer", XPlugin())` at import. To keep parsing pure and testable, factor a module-level `parse(html, base_url) -> list[IndexerMagnet]` that the test drives directly with a saved fixture, while `search()` wires fetch → parse.

- [x] **Step 1: Save fixture HTML**

Save a representative search-result page (or hand-trim a minimal one) for each source. The fixtures must contain the structure the parser keys on:
- `tests/fixtures/indexer/javbus_ABC-001.html` — a JAVBUS movie page whose magnet table rows carry `a[href^="magnet:"]` anchors with a name cell, a size cell, and tag spans (e.g. 高清/字幕).
- `tests/fixtures/indexer/sukebei_ABC-001.html` — a Sukebei (`sukebei.nyaa.si`) search-result `<table>` whose rows carry a title link and a `a[href^="magnet:"]` download anchor plus a size column.

> Keep fixtures SMALL and synthetic — do not commit a full live capture. Two or three magnet rows each is enough to exercise dedup. Anonymise any real codes.

- [x] **Step 2: Write the JAVBUS plugin skeleton (so `dispatch.py` imports resolve)**

Create `javdb/integrations/indexer/javbus/__init__.py`:

```python
"""JAVBUS indexer plugin package (ADR-054 WS3)."""
```

Create `javdb/integrations/indexer/javbus/plugin.py` with the full implementation:

```python
"""JAVBUS indexer plugin (ADR-054 WS3).

JAVBUS is code-keyed and javdb-adjacent, so dedup-by-video_code works cleanly.
Reads JAVBUS_BASE_URL; default https://www.javbus.com.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from javdb.infra.config import cfg
from javdb.integrations.indexer.fetch import fetch_source_html
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.plugins.registry import REGISTRY
from javdb.integrations.qb.client import extract_hash_from_magnet

_DEFAULT_BASE = "https://www.javbus.com"


def parse(html: str, base_url: str) -> List[IndexerMagnet]:
    soup = BeautifulSoup(html, "html.parser")
    magnets: List[IndexerMagnet] = []
    for anchor in soup.select('a[href^="magnet:"]'):
        uri = (anchor.get("href") or "").strip()
        if not uri:
            continue
        # Name + size: JAVBUS lays magnet metadata across sibling cells; fall
        # back to the anchor text. Keep tolerant — source HTML drifts.
        row = anchor.find_parent("tr") or anchor.parent
        cells = row.find_all("td") if row else []
        name = (cells[0].get_text(strip=True) if cells else anchor.get_text(strip=True)) or uri
        size = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        tags = [t.get_text(strip=True) for t in row.select("span.label, .tag")] if row else []
        magnets.append(
            IndexerMagnet(
                magnet_uri=uri,
                name=name,
                source="javbus",
                info_hash=extract_hash_from_magnet(uri),
                size=size,
                tags=[t for t in tags if t],
            )
        )
    return magnets


class JavbusIndexerPlugin:
    name = "javbus"

    def _base_url(self) -> str:
        return str(cfg("JAVBUS_BASE_URL", _DEFAULT_BASE) or _DEFAULT_BASE).rstrip("/")

    def is_configured(self) -> bool:
        # A base URL is always derivable (default constant), so JAVBUS is
        # "configured" whenever it is named in MAGNET_SOURCES.
        return bool(self._base_url())

    def search(self, video_code: str) -> IndexerResult:
        base = self._base_url()
        url = urljoin(base + "/", video_code)
        config = _runtime_config()
        html = fetch_source_html(url, config, use_proxy=bool(config.get("MAGNET_SOURCES_USE_PROXY", True)))
        if not html:
            return IndexerResult(source=self.name, ok=False, detail="empty response")
        return IndexerResult(source=self.name, ok=True, magnets=parse(html, base))


def _runtime_config() -> dict:
    # Lazy import avoids a hard apps.api dependency at module import time;
    # falls back to an empty config (no proxy pool) outside the API process.
    try:
        from apps.api.services import config_service
        return config_service.load_runtime_config()
    except Exception:
        return {}


REGISTRY.register("indexer", JavbusIndexerPlugin())
```

- [x] **Step 3: Write the Sukebei plugin**

Create `javdb/integrations/indexer/sukebei/__init__.py`:

```python
"""Sukebei indexer plugin package (ADR-054 WS3)."""
```

Create `javdb/integrations/indexer/sukebei/plugin.py`:

```python
"""Sukebei (sukebei.nyaa.si) indexer plugin (ADR-054 WS3).

A real torrent indexer — different HTML + CF profile from JAVBUS, which is the
point: two sources validate the source-agnostic fetch helper and cross-source
infohash dedup. Reads SUKEBEI_BASE_URL; default https://sukebei.nyaa.si.
"""

from __future__ import annotations

from typing import List
from urllib.parse import quote

from bs4 import BeautifulSoup

from javdb.infra.config import cfg
from javdb.integrations.indexer.fetch import fetch_source_html
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.plugins.registry import REGISTRY
from javdb.integrations.qb.client import extract_hash_from_magnet

_DEFAULT_BASE = "https://sukebei.nyaa.si"


def parse(html: str, base_url: str) -> List[IndexerMagnet]:
    soup = BeautifulSoup(html, "html.parser")
    magnets: List[IndexerMagnet] = []
    for row in soup.select("table.torrent-list tbody tr, table tbody tr"):
        anchor = row.select_one('a[href^="magnet:"]')
        if anchor is None:
            continue
        uri = (anchor.get("href") or "").strip()
        if not uri:
            continue
        title_link = row.select_one('a[href^="/view/"], a[title]')
        name = (title_link.get_text(strip=True) if title_link else "") or uri
        # Sukebei size lives in a td; pick the cell that looks like a size.
        size = ""
        for td in row.find_all("td"):
            text = td.get_text(strip=True)
            if any(u in text for u in ("GiB", "MiB", "GB", "MB", "KiB")):
                size = text
                break
        magnets.append(
            IndexerMagnet(
                magnet_uri=uri,
                name=name,
                source="sukebei",
                info_hash=extract_hash_from_magnet(uri),
                size=size,
            )
        )
    return magnets


class SukebeiIndexerPlugin:
    name = "sukebei"

    def _base_url(self) -> str:
        return str(cfg("SUKEBEI_BASE_URL", _DEFAULT_BASE) or _DEFAULT_BASE).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._base_url())

    def search(self, video_code: str) -> IndexerResult:
        base = self._base_url()
        url = f"{base}/?q={quote(video_code)}"
        config = _runtime_config()
        html = fetch_source_html(url, config, use_proxy=bool(config.get("MAGNET_SOURCES_USE_PROXY", True)))
        if not html:
            return IndexerResult(source=self.name, ok=False, detail="empty response")
        return IndexerResult(source=self.name, ok=True, magnets=parse(html, base))


def _runtime_config() -> dict:
    try:
        from apps.api.services import config_service
        return config_service.load_runtime_config()
    except Exception:
        return {}


REGISTRY.register("indexer", SukebeiIndexerPlugin())
```

- [x] **Step 4: Write the parser tests**

Create `tests/unit/test_indexer_javbus.py`:

```python
"""JAVBUS plugin parse + is_configured honesty (ADR-054 WS3)."""

import pathlib

from javdb.integrations.indexer.javbus.plugin import JavbusIndexerPlugin, parse

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures/indexer/javbus_ABC-001.html"
).read_text(encoding="utf-8")


def test_parse_extracts_magnets_with_infohash():
    magnets = parse(_FIXTURE, "https://www.javbus.com")
    assert magnets, "fixture should yield at least one magnet"
    first = magnets[0]
    assert first.magnet_uri.startswith("magnet:")
    assert first.source == "javbus"
    assert first.info_hash is None or len(first.info_hash) == 40


def test_is_configured_true_with_default_base():
    assert JavbusIndexerPlugin().is_configured() is True
```

Create `tests/unit/test_indexer_sukebei.py` (same shape, `source == "sukebei"`, fixture `sukebei_ABC-001.html`).

- [x] **Step 5: Run the parser tests + the (now-importable) dispatcher test**

Run: `python3 -m pytest tests/unit/test_indexer_javbus.py tests/unit/test_indexer_sukebei.py tests/unit/test_indexer_dispatch.py tests/unit/test_indexer_fetch.py -q`
Expected: PASS (all). Adjust the `parse()` CSS selectors to match whatever real structure you saved in the fixtures — the fixtures are the contract here.

- [x] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/integrations/indexer tests/unit/test_indexer_dispatch.py tests/unit/test_indexer_fetch.py tests/unit/test_indexer_javbus.py tests/unit/test_indexer_sukebei.py tests/fixtures/indexer
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(indexer): add indexer plugin category + JAVBUS/Sukebei sources (ADR-054 WS3)"
```

---

### Task 4: Dedup + ADR-024 quality-merge (TDD — the headline)

**Files:**
- Create: `javdb/integrations/indexer/aggregate.py`
- Test: `tests/unit/test_indexer_aggregate.py`

`aggregate_magnets()` collects rows from `dispatch.aggregate()`, dedups on **normalized info_hash** (primary) + normalized `video_code` (secondary grouping), and runs ADR-024 `score_torrent` **live** per surviving magnet. Because external indexers expose no file list, it feeds `extract_file_features([])` so all file-list signals degrade — `score_torrent` returns `main_video_missing` etc. — and the aggregator stamps a `probe_unavailable` reason so the degradation is explicit (the reason exists in the ADR-024 canon for exactly this case). Among same-infohash cross-source dupes it keeps the higher ADR-024 score and records every `source` that carried it.

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_indexer_aggregate.py`:

```python
"""Dedup + ADR-024 live-score merge across sources (ADR-054 WS3 headline)."""

from javdb.integrations.indexer import aggregate as agg
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult

_HASH_A = "a" * 40
_HASH_B = "b" * 40


def _mag(info_hash, source, name="ABC-001 中字", size="4.2GB"):
    return IndexerMagnet(
        magnet_uri=f"magnet:?xt=urn:btih:{info_hash}",
        name=name,
        source=source,
        info_hash=info_hash,
        size=size,
    )


def test_same_infohash_across_sources_merges_to_one_row(monkeypatch):
    monkeypatch.setattr(
        agg, "_collect",
        lambda code: [
            IndexerResult(source="javbus", ok=True, magnets=[_mag(_HASH_A, "javbus")]),
            IndexerResult(source="sukebei", ok=True, magnets=[_mag(_HASH_A.upper(), "sukebei")]),
        ],
    )
    rows = agg.aggregate_magnets("ABC-001")
    assert len(rows) == 1                                  # deduped by normalized infohash
    assert sorted(rows[0]["sources"]) == ["javbus", "sukebei"]
    assert rows[0]["info_hash"] == _HASH_A                 # normalized lowercase


def test_distinct_infohashes_survive(monkeypatch):
    monkeypatch.setattr(
        agg, "_collect",
        lambda code: [
            IndexerResult(source="javbus", ok=True, magnets=[_mag(_HASH_A, "javbus")]),
            IndexerResult(source="sukebei", ok=True, magnets=[_mag(_HASH_B, "sukebei")]),
        ],
    )
    rows = agg.aggregate_magnets("ABC-001")
    assert len(rows) == 2


def test_live_score_marks_probe_unavailable(monkeypatch):
    monkeypatch.setattr(
        agg, "_collect",
        lambda code: [IndexerResult(source="javbus", ok=True, magnets=[_mag(_HASH_A, "javbus")])],
    )
    rows = agg.aggregate_magnets("ABC-001")
    assert "quality_score" in rows[0]
    assert "probe_unavailable" in rows[0]["quality_reasons"]


def test_malformed_magnet_falls_back_to_video_code_group(monkeypatch):
    # Two magnets with NO parseable infohash but the same code group dedup by code.
    bad1 = IndexerMagnet(magnet_uri="magnet:?dn=x", name="ABC-001", source="javbus", info_hash=None)
    bad2 = IndexerMagnet(magnet_uri="magnet:?dn=y", name="ABC-001", source="sukebei", info_hash=None)
    monkeypatch.setattr(
        agg, "_collect",
        lambda code: [
            IndexerResult(source="javbus", ok=True, magnets=[bad1]),
            IndexerResult(source="sukebei", ok=True, magnets=[bad2]),
        ],
    )
    rows = agg.aggregate_magnets("ABC-001")
    # Same code group, no infohash → collapsed to a single fallback row.
    assert len(rows) == 1
    assert sorted(rows[0]["sources"]) == ["javbus", "sukebei"]
```

- [x] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/unit/test_indexer_aggregate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.integrations.indexer.aggregate'`

- [x] **Step 3: Implement the aggregator**

Create `javdb/integrations/indexer/aggregate.py`:

```python
"""Cross-source dedup + ADR-024 live quality-merge (ADR-054 WS3).

Dedup key: normalized info_hash (primary). Magnets without a parseable btih
fall back to a secondary grouping by normalized video_code so malformed/v2-only
magnets do not each become their own row. ADR-024 score_torrent runs LIVE with
an empty file list, so file-list signals degrade and a 'probe_unavailable'
reason is stamped (the reason code exists in the ADR-024 canon for this case).
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List

from javdb.integrations.indexer import dispatch
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.quality.features import extract_file_features
from javdb.quality.scoring import score_torrent

_PROBE_UNAVAILABLE = "probe_unavailable"


def _normalise_hash(info_hash: str | None) -> str | None:
    if not info_hash:
        return None
    return info_hash.strip().lower() or None


def _normalise_code(code: str | None) -> str:
    # Same NFKC-fold+strip+upper as spider dedup (_normalise_code) for parity.
    if not code:
        return ""
    return unicodedata.normalize("NFKC", code).strip().upper()


def _collect(video_code: str) -> List[IndexerResult]:
    # Seam for tests: returns the per-source results from the fan-out dispatcher.
    return dispatch.aggregate(video_code)


def _live_score(magnet: IndexerMagnet) -> Dict[str, Any]:
    # External indexers expose no file list → empty features → file signals
    # degrade. We still get the name/tag/size-derived signals from score_torrent.
    features = extract_file_features([])
    result = score_torrent(
        features,
        {"javdb_category": "", "magnet_name": magnet.name, "javdb_tags": magnet.tags},
    )
    reasons = list(result.get("reasons", []))
    if _PROBE_UNAVAILABLE not in reasons:
        reasons.append(_PROBE_UNAVAILABLE)
    return {"score": result.get("score", 0.0), "reasons": reasons}


def _row(magnet: IndexerMagnet, sources: List[str], scored: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "magnet_uri": magnet.magnet_uri,
        "name": magnet.name,
        "size": magnet.size,
        "tags": magnet.tags,
        "file_count": magnet.file_count,
        "info_hash": _normalise_hash(magnet.info_hash),
        "sources": sorted(set(sources)),
        "quality_score": scored["score"],
        "quality_reasons": scored["reasons"],
    }


def aggregate_magnets(video_code: str) -> List[Dict[str, Any]]:
    """Return deduped, ADR-024-scored magnet rows across all active sources."""
    by_hash: Dict[str, Dict[str, Any]] = {}
    by_code: Dict[str, Dict[str, Any]] = {}  # fallback group for hash-less magnets
    code_group = _normalise_code(video_code)

    for res in _collect(video_code):
        if not res.ok:
            continue
        for magnet in res.magnets:
            scored = _live_score(magnet)
            norm = _normalise_hash(magnet.info_hash)
            if norm is not None:
                existing = by_hash.get(norm)
                if existing is None:
                    by_hash[norm] = _row(magnet, [magnet.source], scored)
                else:
                    existing["sources"] = sorted(set(existing["sources"]) | {magnet.source})
                    # Keep the higher ADR-024 score among cross-source dupes.
                    if scored["score"] > existing["quality_score"]:
                        existing["quality_score"] = scored["score"]
                        existing["quality_reasons"] = scored["reasons"]
            else:
                key = code_group or magnet.name.strip().upper()
                existing = by_code.get(key)
                if existing is None:
                    by_code[key] = _row(magnet, [magnet.source], scored)
                else:
                    existing["sources"] = sorted(set(existing["sources"]) | {magnet.source})

    rows = list(by_hash.values()) + list(by_code.values())
    rows.sort(key=lambda r: r["quality_score"], reverse=True)
    return rows
```

- [x] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/unit/test_indexer_aggregate.py -q`
Expected: PASS (4 passed)

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/integrations/indexer/aggregate.py tests/unit/test_indexer_aggregate.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(indexer): infohash dedup + ADR-024 live quality-merge (ADR-054 WS3)"
```

---

## Phase B — Python API surface [MAIN]

### Task 5: `POST /api/explore/aggregate-magnets` (TDD)

**Files:**
- Create: `apps/api/schemas/aggregate.py`
- Modify: `apps/api/routers/explore.py`, `apps/api/services/explore_service.py`
- Test: `tests/unit/test_aggregate_magnets_router.py`

The endpoint accepts `{video_code}` (a code, not a javdb URL — aggregation is code-keyed) and returns `{video_code, magnets: [...]}`. It is a **read** (no side effects), so it uses `_require_auth` like `/resolve`, not `require_role("admin")`. The service delegates to `aggregate.aggregate_magnets`.

- [x] **Step 1: Write the schemas**

Create `apps/api/schemas/aggregate.py` (the response is permissive-extra like the other explore responses so fields can grow):

```python
"""Pydantic schemas for the aggregate-magnets endpoint (ADR-054 WS3)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AggregateMagnetsPayload(BaseModel):
    video_code: str = Field(..., min_length=1, max_length=64)


class AggregatedMagnet(BaseModel):
    magnet_uri: str
    name: str
    size: str = ""
    tags: List[str] = []
    file_count: int = 0
    info_hash: Optional[str] = None
    sources: List[str] = []
    quality_score: float = 0.0
    quality_reasons: List[str] = []


class AggregateMagnetsResponse(BaseModel):
    video_code: str
    magnets: List[AggregatedMagnet]
```

- [x] **Step 2: Add the service function**

In `apps/api/services/explore_service.py`, add the import near the top (alongside the other `javdb.*` imports):

```python
from javdb.integrations.indexer.aggregate import aggregate_magnets
```

Then add this coroutine near `resolve_payload` (it is sync work, so offload to a thread to avoid blocking the event loop, mirroring how `index_status_payload` uses `asyncio.to_thread`):

```python
async def aggregate_magnets_payload(payload: Any, username: str) -> Dict[str, Any]:
    code = str(payload.video_code).strip()
    rows = await asyncio.to_thread(aggregate_magnets, code)
    context.audit_logger.info(
        "explore_aggregate_magnets username=%s code=%s count=%s",
        username,
        code,
        len(rows),
    )
    return {"video_code": code, "magnets": rows}
```

Add `"aggregate_magnets_payload",` to the `__all__` list.

- [x] **Step 3: Register the route**

In `apps/api/routers/explore.py`, add the schema import:

```python
from apps.api.schemas.aggregate import (
    AggregateMagnetsPayload,
    AggregateMagnetsResponse,
)
```

Then add the route after `explore_resolve` (a read → `_require_auth`, like `/resolve`):

```python
@router.post("/aggregate-magnets", response_model=AggregateMagnetsResponse)
async def explore_aggregate_magnets(
    payload: AggregateMagnetsPayload,
    current=Depends(_require_auth),
):
    return await explore_service.aggregate_magnets_payload(payload, current["sub"])
```

Add `"explore_aggregate_magnets",` to `__all__`.

- [x] **Step 4: Write the router test**

Create `tests/unit/test_aggregate_magnets_router.py` (monkeypatch the aggregator so the test has no network; override `_require_auth` like the WS1 watchlist router test):

```python
"""Smoke tests for the aggregate-magnets router (ADR-054 WS3)."""

import pytest
from fastapi.testclient import TestClient

from apps.api.infra.auth import _require_auth
from apps.api.services import explore_service


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        explore_service,
        "aggregate_magnets",
        lambda code: [
            {
                "magnet_uri": "magnet:?xt=urn:btih:" + "a" * 40,
                "name": code + " 中字",
                "size": "4.2GB",
                "tags": ["中字"],
                "file_count": 1,
                "info_hash": "a" * 40,
                "sources": ["javbus", "sukebei"],
                "quality_score": 0.55,
                "quality_reasons": ["main_video_missing", "probe_unavailable"],
            }
        ],
    )
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = lambda: {"sub": "test"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(_require_auth, None)


def test_aggregate_returns_merged_rows(client):
    res = client.post("/api/explore/aggregate-magnets", json={"video_code": "ABC-001"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["video_code"] == "ABC-001"
    assert len(body["magnets"]) == 1
    assert sorted(body["magnets"][0]["sources"]) == ["javbus", "sukebei"]
    assert "probe_unavailable" in body["magnets"][0]["quality_reasons"]


def test_aggregate_rejects_empty_code(client):
    res = client.post("/api/explore/aggregate-magnets", json={"video_code": ""})
    assert res.status_code == 422
```

> Monkeypatching `explore_service.aggregate_magnets` works because Step 2 imported the symbol into that module's namespace (`from ... import aggregate_magnets`), so the service function resolves the patched reference.

- [x] **Step 5: Run the test**

Run: `python3 -m pytest tests/unit/test_aggregate_magnets_router.py -q`
Expected: PASS (2 passed)

- [x] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/schemas/aggregate.py apps/api/routers/explore.py apps/api/services/explore_service.py tests/unit/test_aggregate_magnets_router.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): add /api/explore/aggregate-magnets (ADR-054 WS3)"
```

---

### Task 6: `magnet_aggregation` capability flag (Python)

**Files:**
- Modify: `apps/api/routers/capabilities.py`, `apps/api/schemas/capabilities_payloads.py`

Unlike the table-probe flags (`closed_loop`, `watch_intent`), v1 has no table, so the flag is **config-presence**: `bool(active_sources())`. Reuse the dispatcher's `active_sources()` so the flag is honest about which config actually drives aggregation.

- [x] **Step 1: Add the field to the `Features` schema**

In `apps/api/schemas/capabilities_payloads.py`, add `magnet_aggregation: bool` to `class Features(BaseModel)` immediately after `watch_intent: bool`:

```python
    library_consumption: bool
    watch_intent: bool
    magnet_aggregation: bool
    site_drift_sentinel: bool
```

- [x] **Step 2: Add the probe + wire it**

In `apps/api/routers/capabilities.py`, add this after `_watch_intent_enabled()` (config-presence, not a table probe):

```python
def _magnet_aggregation_enabled() -> bool:
    """True when at least one indexer source is configured (ADR-054 WS3, capability honesty).

    v1 is ephemeral (no cache table) so there is nothing to probe; the flag is
    config-presence — MAGNET_SOURCES non-empty — via the dispatcher's parser.
    """
    try:
        from javdb.integrations.indexer.dispatch import active_sources
        return bool(active_sources())
    except Exception:
        return False
```

Then in `build_capabilities()`, add `magnet_aggregation=_magnet_aggregation_enabled(),` to the `Features(...)` call, immediately after `watch_intent=_watch_intent_enabled(),`.

- [x] **Step 3: Verify capabilities builds and exposes the flag**

Run: `python3 -c "from apps.api.routers.capabilities import build_capabilities; print(build_capabilities().features.magnet_aggregation)"`
Expected: prints `False` (no `MAGNET_SOURCES` configured on this path) — proves the field exists and degrades gracefully.

- [x] **Step 4: Document the config keys**

In `config.py.example`, add a new block after the DOWNLOADER BACKENDS section (mirror the NOTIFY_BACKENDS doc style):

```python
# =============================================================================
# MAGNET SOURCES / INDEXERS (ADR-054 WS3)
# =============================================================================

# Active external magnet indexers, queried in a fan-out (every active source).
# Default empty = feature OFF (no aggregation, capability flag false). Accepts a
# list or a CSV string, e.g. ['javbus', 'sukebei']. Aggregation runs server-side
# in the Python backend only; the Cloudflare Worker cannot reach external hosts.
MAGNET_SOURCES = []

# Per-source base URLs (override only if you use a mirror).
JAVBUS_BASE_URL = 'https://www.javbus.com'
SUKEBEI_BASE_URL = 'https://sukebei.nyaa.si'

# Whether indexer fetches go through the proxy pool (recommended for ban safety).
MAGNET_SOURCES_USE_PROXY = True
```

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/routers/capabilities.py apps/api/schemas/capabilities_payloads.py config.py.example
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): expose magnet_aggregation capability flag (ADR-054 WS3)"
```

---

### Task 7: Regenerate the OpenAPI contract

**Files:**
- Modify: `docs/api/openapi.json`

- [x] **Step 1: Dump the OpenAPI schema**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD && python3 -m apps.cli.ops.dump_openapi`
Expected: `wrote /Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json (<N> bytes)`

- [x] **Step 2: Verify the new surface is in the contract**

Run: `python3 -c "import json; d=json.load(open('docs/api/openapi.json')); print('/api/explore/aggregate-magnets' in d['paths']); print('magnet_aggregation' in d['components']['schemas']['Features']['properties'])"`
Expected: prints `True` then `True`

- [x] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/api/openapi.json
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "chore(api): re-vendor openapi.json for aggregate-magnets (ADR-054 WS3)"
```

---

## Phase C — TS Worker mirror [WEB]

### Task 8: Worker `aggregate-magnets` route = 501 (parity)

**Files:**
- Modify: `server/routes/explore.ts`, `server/services/explore-parser.ts`
- Test: `server/__tests__/explore-aggregate.test.ts`

The Worker cannot reach/clear external indexers (no proxy pool / curl_cffi), so it mirrors the route but returns 501 in cloudflare mode — cloning the existing `/download-magnet` 501 at `server/routes/explore.ts:201`. This preserves the dual-backend route surface without advertising a capability the Worker cannot deliver.

- [x] **Step 1: Write the failing route test**

Create `server/__tests__/explore-aggregate.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function login() {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as Record<string, unknown>;
  return { accessToken: data.access_token as string };
}

describe("Explore aggregate-magnets (Worker mirror)", () => {
  it("returns 501 in cloudflare mode", async () => {
    const { accessToken } = await login();
    const res = await app.request(
      "/api/explore/aggregate-magnets",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ video_code: "ABC-001" }),
      },
      env,
    );
    expect(res.status).toBe(501);
  });
});
```

- [x] **Step 2: Run to verify it fails**

Run: `npx vitest run server/__tests__/explore-aggregate.test.ts --config vitest.server.config.ts`
Expected: FAIL (route 404s — not yet mirrored).

- [x] **Step 3: Add the 501 route**

In `server/routes/explore.ts`, add this route (place it after the `/resolve` route, before `/download-magnet`; a read so no `requireRole`, matching the Python `_require_auth`):

```typescript
// --- aggregate-magnets (ADR-054 WS3) ---
// Multi-source magnet aggregation runs server-side in the Python backend only.
// The Worker cannot reach/clear external indexers (no proxy pool / curl_cffi),
// so it mirrors the route surface but 501s — exactly like /download-magnet.
exploreRoutes.post("/aggregate-magnets", async (c) => {
  const body = await c.req.json<{ video_code?: string }>();
  if (!body.video_code || body.video_code.length > 64) {
    throw new HTTPException(422, { message: "video_code required (max 64 chars)" });
  }
  throw new HTTPException(501, {
    message: "Multi-source magnet aggregation unavailable in Cloudflare mode (Python backend only).",
  });
});
```

- [x] **Step 4: Extend `ParsedMagnet`**

In `server/services/explore-parser.ts`, add the optional fields to `ParsedMagnet` (so a Python-sourced aggregated magnet folded into the shared shape type-checks; optional → existing `/resolve` parse is unchanged):

```typescript
export interface ParsedMagnet {
  name: string;
  magnet_uri: string;
  size: string;
  tags: string[];
  file_count: number;
  source?: string;
  quality_score?: number;
}
```

- [x] **Step 5: Run the test to verify it passes**

Run: `npx vitest run server/__tests__/explore-aggregate.test.ts --config vitest.server.config.ts`
Expected: PASS (1 passed)

- [x] **Step 6: Type-check**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Expected: no errors referencing `explore.ts` / `explore-parser.ts`.

- [x] **Step 7: Commit**

```bash
git add server/routes/explore.ts server/services/explore-parser.ts server/__tests__/explore-aggregate.test.ts
git commit -m "feat(server): mirror aggregate-magnets route as 501 (ADR-054 WS3)"
```

---

### Task 9: `magnet_aggregation` capability probe (TS, hardcoded false)

**Files:**
- Modify: `server/routes/capabilities.ts`

The Worker cannot aggregate, so it must report `false` — capability-honest by construction, mirroring the existing index-status `has_uncensored=false` and `site_drift_sentinel` hardcodes. There is no probe function; it is a literal in the `features:` object.

- [x] **Step 1: Add the hardcoded flag**

In `server/routes/capabilities.ts`, inside the `features:` object, add `magnet_aggregation` immediately after `watch_intent` (note the explanatory comment — this is a deliberate backend-asymmetric flag, WS3-D2):

```typescript
      watch_intent,
      // ADR-054 WS3: aggregation runs in the Python backend only; the Worker
      // cannot reach/clear external indexers, so it is capability-honestly false
      // (mirrors index-status has_uncensored=false).
      magnet_aggregation: false,
```

- [x] **Step 2: Verify type-check + capabilities test still passes**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Then: `npx vitest run server/__tests__ --config vitest.server.config.ts -t capabilit`
Expected: type-check clean; capabilities test(s) pass with the `magnet_aggregation: false` key present.

- [x] **Step 3: Commit**

```bash
git add server/routes/capabilities.ts
git commit -m "feat(server): expose magnet_aggregation capability flag (hardcoded false) (ADR-054 WS3)"
```

---

## Phase D — Frontend [WEB]

> **Execution notes (2026-06-15, Agent F — branch `claude/adr054-ws3-magnet-source` off WEB `main`):**
> - **Re-vendor (Task 10):** in a git worktree `fetch-openapi.mjs`'s relative `node_modules/.bin` path fails; ran `openapi-typescript` by absolute path from MAIN `openapi.json`. +116 additive lines (aggregate-magnets path + `AggregatedMagnet` schema + `Features.magnet_aggregation`); drift-clean.
> - **`ResolveCard.vue` was also modified** (File Structure listed only `ResolveMagnetTable.vue`): the aggregate call/merge/error must live where the `video_code` is. `ResolveCard` calls `browse.aggregateMagnets(code)` on mount + url-change (latest-wins guard), appends rows into `displayMagnets`, and surfaces `aggregateError` via an `NAlert`.
> - **Score is rendered too:** the gated Source column shows provenance `NTag`(s) **and** the ADR-024 `quality_score` (warning `NTag`, `quality_reasons` in the tooltip), per the campaign deliverable. Added a `browse.resolve.magnet.score` i18n key.
> - **`skipErrorToast: true`** on the aggregate POST (the caller renders the inline `NAlert`) — avoids a duplicate global toast.
> - **i18n:** en + zh-CN + **ja** (the repo's `tests/unit/i18n-parity.spec.ts` enforces 3-locale parity, beyond the en/zh in this plan).
> - **v1 known behavior:** aggregated rows are appended without dedup against the javdb magnets (the backend dedups across external sources only).

### Task 10: Regenerate api types

**Files:**
- Modify: `src/types/api.gen.ts`

- [x] **Step 1: Regenerate from the local openapi.json produced in Task 7**

Run: `OPENAPI_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json node scripts/fetch-openapi.mjs`
Expected: regenerates `src/types/api.gen.ts`.

- [x] **Step 2: Verify `Features.magnet_aggregation` is now typed**

Run: `grep -n "magnet_aggregation" src/types/api.gen.ts`
Expected: matches the new `magnet_aggregation: boolean;` line under the `Features` schema.

- [x] **Step 3: Commit**

```bash
git add src/types/api.gen.ts
git commit -m "chore(web): re-vendor api types for magnet_aggregation (ADR-054 WS3)"
```

> Note: the `apiAggregateMagnets` client in Task 11 is hand-typed, so it does not depend on this step. This regeneration is only needed so `cap.data?.features?.magnet_aggregation` type-checks.

---

### Task 11: api client + browse store merge

**Files:**
- Modify: `src/api/explore.ts`, `src/stores/browse.ts`

- [x] **Step 1: Add the api client**

In `src/api/explore.ts`, add the aggregated-magnet types + the client function (hand-typed, mirroring the other `apiX` functions; shapes mirror `AggregateMagnetsResponse`):

```typescript
export interface AggregatedMagnet {
  magnet_uri: string
  name: string
  size: string
  tags: string[]
  file_count: number
  info_hash: string | null
  sources: string[]
  quality_score: number
  quality_reasons: string[]
}

export interface AggregateMagnetsResponse {
  video_code: string
  magnets: AggregatedMagnet[]
}

export async function apiAggregateMagnets(
  videoCode: string,
): Promise<AggregateMagnetsResponse> {
  const { data } = await http.post<AggregateMagnetsResponse>(
    '/api/explore/aggregate-magnets',
    { video_code: videoCode },
  )
  return data
}
```

- [x] **Step 2: Extend `MagnetRow` + add a store merge action**

In `src/stores/browse.ts`, extend `MagnetRow` (it is already an open shape, so this is additive typing only):

```typescript
export interface MagnetRow {
  magnet?: string
  title?: string
  size?: string
  quality?: string
  date?: string
  href?: string
  source?: string
  quality_score?: number
  sources?: string[]
  [key: string]: unknown
}
```

Add the import alongside the other `@/api/explore` imports:

```typescript
import { apiAggregateMagnets } from '@/api/explore'
import { useCapabilitiesStore } from '@/stores/capabilities'
```

Add this action inside `useBrowseStore` (capability-gated; merges `source`-tagged rows into a caller-held magnet array — the caller is `ResolveCard`/`ResolveMagnetTable`, which already derives `magnets` from `detail.magnets`):

```typescript
  // Returns BOTH the mapped rows and an error string, so the caller can tell
  // "no extra magnets" apart from "aggregation failed" and surface the latter
  // (a silently-swallowed [] would also make `aggregateError` unreachable).
  async function aggregateMagnets(
    videoCode: string,
  ): Promise<{ rows: MagnetRow[]; error: string | null }> {
    const cap = useCapabilitiesStore()
    if (!cap.data?.features?.magnet_aggregation) return { rows: [], error: null }
    try {
      const res = await apiAggregateMagnets(videoCode)
      // Map the aggregated shape onto the table's MagnetRow shape: the table
      // reads `magnet`/`title`/`size`, so alias magnet_uri→magnet, name→title.
      const rows = res.magnets.map((m) => ({
        magnet: m.magnet_uri,
        title: m.name,
        size: m.size,
        source: m.sources.join(', '),
        sources: m.sources,
        quality_score: m.quality_score,
      }))
      return { rows, error: null }
    } catch (err) {
      // Best-effort: existing rows still render, but the failure is reported so
      // the UI can show `browse.resolve.magnet.aggregateError` once.
      return { rows: [], error: err instanceof Error ? err.message : 'aggregation failed' }
    }
  }
```

Export `aggregateMagnets` in the store's returned object (add `aggregateMagnets,` near `downloadMagnet,`). The caller (`ResolveCard`, Task 12) spreads `.rows` into the magnet array and, when `.error` is non-null, surfaces `t('browse.resolve.magnet.aggregateError')` (e.g. an `NAlert`/message) so a failed aggregation is visible rather than silent.

- [x] **Step 3: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors referencing `explore.ts` / `browse.ts`. (Commit with Task 12.)

---

### Task 12: Gated Source column in ResolveMagnetTable + i18n

**Files:**
- Modify: `src/components/browse/ResolveMagnetTable.vue`
- Modify: `src/i18n/locales/en.json`, `src/i18n/locales/zh-CN.json`

Add ONE gated **Source** column (NTag) after the existing quality column. Gate it on `cap.data?.features?.magnet_aggregation` so deployments without indexers never render the column. Existing index-status dots + download action + `browse.resolve.magnet.*` keys keep working.

- [x] **Step 1: Add the capability gate + Source column**

In `src/components/browse/ResolveMagnetTable.vue` `<script setup>`, add the capabilities store import + ref near the other store imports:

```typescript
import { useCapabilitiesStore } from '@/stores/capabilities'
```

and inside the setup body:

```typescript
const cap = useCapabilitiesStore()
const showSource = computed(() => !!cap.data?.features?.magnet_aggregation)
```

In the `columns` computed, after the `quality` column entry and before the `date` column, insert the gated Source column. Because it is conditional, build it into the `cols` array right after the base columns are declared:

```typescript
  if (showSource.value) {
    cols.push({
      title: t('browse.resolve.magnet.col.source'),
      key: 'source',
      width: 130,
      render: (row) =>
        row.source
          ? h(NTag, { size: 'small', round: true, type: 'success' }, { default: () => String(row.source) })
          : '—',
    })
  }
```

> Place this `if (showSource.value)` block **before** the existing `if (isAdmin.value)` action-column block so Source renders left of the Action column. The `NTag` import already exists in this file.

- [x] **Step 2: Add the i18n strings (en)**

In `src/i18n/locales/en.json`, under `browse.resolve.magnet.col`, add a `source` label (sibling of `title`/`size`/`quality`/`date`/`status`/`action`):

```json
        "source": "Source"
```

Add an aggregation-error string under `browse.resolve.magnet` (sibling of `empty`/`noMagnet`/`downloaded`/`download`):

```json
      "aggregateError": "Failed to aggregate magnets from external sources."
```

- [x] **Step 3: Add the i18n strings (zh-CN, parity)**

In `src/i18n/locales/zh-CN.json`, mirror both keys (en/zh parity is mandatory — translation drift is a defect):

```json
        "source": "来源"
```

```json
      "aggregateError": "从外部来源聚合磁力链接失败。"
```

> The repo also carries `ja.json`. The house pairing rule is en↔zh; updating `ja.json` is optional and out of WS3 scope. If your CI enforces a 3-locale key-parity check, add the same two keys to `ja.json` ("ソース" / aggregation-failure message) to keep it green — otherwise leave it.

- [x] **Step 4: Type-check + lint**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Then: `npx eslint src/components/browse/ResolveMagnetTable.vue src/stores/browse.ts src/api/explore.ts`
Expected: no errors.

- [x] **Step 5: Verify i18n parity (no missing keys across en/zh)**

Run: `node -e "const en=require('./src/i18n/locales/en.json'),zh=require('./src/i18n/locales/zh-CN.json'); const c=en.browse.resolve.magnet.col; console.log('en source:',c.source,'| zh source:',zh.browse.resolve.magnet.col.source); console.log('en err:',en.browse.resolve.magnet.aggregateError,'| zh err:',zh.browse.resolve.magnet.aggregateError)"`
Expected: prints both en and zh values for `source` and `aggregateError` (no `undefined`).

- [x] **Step 6: Commit**

```bash
git add src/api/explore.ts src/stores/browse.ts src/components/browse/ResolveMagnetTable.vue src/i18n/locales/en.json src/i18n/locales/zh-CN.json
git commit -m "feat(web): gated Source column for aggregated magnets (ADR-054 WS3)"
```

---

## Verification gates (whole IMP)

Run these before opening PRs in either repo.

1. **[MAIN] Indexer + aggregation + router suite green:**
   `python3 -m pytest tests/unit/test_indexer_dispatch.py tests/unit/test_indexer_fetch.py tests/unit/test_indexer_javbus.py tests/unit/test_indexer_sukebei.py tests/unit/test_indexer_aggregate.py tests/unit/test_aggregate_magnets_router.py -q`
   Expected: all PASS.

2. **[MAIN] Capability honesty (Python):**
   `python3 -c "from apps.api.routers.capabilities import build_capabilities; print(build_capabilities().features.magnet_aggregation)"` → `False` on empty `MAGNET_SOURCES`. Set `MAGNET_SOURCES=['javbus']` in a throwaway `config.py` and re-run → `True`.

3. **[WEB] Worker suite green (501 mirror + capability):**
   `npx vitest run server/__tests__ --config vitest.server.config.ts`
   Expected: `explore-aggregate.test.ts` 501 passes; capabilities test passes with `magnet_aggregation: false`.

4. **OpenAPI ↔ api.gen parity (no drift):** `/api/explore/aggregate-magnets` in `docs/api/openapi.json` `paths`; `magnet_aggregation` in `Features.properties`; `grep magnet_aggregation src/types/api.gen.ts` matches.

5. **`/resolve` byte-parity preserved:** with `MAGNET_SOURCES` empty (flag off), the Browse `/resolve` response and the magnet table are byte-identical to today — the Source column is hidden, no aggregate call fires. Manually confirm a resolve of a detail page renders unchanged.

6. **Fan-out isolation:** with both sources configured but one base URL pointed at an unreachable host, the aggregate endpoint still returns the reachable source's rows (per-source isolation), not a 500.

---

## Out of scope / Deferred (roadmap)

- **`MagnetSourceResult` cache table + TTL + Cron pre-warm** (WS3-D1 Option B). v1 is ephemeral. When added, it follows house DDL conventions: snake_case, `CREATE TABLE IF NOT EXISTS`, `strftime('%Y-%m-%dT%H:%M:%fZ','now')` default, `-- Write-Class: authoritative` header, `javdb/migrations/d1/<date>_add_magnet_source_result.sql`, D1-first then `sync_d1_to_sqlite --apply --force-overwrite-all`, mirrored into the relevant DDL literal in `_db_migrations.py`, and a `SELECT 1 FROM MagnetSourceResult LIMIT 1` capability probe in **both** backends. No `user_id`.
- **Stored-evaluation join** (WS3-D5 Option B) — reuse stored `TorrentQualityEvaluation` rows by info_hash when present, else fall back to the live name/tag score. Gated on the cache table above. **Do not** write file-list-less external magnets into the ADR-024 evidence/evaluation tables — that pollutes the qB-probe shadow model (keyed by info_hash/scoring_version).
- **BTdig + BTSOW plugins** (WS3-D3 "more sources") — additive `indexer` plugins once the two-source vertical is proven.
- **Seeders/peers / swarm-health** — no such field exists in any current model; these would be net-new columns, not free from existing tables.
- **Negative-cache / backoff tuning** (ADR-054 doc:212) — deferred with the cache table.
