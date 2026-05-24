"""Parser behavior snapshots and optional Rust/Python drift detection.

The Python parsers in ``javdb/parsing/fallback/{detail,index,tag}_parser.py`` are
FROZEN (see the file headers). They exist only as fallbacks for environments
where the Rust wheel is unavailable. New parsing logic must go into the Rust
crate.

This module has two related checks:

1. Golden snapshots for the pure-Python fallback output. These are deterministic
   in both Rust and no-Rust environments and protect ADR-011 Phase 1 moves.
2. Optional Rust/Python parity checks. These import ``javdb.rust_core`` inside
   each test and skip cleanly when the Rust wheel is not installed.

For parity failures, either a Python fallback changed without a Rust port or
the Rust crate diverged from the Python reference behavior. Known existing
drift is marked with a precise xfail reason rather than fixed in this task.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from typing import Any

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

HTML_DIR = os.path.join(REPO_ROOT, "html")
PARSER_FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "parser")
PARSER_GOLDEN_DIR = os.path.join(PARSER_FIXTURE_DIR, "golden")

from javdb.parsing.fallback.detail_parser import parse_detail_page as py_parse_detail
from javdb.parsing.fallback.index_parser import parse_category_page as py_parse_category
from javdb.parsing.fallback.index_parser import parse_index_page as py_parse_index
from javdb.parsing.fallback.index_parser import parse_top_page as py_parse_top
from javdb.parsing.fallback.tag_parser import parse_tag_page as py_parse_tag


def _load(filename: str) -> str:
    path = os.path.join(HTML_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"HTML fixture missing: {filename}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_parser_fixture(filename: str) -> str:
    path = os.path.join(PARSER_FIXTURE_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_golden(filename: str) -> Any:
    path = os.path.join(PARSER_GOLDEN_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _canonical(obj: Any) -> Any:
    """Reduce a dataclass or pyo3 #[pyclass] to JSON-comparable primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if dataclasses.is_dataclass(obj):
        return _canonical(dataclasses.asdict(obj))
    out: dict[str, Any] = {}
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if callable(val):
            continue
        out[name] = _canonical(val)
    return out


def _diff(rust: Any, py: Any, path: str = "") -> list[str]:
    """Walk two canonical structures and return human-readable mismatch lines."""
    if isinstance(rust, dict) and isinstance(py, dict):
        diffs: list[str] = []
        for k in sorted(set(rust) | set(py)):
            if k not in rust:
                diffs.append(f"{path}.{k}: missing in rust, py={py[k]!r}")
            elif k not in py:
                diffs.append(f"{path}.{k}: missing in py, rust={rust[k]!r}")
            else:
                diffs.extend(_diff(rust[k], py[k], f"{path}.{k}"))
        return diffs
    if isinstance(rust, list) and isinstance(py, list):
        if len(rust) != len(py):
            return [f"{path}: list length rust={len(rust)} py={len(py)}"]
        diffs = []
        for i, (r, p) in enumerate(zip(rust, py)):
            diffs.extend(_diff(r, p, f"{path}[{i}]"))
        return diffs
    if rust != py:
        return [f"{path}: rust={rust!r} py={py!r}"]
    return []


INDEX_FIXTURES = ["JavDB-normal_index-page1.html"]
DETAIL_FIXTURES = [
    "detail_page_AVSW-067_EN.html",
    "detailed_page_VDD-201.html",
]
TAG_FIXTURES = [
    "tag_单体作品.html",
    "tag_单体作品&捆绑&VR.html",
    "tag_2026&淫乱真实&单体作品&多P&捆绑.html",
]

GOLDEN_CASES = [
    (
        "index_edge_cases",
        "index_edge_cases.html",
        lambda html: py_parse_index(html, 2),
    ),
    (
        "category_actor_edge_cases",
        "category_actor_edge_cases.html",
        lambda html: py_parse_category(html, 3),
    ),
    (
        "top_edge_cases",
        "top_edge_cases.html",
        lambda html: py_parse_top(html, 1),
    ),
    (
        "tag_edge_cases",
        "tag_edge_cases.html",
        lambda html: py_parse_tag(html, 4),
    ),
    (
        "detail_actor_edge_cases",
        "detail_actor_edge_cases.html",
        py_parse_detail,
    ),
    (
        "detail_no_actor_sentinel",
        "detail_no_actor_sentinel.html",
        py_parse_detail,
    ),
    (
        "detail_missing_actors",
        "detail_missing_actors.html",
        py_parse_detail,
    ),
]

