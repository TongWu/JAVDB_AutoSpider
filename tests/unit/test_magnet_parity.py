"""Parity tests: Rust vs Python magnet extraction must produce identical output.

Skipped automatically when the Rust extension is not installed.
"""

import pytest

from packages.python.javdb_core.magnet_extractor import (
    RUST_MAGNET_AVAILABLE,
    _python_extract_magnets,
)

if RUST_MAGNET_AVAILABLE:
    from javdb.rust_core import extract_magnets as _rust_extract_magnets

pytestmark = pytest.mark.skipif(
    not RUST_MAGNET_AVAILABLE,
    reason="javdb_rust_core not installed",
)

CATEGORY_KEYS = (
    "subtitle", "hacked_subtitle", "hacked_no_subtitle", "no_subtitle",
    "size_subtitle", "size_hacked_subtitle", "size_hacked_no_subtitle", "size_no_subtitle",
)

FIXTURES = [
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:sub1", "name": "ABC-123.mp4", "tags": ["字幕"], "size": "3.5GB", "timestamp": "2026-01-01"},
            {"href": "magnet:?xt=urn:btih:nosub1", "name": "ABC-123.mp4", "tags": ["高清"], "size": "2.1GB", "timestamp": "2026-01-01"},
        ],
        id="subtitle-vs-nosub",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:hacked1", "name": "DEF-456-UC.mp4", "tags": [], "size": "4.0GB", "timestamp": "2026-01-02"},
        ],
        id="hacked-subtitle-only",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:hack_nosub", "name": "GHI-789-U.mp4", "tags": [], "size": "1.8GB", "timestamp": "2026-01-03"},
        ],
        id="hacked-no-subtitle",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:4k1", "name": "JKL-012.4k.mp4", "tags": ["4K"], "size": "8.5GB", "timestamp": "2026-02-01"},
            {"href": "magnet:?xt=urn:btih:normal1", "name": "JKL-012.mp4", "tags": [], "size": "2.0GB", "timestamp": "2026-02-01"},
        ],
        id="4k-preferred-over-normal",
    ),
    pytest.param(
        [],
        id="empty-list",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:sub2", "name": "MNO-345-C.无码破解.mp4", "tags": ["字幕"], "size": "5.0GB", "timestamp": "2026-03-01"},
            {"href": "magnet:?xt=urn:btih:sub3", "name": "MNO-345.mp4", "tags": ["字幕"], "size": "3.0GB", "timestamp": "2026-03-01"},
        ],
        id="subtitle-excludes-cracked",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:uc1", "name": "PQR-678-UC.mp4", "tags": [], "size": "4.5GB", "timestamp": "2026-04-01"},
            {"href": "magnet:?xt=urn:btih:cu1", "name": "PQR-678-CU.mp4", "tags": [], "size": "4.4GB", "timestamp": "2026-04-01"},
            {"href": "magnet:?xt=urn:btih:u1", "name": "PQR-678-U.mp4", "tags": [], "size": "4.3GB", "timestamp": "2026-04-01"},
        ],
        id="hacked-priority-UC-over-U",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:all1", "name": "STU-901-UC.mp4", "tags": ["字幕"], "size": "6.0GB", "timestamp": "2026-05-01"},
            {"href": "magnet:?xt=urn:btih:all2", "name": "STU-901.mp4", "tags": ["字幕"], "size": "3.5GB", "timestamp": "2026-05-01"},
            {"href": "magnet:?xt=urn:btih:all3", "name": "STU-901-U.mp4", "tags": [], "size": "4.0GB", "timestamp": "2026-05-01"},
            {"href": "magnet:?xt=urn:btih:all4", "name": "STU-901.mp4", "tags": [], "size": "2.0GB", "timestamp": "2026-05-01"},
        ],
        id="all-categories-populated",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:big1", "name": "VWX-234.mp4", "tags": [], "size": "12.3GB", "timestamp": "2026-06-01"},
            {"href": "magnet:?xt=urn:btih:big2", "name": "VWX-234.mp4", "tags": [], "size": "2.1GB", "timestamp": "2026-06-02"},
        ],
        id="same-name-different-size-timestamp",
    ),
    pytest.param(
        [
            {"href": "magnet:?xt=urn:btih:fc1", "name": "YZA-567.mp4", "tags": [], "size": "1.5GB", "timestamp": "2026-07-01", "file_count": 3},
        ],
        id="with-file-count",
    ),
]


@pytest.mark.parametrize("magnets", FIXTURES)
def test_rust_python_parity(magnets):
    """Rust and Python extract_magnets must agree on the category keys."""
    py = _python_extract_magnets(magnets)
    rs = _rust_extract_magnets(magnets)
    for key in CATEGORY_KEYS:
        assert py.get(key, "") == rs.get(key, ""), (
            f"Mismatch on {key!r}: python={py.get(key)!r}, rust={rs.get(key)!r}"
        )
