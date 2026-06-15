"""Fan-out indexer dispatch with per-source failure isolation (ADR-039 / ADR-054 WS3).

Importing this module registers the built-in plugins (javbus, sukebei).
"""

from __future__ import annotations

import queue
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


def _start_search(
    plugin,
    video_code: str,
    timeout: float,
) -> tuple[queue.Queue[tuple[float, IndexerResult | Exception]], float]:
    done: queue.Queue[tuple[float, IndexerResult | Exception]] = queue.Queue(maxsize=1)
    deadline = time.monotonic() + timeout

    def _run() -> None:
        try:
            result = plugin.search(video_code)
        except Exception as exc:  # preserve existing per-source isolation
            result = exc
        done.put((time.monotonic(), result))

    worker = threading.Thread(target=_run, name=f"indexer-{plugin.name}", daemon=True)
    worker.start()
    return done, deadline


def _finish_search(
    plugin,
    done: queue.Queue[tuple[float, IndexerResult | Exception]],
    deadline: float,
    timeout: float,
) -> IndexerResult:
    try:
        finished_at, result = done.get(timeout=max(0.0, deadline - time.monotonic()))
    except queue.Empty:
        return IndexerResult(
            source=plugin.name,
            ok=False,
            detail=f"timeout after {timeout:g}s",
        )
    if finished_at > deadline:
        return IndexerResult(
            source=plugin.name,
            ok=False,
            detail=f"timeout after {timeout:g}s",
        )
    if isinstance(result, Exception):
        raise result
    return result


def aggregate(video_code: str) -> list[IndexerResult]:
    """Query every active source for ``video_code`` with per-source isolation."""
    results: list[IndexerResult] = []
    timeout = source_timeout_seconds()
    pending: list[
        tuple[str, object, queue.Queue[tuple[float, IndexerResult | Exception]], float]
    ] = []
    for name in active_sources():
        plugin = REGISTRY.get("indexer", name)
        if plugin is None:
            results.append(IndexerResult(source=name, ok=False, detail="not registered"))
            continue
        try:
            if not plugin.is_configured():
                results.append(IndexerResult(source=name, ok=False, detail="not configured"))
                continue
            done, deadline = _start_search(plugin, video_code, timeout)
            pending.append((name, plugin, done, deadline))
        except Exception as exc:  # failure isolation includes is_configured errors
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
    for name, plugin, done, deadline in pending:
        try:
            results.append(_finish_search(plugin, done, deadline, timeout))
        except Exception as exc:
            results.append(IndexerResult(source=name, ok=False, detail=f"error: {exc}"))
    return results