EDGE_PARITY_CASES = [
    (
        "index_edge_cases",
        "index_edge_cases.html",
        lambda rust_core, html: rust_core.parse_index_page(html, 2),
        lambda html: py_parse_index(html, 2),
    ),
    (
        "category_actor_edge_cases",
        "category_actor_edge_cases.html",
        lambda rust_core, html: rust_core.parse_category_page(html, 3),
        lambda html: py_parse_category(html, 3),
    ),
    (
        "top_edge_cases",
        "top_edge_cases.html",
        lambda rust_core, html: rust_core.parse_top_page(html, 1),
        lambda html: py_parse_top(html, 1),
    ),
    (
        "tag_edge_cases",
        "tag_edge_cases.html",
        lambda rust_core, html: rust_core.parse_tag_page(html, 4),
        lambda html: py_parse_tag(html, 4),
    ),
    (
        "detail_actor_edge_cases",
        "detail_actor_edge_cases.html",
        lambda rust_core, html: rust_core.parse_detail_page(html),
        py_parse_detail,
    ),
    (
        "detail_no_actor_sentinel",
        "detail_no_actor_sentinel.html",
        lambda rust_core, html: rust_core.parse_detail_page(html),
        py_parse_detail,
    ),
    (
        "detail_missing_actors",
        "detail_missing_actors.html",
        lambda rust_core, html: rust_core.parse_detail_page(html),
        py_parse_detail,
    ),
]

EXPECTED_EDGE_PARITY_DRIFTS = {
    "detail_actor_edge_cases": {
        "detail_actor_edge_cases.magnets[0].size: rust='' py='1.25GB'",
    },
    "detail_missing_actors": {
        "detail_missing_actors.magnets[0].size: rust='' py='750MB'",
    },
}


@pytest.mark.parametrize(("case_name", "fixture", "parser"), GOLDEN_CASES)
def test_python_fallback_parser_output_matches_golden(
    case_name: str,
    fixture: str,
    parser,
) -> None:
    html = _load_parser_fixture(fixture)
    expected = _load_golden(f"{case_name}.json")
    assert _canonical(parser(html)) == expected


def test_edge_case_fixtures_cover_current_model_helpers() -> None:
    detail = py_parse_detail(_load_parser_fixture("detail_actor_edge_cases.html"))
    assert detail.get_first_actor_href() == "/actors/lead"
    assert json.loads(detail.get_supporting_actors_json()) == [
        {
            "name": "Support Actor",
            "gender": "male",
            "link": "/actors/support",
        }
    ]

    sentinel = py_parse_detail(_load_parser_fixture("detail_no_actor_sentinel.html"))
    assert sentinel.no_actor_listing is True
    assert sentinel.get_first_actor_name() == "N/A"
    assert sentinel.get_first_actor_gender() == "N/A"
    assert sentinel.get_supporting_actors_json() == "[]"

    missing = py_parse_detail(_load_parser_fixture("detail_missing_actors.html"))
    assert missing.no_actor_listing is False
    assert missing.get_first_actor_name() == ""
    assert missing.get_supporting_actors_json() == "[]"


def test_edge_case_listing_fixture_covers_no_code_and_score_fallbacks() -> None:
    index_result = py_parse_index(_load_parser_fixture("index_edge_cases.html"), 2)
    assert [movie.video_code for movie in index_result.movies] == [
        "ABC-123",
        "REL-456",
        "",
    ]
    assert index_result.movies[1].rate == ""
    assert index_result.movies[1].comment_count == ""
    assert index_result.movies[0].tags == ["含中字磁鏈", "今日新種"]
    assert index_result.movies[1].tags == ["含磁鏈"]

    category_result = py_parse_category(_load_parser_fixture("category_actor_edge_cases.html"), 3)
    assert category_result.category_type == "actors"
    assert category_result.category_name == "Actor Edge"
    assert category_result.movies[0].rate == "3.5"
    assert category_result.movies[0].comment_count == ""
    assert category_result.movies[1].video_code == ""


