"""Fan-out indexer dispatch with per-source failure isolation (ADR-039 / ADR-054 WS3).

Importing this module registers the built-in plugins (javbus, sukebei).
"""

from __future__ import annotations

import concurrent.futures
import threading
import time

from javdb.infra.config import cfg
from javdb.integrations.indexer.plugin import IndexerResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.indexer.javbus.plugin  # noqa: F401,E402
import javdb.integrations.indexer.sukebei.plugin  # noqa: F401,E402

# Discover any third-party indexer plugins installed as entry points.
REGISTRY.discover_entry_points("javdb.indexer_plugins")

DEFAULT_SOURCE_TIMEOUT_SECONDS = 10.0

# Per-source bounded pools. The previous implementation spawned a fresh daemon
# thread per source per request and abandoned it on timeout; the underlying
# network I/O kept running, so repeated calls against a hung source (a proxy
# that accepts TCP but never replies) accumulated threads and file descriptors
# without bound. A single shared pool would bound threads but let one hung
# source occupy every slot and starve the others — breaking the per-source
# failure isolation this module promises. Each source instead gets its own small
# bounded pool: a hung source can occupy at most ``_MAX_WORKERS_PER_SOURCE`` of
# ITS pool's threads (excess work queues there, no thread leak) and can never
# block a healthy source.
_MAX_WORKERS_PER_SOURCE = 4
_executors: dict[str, concurrent.futures.ThreadPoolExecutor] = {}
_executors_lock = threading.Lock()


def _executor_for(source: str) -> concurrent.futures.ThreadPoolExecutor:
    with _executors_lock:
        executor = _executors.get(source)
        if executor is None:
            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=_MAX_WORKERS_PER_SOURCE,
                thread_name_prefix=f"indexer-{source}",
            )
            _executors[source] = executor
        return executor


def active_sources() -> list[str]:
    val = cfg("MAGNET_SOURCES", [])
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    if isinstance(val, (list, tuple)):
        return [v.strip() for v in val if isinstance(v, str) and v.strip()]
    return []


def source_timeout_seconds() -> float:
    val = cfg("MAGNET_SOURCE_TIMEOUT_SECONDS", DEFAULT_SOURCE_TIMEOUT_SECONDS)
    try:
        timeout = float(val)
    except (TypeError, ValueError):
        return DEFAULT_SOURCE_TIMEOUT_SECONDS
    if timeout <= 0:
        return DEFAULT_SOURCE_TIMEOUT_SECONDS
    return timeout


def aggregate(video_code: str) -> list[IndexerResult]:
    """Query every active source for ``video_code`` with per-source isolation."""
    results: list[IndexerResult] = []
    timeout = source_timeout_seconds()
    pending: list[tuple[str, concurrent.futures.Future]] = []
    for name in active_sources():
        plugin = REGISTRY.get("indexer", name)
        if plugin is None:
            results.append(IndexerResult(source=name, ok=False, detail="not registered"))
            continue
        try:
            if not plugin.is_configured():
                results.append(IndexerResult(source=name, ok=False, detail="not configured"))
                continue
            pending.append((name, _executor_for(name).submit(plugin.search, video_code)))
        except Exception as exc:  # failure isolation includes is_configured errors
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
    deadline = time.monotonic() + timeout
    for name, future in pending:
        try:
            results.append(future.result(timeout=max(0.0, deadline - time.monotonic())))
        except concurrent.futures.TimeoutError:
            # Free the slot if the task has not started; a running task keeps
            # going on its pool thread until the HTTP read timeout unblocks it,
            # but the pool is bounded so no thread leaks.
            future.cancel()
            results.append(
                IndexerResult(source=name, ok=False, detail=f"timeout after {timeout:g}s")
            )
        except Exception as exc:  # preserve per-source failure isolation
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
    return results
