# IMP-ADR035-02: Independent Canary Probe (Site-Contract Sentinel Phase 2) Implementation Plan

**Status:** Proposed (2026-06-03) — Phase 2 of three. Phase 1 ([IMP-ADR035-01](IMP-ADR035-01-piggyback-and-gate.md)) shipped and is verified.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-035](ADR-035-site-contract-drift-sentinel.md) (umbrella) — this is **Phase 2** (the independent canary, ADR-035 D1 source (b)).

**Goal:** Add a small, independent, scheduled **canary** that fetches a fixed set of pinned javdb.com pages, parses them with the same parsers the daily spider uses, and detects site-contract drift **between** daily runs — via the Phase-1 detector core (per-field fill-rate vs. the committed daily baseline) plus **golden anchors** (exact expected values on known pinned pages) — raising a `site_drift` `OpsIncident` so the operator gets lead time *before* the next daily run gates.

**Architecture:** A new read-only `probes.py` declares the pinned page set + golden anchors and does fetch→parse→observe→anchor-check through an **injectable** `SpiderGateway` (so it is unit-testable with a fake gateway and never needs a live fetch in tests). A new `service.run_canary()` wires the probe output to the **existing** Phase-1 `detectors.evaluate()` core, reads the committed-daily-run baseline from `ParseRunFieldFillRepo` (read-only — the canary never writes fills), merges golden-anchor findings, and emits the `site_drift` incident (the service stays the **sole writer**, exactly as in Phase 1). The Phase-1 CLI (`apps/cli/ops/sentinel.py`) gains a `--canary` mode and a `--capture-anchors` helper; a new scheduled workflow `.github/workflows/SiteContractSentinel.yml` runs the canary against D1.

**Tech Stack:** Python 3, `javdb.spider.spider_gateway.create_gateway` (proxy + CF-bypass + cookie fetch), `javdb.parsing` parsers, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `pytest`, GitHub Actions (cron), Cloudflare D1 + `wrangler`.

**Scope decisions (read before implementing):**

- **The canary does NOT persist fills to `ParseRunFieldFill`.** That table is keyed by `session_id` (ADR-035 D8); the canary has no session. It evaluates fills **in memory** against the committed daily baseline and emits an incident on drift. This keeps `ParseRunFieldFill` purely session-keyed and keeps the baseline learned from clean committed daily runs only (D5). Adding canary points to a Phase-3 web timeline is an explicit non-goal here.
- **Index page → fill-rate; detail pages → golden anchors.** One index page yields ~40 entries (≥ `SENTINEL_MIN_SAMPLE`), so the fill-rate path (reusing `evaluate()`) is statistically valid for the index. The canary fetches only a handful of detail pages (sample ≪ `min_sample`), so the detector's sample-size guard would skip them — therefore detail-page coverage is provided by **golden anchors** (exact assertions on *known* pages), not fill-rate. This is by design, not a gap.
- **Drift ≠ fetch failure (ADR-035 D4).** If a pinned page fails to load (Cloudflare wall / login / 404 / network), the canary records a *fetch failure* and **does not** raise a drift incident — login/maintenance/empty stay `html_validators`' domain. A `site_drift` incident fires only when a page **parsed** but a field collapsed (fill-rate) or a known value changed (anchor).
- **Golden-anchor values are operator-captured data, not invented constants.** `DEFAULT_GOLDEN_ANCHORS` ships **empty**; the canary is fully useful with zero anchors (critical fill-rate on the index + soft-vs-baseline). Anchors add stronger detail coverage once an operator pins real pages via `--capture-anchors` (Task 5) and stores them in `SENTINEL_CANARY_ANCHORS`. Re-pin if javdb removes a page (a fetch failure logs a "re-pin" warning).

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/ops/sentinel/probes.py` | Create | Pinned-page + golden-anchor declarations; pure `check_golden_anchors()`; `run_probes(gateway) -> ProbeObservation` |
| `javdb/ops/sentinel/persistence.py` | Modify | `build_drift_incident(..., trigger_source="sentinel")` — add `trigger_source` so canary incidents are distinct |
| `javdb/ops/sentinel/service.py` | Modify | Add `run_canary(...)` orchestrator (reuse detector core + baseline; sole writer of the canary incident) |
| `javdb/ops/sentinel/__init__.py` | Modify | Re-export `run_canary` |
| `apps/cli/ops/sentinel.py` | Modify | Add `--canary` mode + `--capture-anchors` helper; make `--session-id` optional |
| `.github/workflows/SiteContractSentinel.yml` | Create | Cron + dispatch canary against D1 |
| `config.py.example` | Modify | Document `SENTINEL_CANARY_INDEX_URL`, `SENTINEL_CANARY_ANCHORS` |
| `CONTEXT.md` | Modify | Promote *Canary probe* from "(Phase-2)" to shipped |
| `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Document `--canary` / `--capture-anchors` |
| `tests/unit/test_sentinel_probes.py` | Create | `check_golden_anchors` + `run_probes` (fake gateway + fixtures) |
| `tests/unit/test_sentinel_canary_incident.py` | Create | `build_drift_incident(trigger_source=...)` + `run_canary` orchestration |
| `tests/smoke/test_sentinel_cli_canary.py` | Create | CLI `--canary` / `--capture-anchors` smoke |

**Naming contract (verbatim across tasks):**
`GoldenAnchor(url, video_code, title_contains, min_magnets)`; `DEFAULT_INDEX_URL`; `DEFAULT_GOLDEN_ANCHORS: tuple[GoldenAnchor, ...]`; module fns `index_url() -> str`, `golden_anchors() -> tuple[GoldenAnchor, ...]`, `check_golden_anchors(detail, anchor) -> list[DriftFinding]`, `run_probes(gateway) -> ProbeObservation`; `ProbeObservation(fills: list[FieldFill], anchor_findings: list[DriftFinding], fetch_failures: list[str])`; persistence `build_drift_incident(verdict, *, session_id, run_id, run_attempt, trigger_source="sentinel")`; service `run_canary(*, gateway=None, options=None, run_id=None, run_attempt=None, fill_repo=None, incident_repo=None) -> SentinelVerdict`.