def test_edge_case_tag_fixture_captures_selection_and_movie_output() -> None:
    result = py_parse_tag(_load_parser_fixture("tag_edge_cases.html"), 4)
    assert result.current_selections == {"10": "2", "7": "28,212", "9": "lt-45"}
    assert result.get_category_by_id("10").get_selected()[0].name == "含字幕"
    assert result.get_category_by_id("10").get_selected()[0].tag_id == "2"
    assert result.get_category_by_id("7").get_id_to_name_map() == {
        "28": "單體作品",
        "80": "首次亮相",
        "212": "VR",
    }
    assert result.get_category_by_id("4").get_id_to_name_map() == {
        "15": "熟女",
        "17": "巨乳",
    }
    assert result.movies[0].tags == ["含中字磁鏈", "今日新種"]


def test_edge_case_top_fixture_captures_period_rank_and_invalid_score() -> None:
    result = py_parse_top(_load_parser_fixture("top_edge_cases.html"), 1)
    assert result.top_type == "top250"
    assert result.period == "2026"
    assert result.movies[0].ranking == 1
    assert result.movies[1].ranking is None
    assert result.movies[1].rate == ""
    assert result.movies[1].comment_count == ""


@pytest.mark.parametrize(("case_name", "fixture", "rust_parser", "py_parser"), EDGE_PARITY_CASES)
def test_edge_fixture_rust_python_parity(
    case_name: str,
    fixture: str,
    rust_parser,
    py_parser,
) -> None:
    rust_core = pytest.importorskip(
        "javdb.rust_core",
        reason="Rust extension not installed — parity test requires both implementations.",
    )
    html = _load_parser_fixture(fixture)
    rust_out = _canonical(rust_parser(rust_core, html))
    py_out = _canonical(py_parser(html))
    diffs = _diff(rust_out, py_out, path=case_name)
    expected_drift = EXPECTED_EDGE_PARITY_DRIFTS.get(case_name)
    if expected_drift is not None:
        actual_drift = set(diffs)
        if actual_drift == expected_drift:
            pytest.xfail(
                "known Rust/Python magnet size drift: "
                + "; ".join(sorted(expected_drift))
            )
        assert not diffs, (
            "Unexpected Rust/Python edge parser drift.\n"
            "Expected only:\n"
            + "\n".join(sorted(expected_drift))
            + "\nActual:\n"
            + "\n".join(diffs)
        )
    assert not diffs, "Rust/Python edge parser drift:\n" + "\n".join(diffs)


@pytest.mark.parametrize("fixture", INDEX_FIXTURES)
def test_index_page_parity(fixture: str) -> None:
    rust_core = pytest.importorskip(
        "javdb.rust_core",
        reason="Rust extension not installed — parity test requires both implementations.",
    )
    html = _load(fixture)
    rust_out = _canonical(rust_core.parse_index_page(html, 1))
    py_out = _canonical(py_parse_index(html, 1))
    diffs = _diff(rust_out, py_out, path=fixture)
    assert not diffs, "Rust/Python index parser drift:\n" + "\n".join(diffs)


@pytest.mark.parametrize("fixture", DETAIL_FIXTURES)
def test_detail_page_parity(fixture: str) -> None:
    rust_core = pytest.importorskip(
        "javdb.rust_core",
        reason="Rust extension not installed — parity test requires both implementations.",
    )
    html = _load(fixture)
    rust_out = _canonical(rust_core.parse_detail_page(html))
    py_out = _canonical(py_parse_detail(html))
    diffs = _diff(rust_out, py_out, path=fixture)
    assert not diffs, "Rust/Python detail parser drift:\n" + "\n".join(diffs)


@pytest.mark.parametrize("fixture", TAG_FIXTURES)
def test_tag_page_parity(fixture: str) -> None:
    rust_core = pytest.importorskip(
        "javdb.rust_core",
        reason="Rust extension not installed — parity test requires both implementations.",
    )
    html = _load(fixture)
    rust_out = _canonical(rust_core.parse_tag_page(html, 1))
    py_out = _canonical(py_parse_tag(html, 1))
    diffs = _diff(rust_out, py_out, path=fixture)
    assert not diffs, "Rust/Python tag parser drift:\n" + "\n".join(diffs)
