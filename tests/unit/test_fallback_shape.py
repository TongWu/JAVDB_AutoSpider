"""Shape/smoke tests for the Best-Effort pure-Python fallbacks (ADR-041 D2/D3).

These replace the retired value-parity suites (``tests/parity/test_parser_parity.py``
and ``tests/unit/test_magnet_parity.py``). Per ADR-041 the Python fallbacks for
parsers / magnet / url_helper / masking are **best-effort**, not byte-equal to
Rust — so we assert only the *shape* a caller depends on (the same accessors /
keys the Rust objects expose, ADR-020 D2's ``get_magnets_as_legacy()`` being the
canonical example) and that the fallback is **loud** (one WARNING, ADR-041 D3).

The fallbacks are imported directly from ``javdb.parsing.fallback.*`` etc. so the
Rust wheel is never exercised here regardless of whether it is installed.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from contextlib import contextmanager

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PARSER_FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "parser")


def _fixture(name: str) -> str:
    with open(os.path.join(PARSER_FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Shape: parsers (Python fallback returns the contracted domain-object shape)
# ---------------------------------------------------------------------------

def test_index_fallback_shape():
    from javdb.parsing.fallback.index_parser import parse_index_page

    result = parse_index_page(_fixture("index_edge_cases.html"))

    # IndexPageResult shape
    assert hasattr(result, "has_movie_list")
    assert isinstance(result.movies, list)
    assert hasattr(result, "page_title")
    # MovieIndexEntry shape (the keys callers read)
    for entry in result.movies:
        assert hasattr(entry, "href")
        assert hasattr(entry, "video_code")
        assert hasattr(entry, "title")


def test_detail_fallback_shape():
    from javdb.parsing.fallback.detail_parser import parse_detail_page

    detail = parse_detail_page(_fixture("detail_actor_edge_cases.html"))

    # MovieDetail shape + the uniform accessor relied on across Rust/Python (ADR-020 D2)
    assert hasattr(detail, "video_code")
    assert hasattr(detail, "parse_success")
    assert callable(getattr(detail, "get_magnets_as_legacy", None))
    assert isinstance(detail.get_magnets_as_legacy(), list)


# ---------------------------------------------------------------------------
# Shape: magnet categorisation (Python branch returns the four buckets + counterparts)
# ---------------------------------------------------------------------------

_BASE_MAGNET_KEYS = ("subtitle", "hacked_subtitle", "hacked_no_subtitle", "no_subtitle")


def test_magnet_python_categorize_shape():
    from javdb.parsing.magnet_categorize import _python_categorize

    out = _python_categorize(
        [
            {"href": "magnet:?xt=urn:btih:sub1", "name": "ABC-123.mp4",
             "tags": ["字幕"], "size": "3.5GB", "timestamp": "2026-01-01"},
            {"href": "magnet:?xt=urn:btih:nosub1", "name": "ABC-123.mp4",
             "tags": [], "size": "2.1GB", "timestamp": "2026-01-01"},
        ]
    )

    assert isinstance(out, dict)
    for key in _BASE_MAGNET_KEYS:
        assert key in out
        # each bucket carries its size_/file_count_/resolution_ counterparts
        assert f"size_{key}" in out
        assert f"file_count_{key}" in out
        assert f"resolution_{key}" in out


def test_magnet_empty_list_shape():
    from javdb.parsing.magnet_categorize import _python_categorize

    out = _python_categorize([])
    assert isinstance(out, dict)
    for key in _BASE_MAGNET_KEYS:
        assert key in out


# ---------------------------------------------------------------------------
# Loudness: every Best-Effort fallback logs exactly one WARNING (ADR-041 D3)
# ---------------------------------------------------------------------------

@contextmanager
def _rust_blocked():
    """Force ``from javdb.rust_core import ...`` to raise ImportError, then restore."""
    sentinel = object()
    saved = sys.modules.get("javdb.rust_core", sentinel)
    sys.modules["javdb.rust_core"] = None  # None → ImportError on import
    try:
        yield
    finally:
        if saved is sentinel:
            sys.modules.pop("javdb.rust_core", None)
        else:
            sys.modules["javdb.rust_core"] = saved


@pytest.mark.parametrize("module_name", [
    "javdb.parsing",
    "javdb.parsing.magnet_categorize",
    "javdb.spider.url_helper",
    "javdb.infra.masking",
])
def test_best_effort_fallback_is_loud(module_name, caplog):
    module = importlib.import_module(module_name)
    try:
        with _rust_blocked(), caplog.at_level(logging.WARNING):
            importlib.reload(module)
        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING and "best-effort" in r.getMessage()]
        assert warnings, f"{module_name} fallback did not log a best-effort WARNING"
    finally:
        # Restore the Rust-backed module for the rest of the suite.
        importlib.reload(module)