Reused verbatim from Phase 1 (do not redefine): `FieldFill`, `DriftFinding`, `SentinelVerdict`, `SentinelOptions` (`javdb/ops/sentinel/models.py`); `evaluate(fills, *, min_sample, baseline_fn)` (`detectors.py`); `FieldHealthAccumulator.observe/fill_rates` (`field_health.py`); `ParseRunFieldFillRepo.baseline(page_type, field, *, window)` (read-only); `_fill_ctx` / `_incident_ctx` context helpers (`service.py`); `create_gateway(*, use_proxy, use_cf_bypass, use_cookie) -> SpiderGateway` with `.fetch_html(url) -> str | None` (`javdb/spider/spider_gateway.py`); `parse_index_page(html, page_num) -> IndexPageResult` (`.movies`), `parse_detail_page(html) -> MovieDetail` (`javdb.parsing`).

---

## Task 1: `probes.py` declarations + pure `check_golden_anchors`

**Files:**
- Create: `javdb/ops/sentinel/probes.py`
- Test: `tests/unit/test_sentinel_probes.py`

`check_golden_anchors(detail, anchor)` compares a parsed `MovieDetail` against an anchor's expectations and returns one **critical** `DriftFinding` per violated expectation (a *known* page parsing to the wrong value is unambiguous drift). It reads fields via `getattr` so a dataclass or a simple stand-in both work.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sentinel_probes.py
from dataclasses import dataclass, field as dfield

from javdb.ops.sentinel.probes import GoldenAnchor, check_golden_anchors


@dataclass
class _Detail:
    video_code: str = ""
    title: str = ""
    magnets: list = dfield(default_factory=list)


def test_matching_detail_yields_no_findings():
    d = _Detail(video_code="ABC-123", title="Some Long Title", magnets=[1, 2, 3])
    anchor = GoldenAnchor(url="u", video_code="ABC-123",
                          title_contains="Long", min_magnets=2)
    assert check_golden_anchors(d, anchor) == []


def test_wrong_video_code_is_critical():
    d = _Detail(video_code="WRONG-1", title="t", magnets=[1])
    anchor = GoldenAnchor(url="u", video_code="ABC-123",
                          title_contains="", min_magnets=0)
    out = check_golden_anchors(d, anchor)
    assert len(out) == 1
    assert out[0].field == "video_code"
    assert out[0].severity == "critical"
    assert out[0].page_type == "detail"


def test_missing_magnets_is_critical():
    d = _Detail(video_code="ABC-123", title="t", magnets=[])
    anchor = GoldenAnchor(url="u", video_code="ABC-123",
                          title_contains="", min_magnets=5)
    out = check_golden_anchors(d, anchor)
    assert [f.field for f in out] == ["magnets"]
    assert out[0].severity == "critical"


