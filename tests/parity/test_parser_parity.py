"""Drift detection between javdb.rust_core parsers and the Python fallbacks.

The Python parsers in ``apps/api/parsers/{detail,index,tag}_parser.py`` are
FROZEN (see the file headers). They exist only as fallbacks for environments
where the Rust wheel is unavailable. New parsing logic must go into the Rust
crate. This test asserts that on a corpus of real HTML fixtures, both
implementations produce equivalent output. A failure here means either:

1. Someone modified a Python parser without porting the change to Rust, or
2. The Rust crate diverged from the Python reference behavior.

Either way, the fix is to align the two — not to silence this test.

Skipped cleanly if the Rust extension is not installed; that is a separate
concern handled by build-rust-extension CI.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from typing import Any

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

HTML_DIR = os.path.join(REPO_ROOT, "html")

rust_core = pytest.importorskip(
    "javdb.rust_core",
    reason="Rust extension not installed — parity test requires both implementations.",
)

from apps.api.parsers.detail_parser import parse_detail_page as py_parse_detail
from apps.api.parsers.index_parser import parse_index_page as py_parse_index
from apps.api.parsers.tag_parser import parse_tag_page as py_parse_tag

rust_parse_index = rust_core.parse_index_page
rust_parse_detail = rust_core.parse_detail_page
rust_parse_tag = rust_core.parse_tag_page


def _load(filename: str) -> str:
    path = os.path.join(HTML_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"HTML fixture missing: {filename}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


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


@pytest.mark.parametrize("fixture", INDEX_FIXTURES)
def test_index_page_parity(fixture: str) -> None:
    html = _load(fixture)
    rust_out = _canonical(rust_parse_index(html, 1))
    py_out = _canonical(py_parse_index(html, 1))
    diffs = _diff(rust_out, py_out, path=fixture)
    assert not diffs, "Rust/Python index parser drift:\n" + "\n".join(diffs)


@pytest.mark.parametrize("fixture", DETAIL_FIXTURES)
def test_detail_page_parity(fixture: str) -> None:
    html = _load(fixture)
    rust_out = _canonical(rust_parse_detail(html))
    py_out = _canonical(py_parse_detail(html))
    diffs = _diff(rust_out, py_out, path=fixture)
    assert not diffs, "Rust/Python detail parser drift:\n" + "\n".join(diffs)


@pytest.mark.parametrize("fixture", TAG_FIXTURES)
def test_tag_page_parity(fixture: str) -> None:
    html = _load(fixture)
    rust_out = _canonical(rust_parse_tag(html, 1))
    py_out = _canonical(py_parse_tag(html, 1))
    diffs = _diff(rust_out, py_out, path=fixture)
    assert not diffs, "Rust/Python tag parser drift:\n" + "\n".join(diffs)
