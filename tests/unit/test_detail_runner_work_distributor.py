"""W6.C integration tests: ``process_detail_entries`` x WorkDistributor.

This commit ships the **producer-only** integration — when the queue
client is enabled, each runner enqueues its discovered hrefs so peers
get queue-depth visibility via ``/work/stats``. The local dispatch
loop is intentionally unchanged; the pull/consume side is a planned
follow-up.

Two contracts under test:

1. **Disabled is invisible** — when ``state.global_work_distributor_client``
   is ``None`` (the default), the runner must NOT touch any client
   surface. Existing dispatch path unchanged.
2. **Enabled enqueues survivors** — when a client is present, ``enqueue``
   is called once with the surviving candidates' hrefs. Failures are
   fail-open: an enqueue exception is logged and the local dispatch
   loop continues.

Smoke-level test machinery (stub backend / result / persist patches)
reused from ``test_detail_runner_movie_claim``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional
from unittest.mock import MagicMock

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import packages.python.javdb_spider.runtime.state as state  # noqa: E402
from packages.python.javdb_platform.work_distributor_client import (  # noqa: E402
    EnqueueResult,
    WorkDistributorUnavailable,
)
from packages.python.javdb_spider.fetch.backend import FetchRuntimeState  # noqa: E402
from packages.python.javdb_spider.detail import runner as detail_runner  # noqa: E402
from packages.python.javdb_spider.detail.runner import (  # noqa: E402
    DetailPersistOutcome,
    process_detail_entries,
)


# ── shared stub backend (mirrors test_detail_runner_movie_claim) ─────────


@dataclass
class _StubResult:
    task: Any
    success: bool
    data: Optional[dict] = None
    used_cf: bool = False
    worker_name: str = "w0"
    error: Optional[str] = None
    ack_calls: List[tuple] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.ack_calls = []

    def acknowledge(self, outcome_status: str, *, runtime_state_changed: bool = False):
        self.ack_calls.append((outcome_status, runtime_state_changed))


class _StubTask:
    def __init__(self, entry: dict, entry_index: str):
        self.meta = {"entry": entry, "phase": 1, "video_code": entry.get("video_code", "")}
        self.entry_index = entry_index


class _StubBackend:
    def __init__(self, results: Optional[List[_StubResult]] = None):
        self.submitted: List[Any] = []
        self.results_to_yield = list(results or [])
        self._runtime_state = FetchRuntimeState(use_proxy=False, use_cf_bypass=False)

    @property
    def worker_count(self) -> int:
        return 1

    def start(self) -> None:
        pass

    def submit_task(self, task) -> None:
        self.submitted.append(task)

    def mark_done(self) -> None:
        pass

    def results(self) -> Iterator[_StubResult]:
        for r in self.results_to_yield:
            yield r

    def shutdown(self, *, timeout: float = 10):
        return []

    def runtime_state(self) -> FetchRuntimeState:
        return self._runtime_state


def _entry(code: str, href: str) -> dict:
    return {
        "video_code": code, "href": href, "page": 1,
        "is_today_release": False, "is_yesterday_release": False,
    }


def _success(entry: dict) -> _StubResult:
    return _StubResult(
        task=_StubTask(entry, entry_index="1/1"),
        success=True,
        data={"magnets": [], "actor_info": "", "actor_gender": "",
              "actor_link": "", "supporting": ""},
    )


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    state.parsed_links.clear()
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "global_work_distributor_client", None, raising=False)
    yield
    state.parsed_links.clear()


def _patch_pipeline(monkeypatch):
    monkeypatch.setattr(detail_runner, "has_complete_subtitles", lambda *a, **kw: False)
    monkeypatch.setattr(detail_runner, "should_skip_from_rclone", lambda *a, **kw: False)
    monkeypatch.setattr(
        detail_runner, "should_skip_recent_today_release", lambda *a, **kw: False,
    )
    monkeypatch.setattr(
        detail_runner, "should_skip_recent_yesterday_release", lambda *a, **kw: False,
    )
    monkeypatch.setattr(
        detail_runner, "persist_parsed_detail_result",
        lambda **kw: DetailPersistOutcome(
            status="reported",
            visited_href=kw.get("entry", {}).get("href"),
        ),
    )
    monkeypatch.setattr(detail_runner, "finalize_detail_phase", lambda **kw: None)
    monkeypatch.setattr(detail_runner, "extract_magnets", lambda *a, **kw: {})


def _common_kwargs() -> dict:
    return dict(
        history_data={}, history_file="", csv_path="", fieldnames=[],
        dry_run=True, use_history_for_saving=False, is_adhoc_mode=False,
    )


# ── tests ────────────────────────────────────────────────────────────────


def test_disabled_does_not_touch_work_distributor(monkeypatch):
    """Default state: queue client is None → no enqueue call, no logs."""
    _patch_pipeline(monkeypatch)
    entry = _entry("A", "/v/a")
    backend = _StubBackend(results=[_success(entry)])

    sentinel = MagicMock()
    sentinel.enqueue.side_effect = AssertionError(
        "enqueue() must not be called when client is None",
    )

    process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )
    # Sentinel never wired into state → assertion never trips.
    assert len(backend.submitted) == 1


def test_enabled_enqueues_surviving_hrefs(monkeypatch):
    """Queue client present → exactly one enqueue() with all survivor hrefs."""
    _patch_pipeline(monkeypatch)
    entries = [
        _entry("A", "/v/aaa"),
        _entry("B", "/v/bbb"),
        _entry("C", "/v/ccc"),
    ]
    backend = _StubBackend(results=[_success(e) for e in entries])

    client = MagicMock()
    client.enqueue.return_value = EnqueueResult(
        enqueued=["/v/aaa", "/v/bbb", "/v/ccc"],
        duplicates=[],
        queue_size=3,
        server_time_ms=1,
    )
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    client.enqueue.assert_called_once()
    args, _ = client.enqueue.call_args
    assert args[0] == ["/v/aaa", "/v/bbb", "/v/ccc"]
    # Local dispatch is unchanged — every entry was still submitted to
    # the backend.
    assert len(backend.submitted) == 3


def test_enqueue_failure_is_swallowed_local_dispatch_continues(monkeypatch):
    """Queue Unavailable must NOT prevent local dispatch."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa")]
    backend = _StubBackend(results=[_success(entries[0])])

    client = MagicMock()
    client.enqueue.side_effect = WorkDistributorUnavailable("worker down")
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    # Must NOT raise out of process_detail_entries.
    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    # Local dispatch happened despite the queue failure.
    assert len(backend.submitted) == 1


def test_no_entries_skips_enqueue(monkeypatch):
    """Empty survivor list → no enqueue call (avoids a no-op round-trip)."""
    _patch_pipeline(monkeypatch)
    # has_complete_subtitles → True so every entry is filtered out.
    monkeypatch.setattr(detail_runner, "has_complete_subtitles", lambda *a, **kw: True)
    backend = _StubBackend(results=[])

    client = MagicMock()
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=[_entry("A", "/v/a")], phase=1, **_common_kwargs(),
    )

    client.enqueue.assert_not_called()
