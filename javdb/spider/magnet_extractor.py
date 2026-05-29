"""Magnet link extraction and categorisation (thin re-export).

The categorisation algorithm — including the Rust-first dispatch with a
pure-Python fallback — now lives in :mod:`javdb.parsing.magnet_categorize`
(see ADR-020 Phase 1). This module remains as a thin compatibility shim so
existing callers (``parallel_mode``, ``detail/runner``, legacy spider,
migration tools, pipeline policies, dedup) keep importing the same public
names from ``javdb.spider.magnet_extractor``.
"""

from javdb.parsing.magnet_categorize import (
    RUST_MAGNET_AVAILABLE,
    _parse_size,
    _python_categorize as _python_extract_magnets,
    _sort_key,
    categorize as extract_magnets,
    infer_resolution,
)

__all__ = [
    'extract_magnets',
    'infer_resolution',
    '_parse_size',
    '_sort_key',
    '_python_extract_magnets',
    'RUST_MAGNET_AVAILABLE',
]
