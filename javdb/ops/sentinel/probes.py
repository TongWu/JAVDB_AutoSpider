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
