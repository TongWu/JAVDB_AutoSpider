from __future__ import annotations

import threading
from typing import Optional

from javdb.spider.runtime.context import SpiderRuntime

_active_runtime: Optional[SpiderRuntime] = None
_active_lock = threading.RLock()


def bind_active_runtime(runtime: SpiderRuntime) -> SpiderRuntime:
    global _active_runtime
    with _active_lock:
        _active_runtime = runtime
        return runtime


def get_active_runtime() -> Optional[SpiderRuntime]:
    with _active_lock:
        return _active_runtime


def clear_active_runtime(runtime: SpiderRuntime | None = None) -> None:
    global _active_runtime
    with _active_lock:
        if runtime is None or _active_runtime is runtime:
            _active_runtime = None
