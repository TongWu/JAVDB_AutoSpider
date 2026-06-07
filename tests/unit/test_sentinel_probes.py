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