def test_vacuous_anchor_checks_nothing():
    d = _Detail(video_code="", title="", magnets=[])
    anchor = GoldenAnchor(url="u", video_code="", title_contains="", min_magnets=0)
    assert check_golden_anchors(d, anchor) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_probes.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.ops.sentinel.probes`

- [ ] **Step 3: Write the declarations + pure checker**

```python
# javdb/ops/sentinel/probes.py
"""Independent canary probe for the site-contract drift sentinel (ADR-035 Phase 2).

Fetches a small fixed set of pinned javdb pages, parses them with the same
parsers the daily spider uses, and produces (a) per-field fill observations for
the Phase-1 detector core and (b) golden-anchor findings (exact expected values
on known pages). Read-only: never writes the DB and never touches the
session/commit lifecycle — the service remains the sole writer (ADR-035 D7)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from javdb.infra.config import cfg
from javdb.ops.sentinel.field_health import FieldHealthAccumulator
from javdb.ops.sentinel.models import DriftFinding, FieldFill
from javdb.parsing import parse_detail_page, parse_index_page

logger = logging.getLogger(__name__)

# A stable, long-lived list page. The homepage is the safest default; operators
# may override via SENTINEL_CANARY_INDEX_URL.
DEFAULT_INDEX_URL = "https://javdb.com/"


@dataclass(frozen=True)
class GoldenAnchor:
    """Known-stable expectations for one pinned DETAIL page.

    The canary fetches a *known* page, so it can assert exact parsed values — a
    far stronger check than fill-rate. Values are operator-captured (see the
    `--capture-anchors` CLI); empty expectation fields are skipped."""

    url: str
    video_code: str = ""      # exact MovieDetail.video_code, or "" to skip
    title_contains: str = ""  # substring required in MovieDetail.title, or "" to skip
    min_magnets: int = 0      # MovieDetail.magnets length must be >= this, or 0 to skip


# Ships empty: anchors are deployment-specific captured data, not invented
# constants. The canary is fully useful with zero anchors (critical fill-rate on
# the index + soft-vs-baseline). See IMP header "Golden-anchor values".
DEFAULT_GOLDEN_ANCHORS: tuple[GoldenAnchor, ...] = ()


def index_url() -> str:
    return str(cfg("SENTINEL_CANARY_INDEX_URL", DEFAULT_INDEX_URL))


def golden_anchors() -> tuple[GoldenAnchor, ...]:
    """Anchors from config (`SENTINEL_CANARY_ANCHORS`: list of dicts), else default."""
    raw = cfg("SENTINEL_CANARY_ANCHORS", None)
    if not raw:
        return DEFAULT_GOLDEN_ANCHORS
    out: list[GoldenAnchor] = []
    for item in raw:
        out.append(GoldenAnchor(
            url=item["url"],
            video_code=item.get("video_code", ""),
            title_contains=item.get("title_contains", ""),
            min_magnets=int(item.get("min_magnets", 0)),
        ))
    return tuple(out)


def check_golden_anchors(detail: Any, anchor: GoldenAnchor) -> list[DriftFinding]:
    """Pure check: a parsed MovieDetail vs. an anchor's expectations.

    A mismatch on a *known* page is unambiguous **critical** drift (the parser
    broke). One DriftFinding per violated expectation; empty expectations skip."""
    findings: list[DriftFinding] = []

    code = (getattr(detail, "video_code", "") or "").strip()
    if anchor.video_code and code != anchor.video_code:
        findings.append(DriftFinding("detail", "video_code", "critical", 0.0, 1.0, None))

    title = getattr(detail, "title", "") or ""
    if anchor.title_contains and anchor.title_contains not in title:
        findings.append(DriftFinding("detail", "title", "critical", 0.0, 1.0, None))

    magnets = getattr(detail, "magnets", []) or []
    if anchor.min_magnets and len(magnets) < anchor.min_magnets:
        rate = len(magnets) / anchor.min_magnets
        findings.append(DriftFinding("detail", "magnets", "critical", rate, 1.0, None))

    return findings


@dataclass
class ProbeObservation:
    """Output of one canary sweep over the pinned pages."""

    fills: list[FieldFill] = field(default_factory=list)
    anchor_findings: list[DriftFinding] = field(default_factory=list)
    fetch_failures: list[str] = field(default_factory=list)  # URLs that did not load


def run_probes(gateway: Any) -> ProbeObservation:
    """Fetch + parse the pinned pages via *gateway* and observe field health.

    *gateway* must expose ``fetch_html(url) -> str | None`` (a SpiderGateway).
    Injecting it keeps this unit-testable with a fake gateway. A fetch failure is
    recorded separately and is **not** drift (ADR-035 D4)."""
    acc = FieldHealthAccumulator()
    obs = ProbeObservation()

    idx_url = index_url()
    idx_html = gateway.fetch_html(idx_url)
    if idx_html:
        result = parse_index_page(idx_html, 1)
        acc.observe("index", result.movies)
    else:
        obs.fetch_failures.append(idx_url)
        logger.warning("canary: index page fetch failed: %s", idx_url)

    for anchor in golden_anchors():
        html = gateway.fetch_html(anchor.url)
        if not html:
            obs.fetch_failures.append(anchor.url)
            logger.warning(
                "canary: anchor page fetch failed (stale anchor? re-pin needed): %s",
                anchor.url)
            continue
        detail = parse_detail_page(html)
        acc.observe("detail", [detail])
        obs.anchor_findings.extend(check_golden_anchors(detail, anchor))

    obs.fills = acc.fill_rates()
    return obs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_probes.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/sentinel/probes.py tests/unit/test_sentinel_probes.py
git commit -m "feat(sentinel): add canary probes module + golden-anchor checker (ADR-035 Phase 2)"
```

---

## Task 2: `run_probes` fetch+observe via a fake gateway

**Files:**
- Modify: `tests/unit/test_sentinel_probes.py` (append)

`run_probes` is already implemented (Task 1). This task pins its fetch→parse→observe contract end to end with a fake gateway and the real parser fixtures, plus the fetch-failure path.

- [ ] **Step 1: Append the failing tests**

```python
# tests/unit/test_sentinel_probes.py  (append)
from pathlib import Path

import pytest

from javdb.ops.sentinel import probes as _probes

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "parser"


class _FakeGateway:
    """Returns canned HTML keyed by URL; None for anything unmapped."""

    def __init__(self, mapping: dict[str, str | None]):
        self._mapping = mapping
        self.requested: list[str] = []

    def fetch_html(self, url: str):
        self.requested.append(url)
        return self._mapping.get(url)


def test_run_probes_index_produces_fills(monkeypatch):
    html = (_FIXTURES / "index_edge_cases.html").read_text(encoding="utf-8")
    monkeypatch.setattr(_probes, "golden_anchors", lambda: ())
    gw = _FakeGateway({_probes.index_url(): html})

    obs = _probes.run_probes(gw)

    assert obs.fetch_failures == []
    assert any(f.page_type == "index" for f in obs.fills)
    assert obs.anchor_findings == []


def test_run_probes_records_fetch_failure_not_drift(monkeypatch):
    monkeypatch.setattr(_probes, "golden_anchors", lambda: ())
    gw = _FakeGateway({})  # index URL returns None

    obs = _probes.run_probes(gw)

    assert obs.fills == []
    assert obs.anchor_findings == []
    assert obs.fetch_failures == [_probes.index_url()]


def test_run_probes_anchor_mismatch_is_finding(monkeypatch):
    idx = (_FIXTURES / "index_edge_cases.html").read_text(encoding="utf-8")
    detail = (_FIXTURES / "detail_actor_edge_cases.html").read_text(encoding="utf-8")
    anchor = _probes.GoldenAnchor(
        url="https://javdb.com/v/PINNED",
        video_code="DEFINITELY-WRONG-999",  # cannot match the parsed page
    )
    monkeypatch.setattr(_probes, "golden_anchors", lambda: (anchor,))
    gw = _FakeGateway({_probes.index_url(): idx, anchor.url: detail})

    obs = _probes.run_probes(gw)

    assert any(f.field == "video_code" and f.severity == "critical"
               for f in obs.anchor_findings)
    assert obs.fetch_failures == []
```

- [ ] **Step 2: Run to verify PASS**

Run: `pytest tests/unit/test_sentinel_probes.py -v`
Expected: PASS (7 passed total)

> If `index_edge_cases.html` parses to zero movies in your build, the first
> assertion (`any(... page_type=="index")`) still holds: `observe` records a
> `FieldFill` per contract field as soon as ≥1 record is seen. If the fixture is
> genuinely empty, substitute `tests/fixtures/parser/top_edge_cases.html` (a
> list page) and keep the same assertions.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_sentinel_probes.py
git commit -m "test(sentinel): pin canary run_probes fetch+observe contract"
```

---

## Task 3: `build_drift_incident` gains a `trigger_source`

**Files:**
- Modify: `javdb/ops/sentinel/persistence.py`
- Test: `tests/unit/test_sentinel_canary_incident.py`

Canary incidents must be distinguishable from daily-gate incidents and must not collide in the deterministic `incident_id` (which hashes `trigger_source`). Add a `trigger_source` parameter (default `"sentinel"` — Phase-1 behaviour unchanged) and tailor the recommended action text.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sentinel_canary_incident.py
from javdb.ops.sentinel.models import DriftFinding, SentinelVerdict
from javdb.ops.sentinel.persistence import build_drift_incident


def _verdict(critical: bool) -> SentinelVerdict:
    v = SentinelVerdict(critical=critical)
    v.findings.append(DriftFinding("index", "href", "critical", 0.1, 0.99, None))
    return v


def test_default_trigger_source_is_sentinel():
    rec = build_drift_incident(_verdict(True), session_id="S1",
                               run_id=None, run_attempt=None)
    assert rec.trigger_source == "sentinel"
    assert rec.incident_type == "site_drift"


def test_canary_trigger_source_changes_id_and_actions():
    sentinel = build_drift_incident(_verdict(True), session_id=None,
                                    run_id="R1", run_attempt=1,
                                    trigger_source="sentinel")
    canary = build_drift_incident(_verdict(True), session_id=None,
                                  run_id="R1", run_attempt=1,
                                  trigger_source="canary")
    assert canary.trigger_source == "canary"
    # trigger_source is hashed into the id -> distinct rows, no collision.
    assert canary.incident_id != sentinel.incident_id
    assert "canary" in canary.recommended_next_actions_json.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_canary_incident.py -v`
Expected: FAIL — `build_drift_incident() got an unexpected keyword argument 'trigger_source'`

- [ ] **Step 3: Add the `trigger_source` parameter**

In `javdb/ops/sentinel/persistence.py`, change the signature and the action/id wiring. Replace the function header and the `actions` / `incident_id` / `trigger_source` lines:

Find:

```python
def build_drift_incident(
    verdict: SentinelVerdict, *, session_id: str | None,
    run_id: str | None, run_attempt: int | None,
) -> OpsIncidentRecord:
    now = utc_now_iso()
    findings = [
        {"page_type": f.page_type, "field": f.field, "severity": f.severity,
         "fill_rate": f.fill_rate, "threshold": f.threshold, "baseline": f.baseline}
        for f in verdict.findings
    ]
    confidence = "high" if verdict.critical else "medium"
    actions = (["Inspect the parser/selectors; the commit was gated."]
               if verdict.critical else ["Inspect the soft-field selector; run committed."])
    return OpsIncidentRecord(
        incident_id=build_incident_id(
            trigger_source="sentinel", run_id=run_id, run_attempt=run_attempt,
            session_id=session_id, incident_type="site_drift",
        ),
        trigger_source="sentinel",
```

Replace with:

```python
def build_drift_incident(
    verdict: SentinelVerdict, *, session_id: str | None,
    run_id: str | None, run_attempt: int | None,
    trigger_source: str = "sentinel",
) -> OpsIncidentRecord:
    now = utc_now_iso()
    findings = [
        {"page_type": f.page_type, "field": f.field, "severity": f.severity,
         "fill_rate": f.fill_rate, "threshold": f.threshold, "baseline": f.baseline}
        for f in verdict.findings
    ]
    confidence = "high" if verdict.critical else "medium"
    if trigger_source == "canary":
        actions = (
            ["Between-run canary detected critical drift; inspect the parser/"
             "selectors. The next daily run will gate the commit."]
            if verdict.critical else
            ["Between-run canary detected soft drift; inspect the soft-field selector."]
        )
    else:
        actions = (["Inspect the parser/selectors; the commit was gated."]
                   if verdict.critical else ["Inspect the soft-field selector; run committed."])
    return OpsIncidentRecord(
        incident_id=build_incident_id(
            trigger_source=trigger_source, run_id=run_id, run_attempt=run_attempt,
            session_id=session_id, incident_type="site_drift",
        ),
        trigger_source=trigger_source,
```

> Leave the rest of the `OpsIncidentRecord(...)` body unchanged (it already
> references `actions`, `findings`, `confidence`, `now`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_canary_incident.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Regression — Phase-1 incident behaviour unchanged**

Run: `pytest tests/unit/test_sentinel_service.py -v`
Expected: PASS (the default `trigger_source="sentinel"` preserves Phase-1 behaviour).

- [ ] **Step 6: Commit**

```bash
git add javdb/ops/sentinel/persistence.py tests/unit/test_sentinel_canary_incident.py
git commit -m "feat(sentinel): thread trigger_source through drift incident builder"
```

---

## Task 4: `service.run_canary()` orchestrator

**Files:**
- Modify: `javdb/ops/sentinel/service.py`
- Modify: `javdb/ops/sentinel/__init__.py`
- Test: `tests/unit/test_sentinel_canary_incident.py` (append)

`run_canary` reuses the Phase-1 `evaluate()` core over the probe's index fills (baseline = committed daily runs, read-only), merges the golden-anchor findings, and — if there are any findings — emits one `site_drift` incident with `trigger_source="canary"`. The service stays the sole writer.

- [ ] **Step 1: Append the failing test**

```python
# tests/unit/test_sentinel_canary_incident.py  (append)
import pytest

from javdb.ops.sentinel import probes as _probes
from javdb.ops.sentinel import service as _service
from javdb.ops.sentinel.models import DriftFinding, FieldFill, SentinelOptions
from javdb.ops.sentinel.probes import ProbeObservation


class _FakeFillRepo:
    """Only baseline() is exercised by the canary (read-only)."""

    def __init__(self, baseline_value=None):
        self._b = baseline_value

    def baseline(self, page_type, field, *, window):
        return self._b


class _FakeIncidentRepo:
    def __init__(self):
        self.records = []

    def upsert(self, record):
        self.records.append(record)


def _stub_probes(monkeypatch, obs: ProbeObservation):
    monkeypatch.setattr(_probes, "run_probes", lambda gateway: obs)


def test_run_canary_clean_emits_no_incident(monkeypatch):
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 1.0, 100)]))
    inc = _FakeIncidentRepo()
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=inc, options=SentinelOptions(min_sample=30))
    assert v.critical is False
    assert v.findings == []
    assert inc.records == []


def test_run_canary_critical_fill_emits_canary_incident(monkeypatch):
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 0.05, 100)]))  # below min_fill 0.99
    inc = _FakeIncidentRepo()
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(),
                            incident_repo=inc, options=SentinelOptions(min_sample=30),
                            run_id="R1", run_attempt=1)
    assert v.critical is True
    assert len(inc.records) == 1
    assert inc.records[0].trigger_source == "canary"
    assert inc.records[0].incident_type == "site_drift"


def test_run_canary_anchor_finding_emits_incident(monkeypatch):
    _stub_probes(monkeypatch, ProbeObservation(
        fills=[FieldFill("index", "href", 1.0, 100)],
        anchor_findings=[DriftFinding("detail", "video_code", "critical", 0.0, 1.0, None)]))
    inc = _FakeIncidentRepo()
    v = _service.run_canary(gateway=object(), fill_repo=_FakeFillRepo(), incident_repo=inc)
    assert v.critical is True
    assert any(f.field == "video_code" for f in v.findings)
    assert len(inc.records) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_canary_incident.py -v`
Expected: FAIL — `module 'javdb.ops.sentinel.service' has no attribute 'run_canary'`

- [ ] **Step 3: Add `run_canary` to `service.py`**

No new imports are needed: `service.py` already imports `evaluate`, `SentinelOptions`, `SentinelVerdict`, `cfg`, and `build_drift_incident`, and defines `_fill_ctx` / `_incident_ctx` / `logger` (confirm with `grep -nE "^from|^import|_fill_ctx|_incident_ctx" javdb/ops/sentinel/service.py`). `probes` and `create_gateway` are imported lazily *inside* `run_canary` (below) to avoid importing the spider at module load.

Append this function at the end of `javdb/ops/sentinel/service.py`:

```python
def run_canary(
    *, gateway=None, options: SentinelOptions | None = None,
    run_id: str | None = None, run_attempt: int | None = None,
    fill_repo=None, incident_repo=None,
) -> SentinelVerdict:
    """Independent between-run canary (ADR-035 D1 source b).

    Fetch the pinned pages, evaluate index fill-rate against the committed daily
    baseline (reusing the Phase-1 detector core), merge golden-anchor findings,
    and emit one ``site_drift`` incident (trigger_source='canary') on any finding.
    Read-only w.r.t. ``ParseRunFieldFill``; the service is the sole incident writer."""
    from javdb.ops.sentinel import probes  # local import: avoids a spider import at module load

    opts = options or SentinelOptions(
        min_sample=int(cfg("SENTINEL_MIN_SAMPLE", 30)),
        baseline_window=int(cfg("SENTINEL_BASELINE_WINDOW", 14)),
    )
    if gateway is None:
        from javdb.spider.spider_gateway import create_gateway
        gateway = create_gateway(use_proxy=True, use_cf_bypass=True, use_cookie=True)

    obs = probes.run_probes(gateway)

    # Fill-rate findings via the shared core; baseline = committed daily runs.
    with _fill_ctx(fill_repo) as r:
        verdict = evaluate(
            obs.fills, min_sample=opts.min_sample,
            baseline_fn=lambda pt, f: r.baseline(pt, f, window=opts.baseline_window),
        )

    # Merge golden-anchor findings (a known page parsed wrong -> critical).
    for finding in obs.anchor_findings:
        verdict.findings.append(finding)
        if finding.severity == "critical":
            verdict.critical = True

    if verdict.findings:
        record = build_drift_incident(
            verdict, session_id=None, run_id=run_id, run_attempt=run_attempt,
            trigger_source="canary")
        try:
            with _incident_ctx(incident_repo) as ir:
                ir.upsert(record.with_persistence_status("d1_written"))
        except Exception:
            logger.warning("run_canary: incident persist failed", exc_info=True)
    return verdict
```

- [ ] **Step 4: Re-export from the package**

In `javdb/ops/sentinel/__init__.py`, extend the import and `__all__`:

Find:
```python
from javdb.ops.sentinel.service import evaluate_session, mark_committed, persist_run

__all__ = ["evaluate_session", "mark_committed", "persist_run"]
```

Replace with:
```python
from javdb.ops.sentinel.service import (
    evaluate_session,
    mark_committed,
    persist_run,
    run_canary,
)

__all__ = ["evaluate_session", "mark_committed", "persist_run", "run_canary"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_canary_incident.py -v`
Expected: PASS (5 passed total)

- [ ] **Step 6: Import-and-smoke check**

Run: `python3 -c "from javdb.ops.sentinel import run_canary; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add javdb/ops/sentinel/service.py javdb/ops/sentinel/__init__.py tests/unit/test_sentinel_canary_incident.py
git commit -m "feat(sentinel): add run_canary orchestrator (ADR-035 Phase 2)"
```

---

## Task 5: CLI `--canary` mode + `--capture-anchors` helper

**Files:**
- Modify: `apps/cli/ops/sentinel.py`
- Test: `tests/smoke/test_sentinel_cli_canary.py`

The Phase-1 CLI requires `--session-id`. Make it optional and add two modes:
`--canary` (run the live canary; exit 4 on critical) and `--capture-anchors` (fetch the URLs passed via `--url` and print their current parsed anchor values as JSON, so an operator can populate `SENTINEL_CANARY_ANCHORS`).

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_sentinel_cli_canary.py
import subprocess
import sys


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.sentinel", *args],
        capture_output=True, text=True,
    )


def test_help_mentions_canary():
    r = _run("--help")
    assert r.returncode == 0
    assert "--canary" in r.stdout
    assert "--capture-anchors" in r.stdout


def test_no_mode_and_no_session_errors():
    # Neither --canary nor --session-id -> usage error, exit 2.
    r = _run()
    assert r.returncode == 2
    assert "session-id" in (r.stdout + r.stderr).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/smoke/test_sentinel_cli_canary.py -v`
Expected: FAIL — `--canary` absent from help; no-arg run currently errors on the required `--session-id` with a different message.

- [ ] **Step 3: Rewrite `apps/cli/ops/sentinel.py`**

Replace the file body with (keeps the Phase-1 session path intact, adds the two modes):

```python
# apps/cli/ops/sentinel.py
"""Evaluate parse field-health for site-contract drift (ADR-035).

