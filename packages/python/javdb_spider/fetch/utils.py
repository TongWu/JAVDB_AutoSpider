"""Shared utilities for the fetch package (W3.5).

This module is intentionally tiny — it exists to give the duplicated
``_task_worker_ctx`` helper a single home, so ``fetch_engine`` and
``login_coordinator`` can stop maintaining identical copies. Future
fetch-layer pure helpers that need to be shared across both modules
belong here as well.
"""

from __future__ import annotations


def task_worker_ctx(entry_index: str, worker_name: str) -> str:
    """Unified task log prefix: entry first, then worker.

    Used wherever a fetch-layer log line needs to identify both the
    in-flight task (``entry_index``) and the worker / proxy executing
    it. Centralised so the bracket format never drifts between
    ``fetch_engine`` and ``login_coordinator`` (each call-site routes
    through this function).
    """
    return f"[{entry_index}][worker={worker_name}]"
