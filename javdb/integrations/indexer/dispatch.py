"""Fan-out indexer dispatch with per-source failure isolation (ADR-039 / ADR-054 WS3).

Importing this module registers the built-in plugins (javbus, sukebei).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from javdb.infra.config import cfg
from javdb.integrations.indexer.plugin import IndexerResult
from javdb.integrations.plugins.registry import REGISTRY

# Trigger built-in plugin self-registration.
import javdb.integrations.indexer.javbus.plugin  # noqa: F401,E402
import javdb.integrations.indexer.sukebei.plugin  # noqa: F401,E402

# Discover any third-party indexer plugins installed as entry points.
REGISTRY.discover_entry_points("javdb.indexer_plugins")

DEFAULT_SOURCE_TIMEOUT_SECONDS = 10.0

# Per-source bounded concurrency on DAEMON worker threads. Two failure modes
# shape this design:
#   1. Thread leak (issue #225): the original code spawned a fresh thread per
#      request and abandoned it on timeout; the underlying network read kept
#      running, so repeated calls against a hung source (a proxy that accepts
#      TCP but never replies) accumulated threads/fds without bound.
#   2. Hang at interpreter exit: a concurrent.futures.ThreadPoolExecutor (the
#      previous fix for #225) creates NON-daemon workers that the futures
#      atexit handler (_python_exit) joins unconditionally on shutdown — a
#      worker stuck in a hung read blocks the whole process from exiting.
# Both are solved by giving each source its own BoundedSemaphore of
# ``_MAX_WORKERS_PER_SOURCE`` permits and running each search on a manually
# created DAEMON thread: the permits cap the thread count per source (no leak;
# once they are exhausted further calls fail fast with "busy" rather than queue
# work or spawn an unbounded thread), and daemon threads are abandoned at exit
# rather than joined (a hung source can never wedge process shutdown). A hung
# source can occupy at most its own permits and never starves a healthy source.
_MAX_WORKERS_PER_SOURCE = 4
_source_slots: dict[str, threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()


def _slot_for(source: str) -> threading.BoundedSemaphore:
    with _slots_lock:
        slot = _source_slots.get(source)
        if slot is None:
            slot = threading.BoundedSemaphore(_MAX_WORKERS_PER_SOURCE)
            _source_slots[source] = slot
        return slot


class _SourceTask:
    """A single source's in-flight search running on a daemon worker thread."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.done = threading.Event()
        self.result: Optional[IndexerResult] = None
        self.error: Optional[Exception] = None


def _run_search(plugin, video_code: str, task: _SourceTask, slot: threading.BoundedSemaphore) -> None:
    try:
        task.result = plugin.search(video_code)
    except Exception as exc:  # isolate any failure to this source
        task.error = exc
    finally:
        task.done.set()
        slot.release()


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
    pending: list[_SourceTask] = []
    for name in active_sources():
        plugin = REGISTRY.get("indexer", name)
        if plugin is None:
            results.append(IndexerResult(source=name, ok=False, detail="not registered"))
            continue
        try:
            configured = plugin.is_configured()
        except Exception as exc:  # failure isolation includes is_configured errors
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
            continue
        if not configured:
            results.append(IndexerResult(source=name, ok=False, detail="not configured"))
            continue
        slot = _slot_for(name)
        if not slot.acquire(blocking=False):
            # Every worker permit for this source is still held by an earlier
            # search that has not returned (a hung source). Fail fast instead of
            # queueing work or spawning an unbounded thread (issue #225).
            results.append(
                IndexerResult(source=name, ok=False, detail="busy (all workers in use)")
            )
            continue
        task = _SourceTask(name)
        worker = threading.Thread(
            target=_run_search,
            args=(plugin, video_code, task, slot),
            name=f"indexer-{name}",
            daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:  # OS refused a new thread; release the permit
            slot.release()
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
            continue
        pending.append(task)
    deadline = time.monotonic() + timeout
    for task in pending:
        if task.done.wait(timeout=max(0.0, deadline - time.monotonic())):
            if task.error is not None:
                results.append(
                    IndexerResult(source=task.name, ok=False, detail=f"error: {task.error}")
                )
            else:
                results.append(task.result)
        else:
            # The daemon worker keeps running until its network read unblocks,
            # then releases its permit; it never blocks interpreter exit.
            results.append(
                IndexerResult(source=task.name, ok=False, detail=f"timeout after {timeout:g}s")
            )
    return results