Three modes:
  * --session-id S   evaluate a daily run's persisted fills (Phase 1; the gate).
  * --canary         run the independent canary over pinned pages (Phase 2).
  * --capture-anchors --url U [--url U2 ...]
                     fetch each URL, print current parsed anchor values as JSON
                     (to populate SENTINEL_CANARY_ANCHORS).

Read-only by default; exit code 4 signals critical drift so a workflow can act."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from javdb.infra.logging import setup_logging
from javdb.ops.sentinel.service import evaluate_session, run_canary

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.sentinel",
        description="Evaluate parse field-health for site-contract drift.",
    )
    p.add_argument("--session-id", default=None,
                   help="Evaluate this run's persisted fills (Phase 1 gate mode).")
    p.add_argument("--canary", action="store_true",
                   help="Run the independent canary over the pinned pages (Phase 2).")
    p.add_argument("--capture-anchors", action="store_true",
                   help="Fetch --url pages and print current parsed anchor values as JSON.")
    p.add_argument("--url", action="append", default=[], dest="urls",
                   help="Detail-page URL to capture (repeatable; with --capture-anchors).")
    p.add_argument("--run-id", default=None)
    p.add_argument("--attempt", type=int, default=None, dest="run_attempt")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def _print_verdict(verdict, json_output: bool) -> None:
    if json_output:
        print(json.dumps({
            "critical": verdict.critical,
            "evaluated": verdict.evaluated,
            "findings": [asdict(f) for f in verdict.findings],
        }, ensure_ascii=False))
    else:
        logger.info("Sentinel: critical=%s evaluated=%d findings=%d",
                    verdict.critical, verdict.evaluated, len(verdict.findings))


