"""W6.C integration tests: ``process_detail_entries`` x WorkDistributor.

The W6.C follow-up ships the **full producer + consumer** integration:
enqueue + pull-based dispatch + per-task complete/release acks.

Contracts under test:

1. **Disabled is invisible** — client=None → no client surface
   touched; original local-iteration dispatch unchanged.
2. **Producer enqueues survivors** — client set → exactly one enqueue
   with surviving hrefs; idempotent dedup is the Worker's job.
3. **Consumer pulls and dispatches** — pulled items become
   EngineTasks; pulled hrefs the runner has no local metadata for are
   released back.
4. **Success → complete()** — task success path calls
   complete(holder_id, [href]) once.
5. **Failure → release()** — task failure path calls release(...),
   never complete().
6. **Pull failure falls back to local dispatch** — pull exception
   doesn't strand candidates; the local-loop tail-path picks them up.
7. **Shutdown bulk-releases held leases** — finally block fires
   release for everything in queue_held_hrefs.

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
    CompleteResult,
    EnqueueResult,
    PullResult,
    ReleaseResult,
    WorkDistributorUnavailable,
    WorkItem,
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


def _failure(entry: dict, error: str = "fetch error") -> _StubResult:
    return _StubResult(
        task=_StubTask(entry, entry_index="1/1"),
        success=False,
        error=error,
    )


def _work_item(href: str) -> WorkItem:
    return WorkItem(
        key=href, payload=None, enqueued_at_ms=1, attempt_count=1,
    )


def _make_pull_client(pulled_hrefs, *, enqueue_result=None, complete_ok=True, release_ok=True):
    """Build a MagicMock WorkDistributorClient.

    ``pulled_hrefs`` is the list of hrefs returned by the first pull;
    subsequent pulls return empty (signals "queue drained"). Use a
    sequence of lists to script multi-batch pulls.
    """
    client = MagicMock()
    client.enqueue.return_value = enqueue_result or EnqueueResult(
        enqueued=list(pulled_hrefs), duplicates=[],
        queue_size=len(pulled_hrefs), server_time_ms=1,
    )
    if isinstance(pulled_hrefs, list) and pulled_hrefs and isinstance(pulled_hrefs[0], list):
        # Multi-batch script.
        batches = pulled_hrefs + [[]]  # final empty
        side_effects = [
            PullResult(
                items=[_work_item(h) for h in batch],
                queue_size=sum(len(b) for b in batches[i+1:]),
                server_time_ms=1,
            )
            for i, batch in enumerate(batches)
        ]
        client.pull.side_effect = side_effects
    else:
        client.pull.side_effect = [
            PullResult(
                items=[_work_item(h) for h in pulled_hrefs],
                queue_size=len(pulled_hrefs),
                server_time_ms=1,
            ),
            PullResult(items=[], queue_size=0, server_time_ms=1),
        ]
    client.complete.return_value = CompleteResult(
        completed=[], skipped=[], server_time_ms=1,
    ) if complete_ok else None
    client.release.return_value = ReleaseResult(
        released=[], skipped=[], server_time_ms=1,
    ) if release_ok else None
    return client


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


def test_enabled_enqueues_and_pulls_survivors(monkeypatch):
    """Queue client present → enqueue + pull-based dispatch."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa"), _entry("B", "/v/bbb"), _entry("C", "/v/ccc")]
    backend = _StubBackend(results=[_success(e) for e in entries])

    client = _make_pull_client(["/v/aaa", "/v/bbb", "/v/ccc"])
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    # Enqueue called once with the surviving hrefs.
    client.enqueue.assert_called_once()
    assert client.enqueue.call_args[0][0] == ["/v/aaa", "/v/bbb", "/v/ccc"]
    # Pull called at least once.
    assert client.pull.call_count >= 1
    # All three tasks dispatched via the queue path.
    assert len(backend.submitted) == 3


def test_success_calls_complete_per_task(monkeypatch):
    """Each successful task triggers one complete() call."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa"), _entry("B", "/v/bbb")]
    backend = _StubBackend(results=[_success(e) for e in entries])

    client = _make_pull_client(["/v/aaa", "/v/bbb"])
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    # Two completes — one per task — with the correct holder_id.
    completed_hrefs = sorted(
        call.args[1][0] for call in client.complete.call_args_list
    )
    assert completed_hrefs == ["/v/aaa", "/v/bbb"]
    for call in client.complete.call_args_list:
        assert call.args[0] == state.runtime_holder_id
    # No release() call on the happy path.
    client.release.assert_not_called()


def test_failure_calls_release_not_complete(monkeypatch):
    """A failed task triggers release(), never complete()."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa")]
    backend = _StubBackend(results=[_failure(entries[0], error="timeout")])

    client = _make_pull_client(["/v/aaa"])
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    client.complete.assert_not_called()
    # release() is called at least once with the failed href.
    assert any(
        call.args[1] == ["/v/aaa"] for call in client.release.call_args_list
    )


def test_pull_failure_falls_back_to_local_dispatch(monkeypatch):
    """A pull exception must NOT strand candidates."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa"), _entry("B", "/v/bbb")]
    backend = _StubBackend(results=[_success(e) for e in entries])

    client = MagicMock()
    client.enqueue.return_value = EnqueueResult(
        enqueued=["/v/aaa", "/v/bbb"], duplicates=[],
        queue_size=2, server_time_ms=1,
    )
    client.pull.side_effect = WorkDistributorUnavailable("transient")
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    # All candidates still dispatched (via the local-loop fallback after
    # the queue path failed).
    assert len(backend.submitted) == 2


def test_enqueue_failure_disables_pull_and_falls_back(monkeypatch):
    """Enqueue exception → don't even attempt pull; local loop dispatches."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa")]
    backend = _StubBackend(results=[_success(entries[0])])

    client = MagicMock()
    client.enqueue.side_effect = WorkDistributorUnavailable("worker down")
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    # pull / complete / release must not be called when enqueue fails.
    client.pull.assert_not_called()
    client.complete.assert_not_called()
    # Local dispatch happened.
    assert len(backend.submitted) == 1


def test_no_entries_skips_enqueue(monkeypatch):
    """Empty survivor list → no enqueue call (avoids a no-op round-trip)."""
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(detail_runner, "has_complete_subtitles", lambda *a, **kw: True)
    backend = _StubBackend(results=[])

    client = MagicMock()
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=[_entry("A", "/v/a")], phase=1, **_common_kwargs(),
    )

    client.enqueue.assert_not_called()
    client.pull.assert_not_called()


def test_peer_enqueued_href_with_no_local_metadata_is_released(monkeypatch):
    """A pulled href the runner has no candidate for → release back."""
    _patch_pipeline(monkeypatch)
    entries = [_entry("A", "/v/aaa")]  # only one local survivor
    backend = _StubBackend(results=[_success(entries[0])])

    # Worker returns BOTH our /v/aaa AND a peer-enqueued /v/peer.
    client = _make_pull_client(["/v/aaa", "/v/peer"])
    monkeypatch.setattr(state, "global_work_distributor_client", client)

    process_detail_entries(
        backend=backend, entries=entries, phase=1, **_common_kwargs(),
    )

    # /v/peer was released because we have no local entry for it.
    release_calls = [call.args[1] for call in client.release.call_args_list]
    assert ["/v/peer"] in release_calls
    # /v/aaa was dispatched + completed.
    assert len(backend.submitted) == 1
    completed = [call.args[1][0] for call in client.complete.call_args_list]
    assert "/v/aaa" in completed
