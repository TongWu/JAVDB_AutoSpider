"""Deprecated legacy spider implementation, preserved for rollback only.

The canonical spider lives in :mod:`javdb.spider`. This package retains the
pre-Phase-1 implementation as a historical reference and emergency rollback
artefact; it is not imported by any production code path or test, and its
internal imports have been retargeted to the post-ADR-007 canonical paths
so it still parses cleanly under modern layout.
"""