def _capture_anchors(urls: list[str]) -> int:
    """Fetch each detail URL and print its current parsed anchor values."""
    from javdb.parsing import parse_detail_page
    from javdb.spider.spider_gateway import create_gateway

    if not urls:
        print("ERROR: --capture-anchors requires at least one --url.", file=sys.stderr)
        return 2
    gw = create_gateway(use_proxy=True, use_cf_bypass=True, use_cookie=True)
    captured = []
    for url in urls:
        html = gw.fetch_html(url)
        if not html:
            logger.warning("capture-anchors: fetch failed: %s", url)
            captured.append({"url": url, "error": "fetch_failed"})
            continue
        d = parse_detail_page(html)
        captured.append({
            "url": url,
            "video_code": getattr(d, "video_code", ""),
            "title_contains": (getattr(d, "title", "") or "")[:24],
            "min_magnets": len(getattr(d, "magnets", []) or []),
        })
    print(json.dumps(captured, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    if args.capture_anchors:
        return _capture_anchors(args.urls)

    try:
        if args.canary:
            verdict = run_canary(run_id=args.run_id, run_attempt=args.run_attempt)
        else:
            if not args.session_id:
                print("ERROR: provide --session-id (gate mode) or --canary.",
                      file=sys.stderr)
                return 2
            verdict = evaluate_session(
                args.session_id, run_id=args.run_id, run_attempt=args.run_attempt)
    except Exception:
        logger.exception("Sentinel evaluation failed")
        return 1

    _print_verdict(verdict, args.json_output)
    return 4 if verdict.critical else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/smoke/test_sentinel_cli_canary.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Regression — Phase-1 CLI smoke still passes**

Run: `pytest tests/smoke/test_sentinel_cli.py -v`
Expected: PASS — `--help` still contains "drift"; the Phase-1 `--session-id` path is unchanged.

> If `tests/smoke/test_sentinel_cli.py` asserts that running with **no** args
> fails on a *required* `--session-id` (argparse exit 2 with "required"), update
> that assertion: `--session-id` is now optional and the new guard prints
> "provide --session-id (gate mode) or --canary." with exit 2. The exit code is
> unchanged (2); only the message differs.

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/sentinel.py tests/smoke/test_sentinel_cli_canary.py
git commit -m "feat(sentinel): add --canary and --capture-anchors CLI modes (ADR-035 Phase 2)"
```

---

## Task 6: Scheduled workflow `SiteContractSentinel.yml`

**Files:**
- Create: `.github/workflows/SiteContractSentinel.yml`

A cron + dispatch workflow that materialises `config.py` (proxy + JavDB login + D1) and runs `python -m apps.cli.ops.sentinel --canary --json` against D1. Modelled on `ReconcileLibrary.yml` (single job, `STORAGE_BACKEND: d1`, `config_generator --github-actions`, run-one-CLI, upload log, summary) with the proxy/login `VAR_*` block from `DailyIngestion.yml` (the canary fetches javdb, so it needs the spider's proxy + cookie).

- [ ] **Step 1: Create the workflow**

```yaml
# .github/workflows/SiteContractSentinel.yml
name: Site Contract Sentinel

# ADR-035 Phase 2: independent canary. Fetches a small fixed set of pinned
# javdb pages between daily runs and raises a site_drift OpsIncident on drift,
# giving lead time before the next DailyIngestion run gates.

permissions:
  contents: read

on:
  schedule:
    # Every 6h (00/06/12/18 UTC). The 06:00 run lands well before the 12:00 UTC
    # DailyIngestion, so drift is flagged with lead time.
    - cron: '0 */6 * * *'
  workflow_dispatch:
    inputs:
      runner:
        description: 'Runner type. Use the same tier DailyIngestion uses to reach javdb.'
        required: false
        default: 'ubuntu-latest'
        type: choice
        options:
          - 'ubuntu-latest'
          - 'self-hosted'

concurrency:
  group: site-contract-sentinel
  cancel-in-progress: false

jobs:
  canary:
    name: Canary
    runs-on: ${{ inputs.runner || 'ubuntu-latest' }}
    timeout-minutes: 20
    environment: Production
    env:
      TZ: Asia/Singapore
      STORAGE_BACKEND: d1
      D1_RECOVERY_OUTBOX_ENABLED: ${{ vars.D1_RECOVERY_OUTBOX_ENABLED || 'false' }}
      D1_BATCHING_ENABLED: ${{ vars.D1_BATCHING_ENABLED || 'false' }}
      D1_STARTUP_REPLAY_ENABLED: ${{ vars.D1_STARTUP_REPLAY_ENABLED || 'false' }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          ssh-key: ${{ secrets.DEPLOY_KEY }}
          persist-credentials: false
          lfs: true

      - name: Set up Python, venv, dependencies, Rust extension
        uses: ./.github/actions/setup-python-env
        with:
          python-version: '3.11'

      - name: Generate config.py from GitHub Variables and Secrets
        env:
          # Proxy (the canary fetches javdb through the spider's proxy pool).
          VAR_PROXY_MODE: ${{ vars.PROXY_MODE }}
          VAR_PROXY_POOL_JSON: ${{ secrets.PROXY_POOL_JSON }}
          VAR_PROXY_MODULES_JSON: ${{ vars.PROXY_MODULES_JSON }}
          VAR_PROXY_POOL_MAX_FAILURES: ${{ vars.PROXY_POOL_MAX_FAILURES || 3 }}
          VAR_LOGIN_PROXY_NAME: ${{ vars.LOGIN_PROXY_NAME || '' }}
          # JavDB login (cookie-based fetch, matches DailyIngestion).
          VAR_JAVDB_USERNAME: ${{ secrets.JAVDB_USERNAME }}
          VAR_JAVDB_PASSWORD: ${{ secrets.JAVDB_PASSWORD }}
          VAR_JAVDB_SESSION_COOKIE: ${{ secrets.JAVDB_SESSION_COOKIE }}
          # Optional canary tuning (pinned index URL + golden anchors JSON).
          VAR_SENTINEL_CANARY_INDEX_URL: ${{ vars.SENTINEL_CANARY_INDEX_URL || '' }}
          VAR_SENTINEL_CANARY_ANCHORS_JSON: ${{ vars.SENTINEL_CANARY_ANCHORS_JSON || '' }}
          # D1 / Cloudflare — baseline read + incident write land in the reports DB.
          VAR_STORAGE_BACKEND: d1
          VAR_CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          VAR_CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          VAR_D1_HISTORY_DB_ID: ${{ secrets.D1_HISTORY_DB_ID }}
          VAR_D1_REPORTS_DB_ID: ${{ secrets.D1_REPORTS_DB_ID }}
          VAR_D1_OPERATIONS_DB_ID: ${{ secrets.D1_OPERATIONS_DB_ID }}
          VAR_REPORTS_DIR: ${{ vars.REPORTS_DIR || 'reports' }}
          VAR_LOG_LEVEL: ${{ vars.LOG_LEVEL || 'INFO' }}
        run: python3 -m apps.cli.ops.config_generator --github-actions

      - name: Run site-contract canary
        id: canary
        run: |
          set -o pipefail
          mkdir -p logs
          # Do NOT `set -e`: exit 4 (critical drift) is an expected outcome we
          # want to record, not a step crash. Capture the code explicitly.
          STORAGE_BACKEND=d1 python3 -m apps.cli.ops.sentinel \
            --canary --json --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT" \
            2>&1 | tee logs/sentinel_canary.jsonl
          CODE=${PIPESTATUS[0]}
          echo "exit_code=$CODE" >> "$GITHUB_OUTPUT"
          # 0 = clean, 4 = critical drift (recorded as incident); both are "ran ok".
          if [ "$CODE" = "0" ] || [ "$CODE" = "4" ]; then exit 0; fi
          exit "$CODE"

      - name: Upload canary log
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        with:
          name: site-contract-sentinel-log
          path: logs/sentinel_canary.jsonl
          retention-days: 30
          if-no-files-found: ignore

      - name: Summary
        if: always()
        run: |
          {
            echo "## Site Contract Sentinel (canary)"
            echo ""
            echo "- Storage backend: d1"
            echo "- Sentinel exit code: ${{ steps.canary.outputs.exit_code }} (0=clean, 4=critical drift)"
            echo ""
            if [ -f logs/sentinel_canary.jsonl ]; then
              echo "Last output line:"
              echo '```json'
              tail -n 1 logs/sentinel_canary.jsonl
              echo '```'
            fi
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Reconcile the config-generation env block with the source of truth**

The `VAR_*` names above mirror `DailyIngestion.yml`'s "Generate config.py from GitHub Variables and Secrets" step (the maintained contract for `apps.cli.ops.config_generator`). Diff them and add any proxy/login/CF var the daily step sets that the canary also needs (e.g. CF-bypass vars if your deployment uses a hosted bypass):

Run:
```bash
sed -n '/Generate config.py from GitHub Variables/,/config_generator --github-actions/p' .github/workflows/DailyIngestion.yml
```
Expected: prints the daily step's env block. Ensure every proxy/login/CF/D1 var it sets that the canary fetch needs is present in `SiteContractSentinel.yml`. (The two new `VAR_SENTINEL_CANARY_*` lines are canary-only and have no daily counterpart — that is expected. If `config_generator` does not yet map them, that mapping is added in Task 7 Step 2.)

- [ ] **Step 3: Lint the workflow YAML**

Run:
```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/SiteContractSentinel.yml')); print('yaml ok')"
```
Expected: `yaml ok`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/SiteContractSentinel.yml
git commit -m "ci(sentinel): add SiteContractSentinel canary workflow (ADR-035 Phase 2)"
```

---

## Task 7: Config knobs, `config_generator` mapping, docs, full gate

**Files:**
- Modify: `config.py.example`
- Modify: `apps/cli/ops/config_generator.py`
- Modify: `CONTEXT.md`
- Modify: `docs/handbook/en/developer/cli-reference.md` (+ `docs/handbook/zh/developer/cli-reference.md`)

- [ ] **Step 1: Document the canary config knobs** — append to the ADR-035 block in `config.py.example` (after the existing `SENTINEL_BASELINE_WINDOW` line):

```python
# ADR-035 Phase 2 — independent canary (apps.cli.ops.sentinel --canary).
# Override the pinned index page (defaults to the homepage):
SENTINEL_CANARY_INDEX_URL = 'https://javdb.com/'
# Golden anchors: known-stable detail pages with expected parsed values. Capture
# with `python -m apps.cli.ops.sentinel --capture-anchors --url <detail-url>`,
# then paste the values here. Empty = fill-rate-only canary (still useful).
SENTINEL_CANARY_ANCHORS = [
    # {'url': 'https://javdb.com/v/XXXXX', 'video_code': 'ABC-123',
    #  'title_contains': 'Some Stable Title', 'min_magnets': 3},
]
```

- [ ] **Step 2: Map the canary vars in `config_generator`** — so CI's `VAR_SENTINEL_CANARY_*` reach `config.py`.

Inspect how the generator maps `VAR_*` → config and add the two canary keys following the existing idiom:

Run: `grep -n "SENTINEL\|PROXY_MODE\|def \|VAR_" apps/cli/ops/config_generator.py | head -40`

Add `SENTINEL_CANARY_INDEX_URL` (plain string from `VAR_SENTINEL_CANARY_INDEX_URL`) and `SENTINEL_CANARY_ANCHORS` (JSON-decoded from `VAR_SENTINEL_CANARY_ANCHORS_JSON`, like the existing `*_JSON` proxy vars) in the same place the generator writes other optional settings. If `config_generator` writes only keys it knows, both must be added or the CI `VAR_SENTINEL_CANARY_*` values are silently dropped.

> If the generator already passes through unknown `VAR_*` keys generically, this
> step is a no-op — verify by grepping for a pass-through loop. Either way, the
> canary works without these (they are optional overrides); this step only wires
> the CI override path.

- [ ] **Step 3: Promote *Canary probe* in `CONTEXT.md`** — find the ADR-035 "Canary probe" domain term (added by Phase 1, marked Phase-2) and drop the "(Phase-2)" qualifier, leaving the definition:

> **Canary probe** — the small independent scheduled fetch+parse (`apps.cli.ops.sentinel --canary`, `SiteContractSentinel.yml`) used for between-run drift detection.

- [ ] **Step 4: Document the CLI modes** — in `docs/handbook/en/developer/cli-reference.md`, extend the `apps.cli.ops.sentinel` entry with the two new modes, then mirror into `docs/handbook/zh/developer/cli-reference.md` (translate prose; keep commands/flags verbatim):

```text
python -m apps.cli.ops.sentinel --session-id <id>   # Phase 1: evaluate a run's fills (gate)
python -m apps.cli.ops.sentinel --canary            # Phase 2: independent canary over pinned pages
python -m apps.cli.ops.sentinel --capture-anchors --url <detail-url> [--url ...]
                                                    # print current parsed anchor values (JSON)
# Flags: --run-id, --attempt, --json, --log-level. Exit code 4 = critical drift.
```

- [ ] **Step 5: Full verification gate**

Run:
```bash
pytest tests/unit/test_sentinel_probes.py tests/unit/test_sentinel_canary_incident.py \
       tests/unit/test_sentinel_service.py tests/unit/test_sentinel_detectors.py \
       tests/smoke/test_sentinel_cli.py tests/smoke/test_sentinel_cli_canary.py -v
```
Expected: all PASS.

- [ ] **Step 6: Seam invariant — probes + detector stay read-only/pure**

Run:
```bash
grep -rnE "INSERT|UPDATE|execute\(|upsert" javdb/ops/sentinel/probes.py javdb/ops/sentinel/detectors.py
```
Expected: prints nothing — the canary probe and the detector never write; only `service.run_canary` emits (via the incident repo), exactly like the Phase-1 gate.

- [ ] **Step 7: Commit**

```bash
git add config.py.example apps/cli/ops/config_generator.py CONTEXT.md docs/handbook
git commit -m "docs(sentinel): canary config knobs + CLI reference + CONTEXT (ADR-035 Phase 2)"
```

---

## Plan Self-Review

**Spec coverage (ADR-035 Phase 2 row + D-decisions):**
- Independent canary, fixed page set, parse + observe (D1 source b) → Tasks 1, 2, 4. ✓
- One detector core for both sources (D1) → `run_canary` calls the Phase-1 `evaluate()` verbatim (Task 4). ✓
- Pinned pages + golden anchors (Phase 2 "Ships") → Task 1 (`GoldenAnchor`, `check_golden_anchors`, `run_probes`); capture helper Task 5. ✓
- Between-run detection + lead time → `SiteContractSentinel.yml` cron at 06:00 UTC before the 12:00 daily run (Task 6). ✓
- Baseline from clean committed daily runs (D5) → `run_canary` reads `ParseRunFieldFillRepo.baseline()` (committed-only); canary never writes fills. ✓
- Drift ≠ catastrophe (D4) → fetch failures recorded separately, never raised as drift (Task 1 `run_probes`; tested Task 2). ✓
- Reuse ADR-026 incident surface, `incident_type='site_drift'` (D6) → `build_drift_incident(trigger_source="canary")` (Task 3) emitted by the service (Task 4). ✓
- Module shape `probes.py` + service `run_canary` + CLI + workflow (D7) → Tasks 1, 4, 5, 6. ✓
- Canary stays tiny (non-goal) → one index page + a handful of anchors; documented. ✓

**Type consistency:** `GoldenAnchor`, `ProbeObservation`, `check_golden_anchors`, `run_probes`, `run_canary`, and the reused `FieldFill`/`DriftFinding`/`SentinelVerdict`/`SentinelOptions`/`evaluate`/`build_drift_incident`/`_fill_ctx`/`_incident_ctx` are used identically across Tasks 1–6. `build_drift_incident` gains an optional `trigger_source` (default preserves Phase 1). ✓

**Placeholder scan:** No invented javdb video codes/titles — `DEFAULT_GOLDEN_ANCHORS` is empty by design and anchors are operator-captured (Task 5 helper + `config.py.example` commented example). The workflow env block is concrete; Task 6 Step 2 reconciles it against `DailyIngestion.yml` (the maintained contract) rather than guessing. ✓

**Integration points needing in-file location (grep provided, not blind line numbers):**
Task 3 (persistence.py `build_drift_incident` header — exact text given), Task 4 (append to `service.py`; imports already present), Task 5 (full-file replacement of the small CLI), Task 6 Step 2 (`DailyIngestion.yml` env block via `sed`), Task 7 Steps 2–4 (`config_generator` mapping + `CONTEXT.md` term + cli-reference). Each gives the exact target plus a grep/sed to confirm.

**Known coupling (resolved):** the canary reuses the Phase-1 `evaluate()` core and `ParseRunFieldFillRepo.baseline()` unchanged — no detector or schema change. The only Phase-1 file edited is `persistence.py` (additive `trigger_source` param, default-compatible) and `service.py`/`__init__.py` (additive `run_canary`). Phase-1 tests (`test_sentinel_service.py`, `test_sentinel_cli.py`) are re-run as regressions in Tasks 3 and 5.

**Open verification dependency:** `config_generator`'s `VAR_*` contract (Task 6 Step 2 / Task 7 Step 2) is confirmed in-repo against `DailyIngestion.yml` before merge; no production D1 schema change is introduced by Phase 2 (it reads the Phase-1 `ParseRunFieldFill` baseline and writes only `OpsIncidents`, both already live in `javdb-reports`).
