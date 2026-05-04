"""P1-B integration tests: ``process_detail_entries`` × MovieClaim coordinator.

Goals
-----

1. **Legacy parity** — when ``state.global_movie_claim_client`` is ``None``,
   the runner must behave **bit-for-bit** as it did before P1-B.  No
   network calls, no extra skips, no new log lines.
2. **Three exhaustive claim outcomes** — ``acquired=True``,
   ``already_completed=True``, contention — each must route to the right
   per-candidate fate.
3. **Symmetric complete/release** — every successful detail fetch
   produces exactly one ``complete``; every failed fetch produces exactly
   one ``release``; the loop's ``finally:`` releases anything still held
   when the iterator dies prematurely.
4. **Fail-open** — :class:`MovieClaimUnavailable` raised at any of the
   three call sites must NOT corrupt the run.  The candidate is still
   processed (when raised by ``claim``), or the result is still acked
   (when raised by ``complete`` / ``release``).

The tests stub :class:`FetchBackend` and :class:`EngineResult` at the
duck-type level; the real backend is exercised in ``tests/smoke``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.movie_claim_client import (  # noqa: E402
    ClaimResult,
    CompleteResult,
    MovieClaimUnavailable,
    ReleaseResult,
    ReportFailureResult,
)
import packages.python.javdb_spider.runtime.state as state  # noqa: E402
from packages.python.javdb_spider.fetch.backend import FetchRuntimeState  # noqa: E402
from packages.python.javdb_spider.detail import runner as detail_runner  # noqa: E402
from packages.python.javdb_spider.detail.runner import (  # noqa: E402
    DetailEntryCandidate,
    DetailPersistOutcome,
    process_detail_entries,
)


# ── stub backend / result ───────────────────────────────────────────────────


@dataclass
class _StubResult:
    """Minimal duck-type for :class:`EngineResult` used by the runner."""

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
    """Honours :class:`FetchBackend`'s contract well enough for the runner.

    Tests pre-populate ``self.results_to_yield`` with a list of
    :class:`_StubResult` instances; the order is preserved, and the
    runner's iterator is drained once.  ``submit_task`` records the
    submission order so we can assert which hrefs survived the
    pre-fetch claim filter.
    """

    def __init__(self, results: Optional[List[_StubResult]] = None):
        self.submitted: List[Any] = []
        self.results_to_yield = list(results or [])
        self.started = False
        self.done = False
        self.shutdown_called = False
        self._runtime_state = FetchRuntimeState(use_proxy=False, use_cf_bypass=False)

    @property
    def worker_count(self) -> int:
        return 1

    def start(self) -> None:
        self.started = True

    def submit_task(self, task) -> None:
        self.submitted.append(task)

    def mark_done(self) -> None:
        self.done = True

    def results(self) -> Iterator[_StubResult]:
        for r in self.results_to_yield:
            yield r

    def shutdown(self, *, timeout: float = 10):
        self.shutdown_called = True
        return []

    def runtime_state(self) -> FetchRuntimeState:
        return self._runtime_state


def _entry(code: str = "ABC-1", href: str = "/v/abc1", page: int = 1) -> dict:
    return {
        "video_code": code,
        "href": href,
        "page": page,
        "is_today_release": False,
        "is_yesterday_release": False,
    }


def _claim_ok(href: str = "/v/abc1") -> ClaimResult:
    return ClaimResult(
        acquired=True,
        current_holder_id=state.runtime_holder_id,
        expires_at=999_999,
        already_completed=False,
        server_time_ms=1,
    )


def _claim_in_cooldown(href: str = "/v/abc1") -> ClaimResult:
    """P2-A: ``acquired=False`` with ``cooldown_until > server_time_ms``.

    Mirrors the Worker rejecting a claim during the failure cooldown
    window after one or more ``report_failure`` calls.  Includes
    ``last_error_kind`` and ``fail_count`` for end-to-end visibility.
    """
    return ClaimResult(
        acquired=False,
        current_holder_id="",
        expires_at=0,
        already_completed=False,
        server_time_ms=1_000_000,
        cooldown_until=2_000_000,
        last_error_kind="timeout",
        fail_count=2,
    )


def _claim_already_completed() -> ClaimResult:
    return ClaimResult(
        acquired=False,
        current_holder_id="",
        expires_at=0,
        already_completed=True,
        server_time_ms=1,
    )


def _claim_contended(holder: str = "peer-runner") -> ClaimResult:
    return ClaimResult(
        acquired=False,
        current_holder_id=holder,
        expires_at=999_999,
        already_completed=False,
        server_time_ms=1,
    )


@pytest.fixture(autouse=True)
def _reset_global_state(monkeypatch):
    """Keep :mod:`runtime.state` clean across tests."""
    state.parsed_links.clear()
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    yield
    state.parsed_links.clear()


def _patch_runner_filters(monkeypatch):
    """Bypass history / rclone / recent-release filters so candidates pass through."""
    monkeypatch.setattr(detail_runner, "has_complete_subtitles", lambda *a, **kw: False)
    monkeypatch.setattr(detail_runner, "should_skip_from_rclone", lambda *a, **kw: False)
    monkeypatch.setattr(
        detail_runner, "should_skip_recent_today_release", lambda *a, **kw: False
    )
    monkeypatch.setattr(
        detail_runner, "should_skip_recent_yesterday_release", lambda *a, **kw: False
    )


def _patch_persist(monkeypatch):
    """Stub persist_parsed_detail_result → trivial reported outcome with row=None."""
    monkeypatch.setattr(
        detail_runner,
        "persist_parsed_detail_result",
        lambda **kw: DetailPersistOutcome(status="reported", visited_href=kw.get("entry", {}).get("href")),
    )
    # finalize_detail_phase touches sqlite/history; stub it out.
    monkeypatch.setattr(detail_runner, "finalize_detail_phase", lambda **kw: None)
    # extract_magnets is called on result.data['magnets']; we stub data so it's safe.
    monkeypatch.setattr(detail_runner, "extract_magnets", lambda *a, **kw: {})


def _make_success_result(entry: dict) -> _StubResult:
    task = _StubTask(entry, entry_index="1/1")
    return _StubResult(
        task=task,
        success=True,
        data={
            "magnets": [],
            "actor_info": "",
            "actor_gender": "",
            "actor_link": "",
            "supporting": "",
        },
    )


def _make_failure_result(entry: dict, *, error: str = "fetch error") -> _StubResult:
    task = _StubTask(entry, entry_index="1/1")
    return _StubResult(task=task, success=False, error=error)


def _common_kwargs() -> dict:
    return dict(
        history_data={},
        history_file="",
        csv_path="",
        fieldnames=[],
        dry_run=True,
        use_history_for_saving=False,
        is_adhoc_mode=False,
    )


# ── 1. legacy parity (client = None) ────────────────────────────────────────


def test_legacy_no_client_does_not_call_claim(monkeypatch):
    """When MovieClaim is disabled, the runner must NOT touch the client.

    This is the golden test that locks "未配置时行为等同今天" — fail-open
    in the absence of any DO config means zero behavioural drift.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry()
    backend = _StubBackend(results=[_make_success_result(entry)])

    sentinel = MagicMock()  # would explode if any method were ever called
    sentinel.claim.side_effect = AssertionError("claim must not be called when client=None")
    sentinel.release.side_effect = AssertionError("release must not be called when client=None")
    sentinel.complete.side_effect = AssertionError("complete must not be called when client=None")

    # client stays None; sentinel is never assigned to global state.
    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    # Submit + complete behaviour identical to pre-P1-B.
    assert len(backend.submitted) == 1
    assert out["failed"] == 0


# ── 2a. acquired=True path ──────────────────────────────────────────────────


def test_claim_acquired_submits_task_and_completes_on_success(monkeypatch):
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry()
    backend = _StubBackend(results=[_make_success_result(entry)])

    client = MagicMock()
    client.claim.return_value = _claim_ok()
    client.complete.return_value = CompleteResult(
        completed=True, href=entry["href"], server_time_ms=1,
    )
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    client.claim.assert_called_once()
    args, kwargs = client.claim.call_args
    assert args[0] == entry["href"]
    assert args[1] == state.runtime_holder_id
    assert kwargs["ttl_ms"] == 30 * 60 * 1000
    assert "date" in kwargs and len(kwargs["date"]) == 10  # YYYY-MM-DD

    client.complete.assert_called_once()
    client.release.assert_not_called()
    assert len(backend.submitted) == 1


# ── 2b. already_completed=True path ─────────────────────────────────────────


def test_claim_already_completed_skips_and_marks_parsed(monkeypatch):
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/done")
    backend = _StubBackend(results=[])  # nothing should be submitted

    client = MagicMock()
    client.claim.return_value = _claim_already_completed()
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert backend.submitted == []
    assert out["skipped_history"] >= 1
    # The already-completed href must be added to parsed_links so we don't
    # re-issue claims on subsequent same-run pages.
    assert "/v/done" in state.parsed_links
    client.complete.assert_not_called()
    client.release.assert_not_called()


# ── 2c. contended (acquired=False, already_completed=False) ────────────────


def test_claim_contended_skips_without_completing_or_releasing(monkeypatch):
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/contended")
    backend = _StubBackend(results=[])

    client = MagicMock()
    client.claim.return_value = _claim_contended()
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert backend.submitted == []
    # No fetch ⇒ no complete; no lease ⇒ no release.  The contended href
    # may end up in :data:`state.parsed_links` from the upstream
    # ``prepare_detail_entries`` (within-session dedup) but that's a
    # separate pre-P1-B concern; the runner's per-process retry
    # behaviour for contended hrefs across multiple ingestion runs is
    # unchanged.
    client.complete.assert_not_called()
    client.release.assert_not_called()


# ── 3. failure path → report_failure (P2-A) ────────────────────────────────


def test_failed_fetch_reports_failure(monkeypatch):
    """P2-A: a failed fetch issues ``report_failure`` (not ``release``).

    ``report_failure`` bumps the DO ``fail_count`` *and* releases the
    active claim atomically, so the caller MUST NOT also issue a
    separate ``release`` call when ``report_failure`` succeeds — peer
    runners would otherwise see two transitions for one failure.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/will-fail")
    backend = _StubBackend(
        results=[_make_failure_result(entry, error="connection timeout")],
    )

    client = MagicMock()
    client.claim.return_value = _claim_ok(href=entry["href"])
    client.report_failure.return_value = ReportFailureResult(
        fail_count=1, cooldown_until=0, dead_lettered=False, server_time_ms=1,
    )
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert out["failed"] == 1
    client.report_failure.assert_called_once()
    args, kwargs = client.report_failure.call_args
    assert args[0] == entry["href"]
    assert args[1] == state.runtime_holder_id
    # ``error_kind`` should have been classified from "connection timeout"
    # → ``"timeout"`` per ``_classify_fetch_error_kind``.
    assert kwargs.get("error_kind") == "timeout"
    # report_failure already releases the claim on the Worker side, so
    # no separate release call should fire.
    client.release.assert_not_called()
    client.complete.assert_not_called()


def test_failed_fetch_falls_back_to_release_when_report_unavailable(monkeypatch):
    """If ``report_failure`` raises Unavailable, fall back to ``release``.

    Pre-P2-A Workers don't expose ``/report_failure`` (they 404).  In
    that case the spider must still free the slot promptly — peer
    runners shouldn't have to wait for the TTL just because the new
    endpoint is missing.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/will-fail")
    backend = _StubBackend(results=[_make_failure_result(entry)])

    client = MagicMock()
    client.claim.return_value = _claim_ok(href=entry["href"])
    client.report_failure.side_effect = MovieClaimUnavailable("HTTP 404: ...")
    client.release.return_value = ReleaseResult(released=True, server_time_ms=1)
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert out["failed"] == 1
    client.report_failure.assert_called_once()
    client.release.assert_called_once()
    args, _ = client.release.call_args
    assert args[0] == entry["href"]
    client.complete.assert_not_called()


# ── 4. fail-open on MovieClaimUnavailable ──────────────────────────────────


def test_claim_unavailable_keeps_candidate(monkeypatch):
    """If ``claim`` raises Unavailable, the candidate is still fetched.

    This is the second leg of the fail-open contract: a coordinator
    outage during the lease attempt must not strip the runner of work.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry()
    backend = _StubBackend(results=[_make_success_result(entry)])

    client = MagicMock()
    client.claim.side_effect = MovieClaimUnavailable("network error")
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    # Candidate kept and submitted despite claim failure.
    assert len(backend.submitted) == 1
    # complete/release are still called (via best-effort wrappers) but
    # the held_claims set was empty because shard_date is None when claim
    # never returned a valid date pin.  Verify no spurious calls.
    # complete may be called from the best-effort wrapper but only when
    # shard_date is set; here it's None, so both must not have fired.
    client.complete.assert_not_called()
    client.release.assert_not_called()


def test_complete_unavailable_falls_back_to_release(monkeypatch):
    """If ``complete`` raises Unavailable, we still ack AND release.

    Regression for the lease-leak bug: previously the success path
    discarded the href from ``held_claims`` immediately after calling
    ``_complete_movie_claim``, even when the call timed out or raised
    :class:`MovieClaimUnavailable`.  That left the lease attributed to
    this runner on the Worker side until the 30-minute TTL expired,
    blocking peer runners from claiming the same href.  The fix falls
    back to an explicit ``release`` whenever completion was not
    confirmed, so the slot frees up promptly while the result is still
    acked locally.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry()
    success = _make_success_result(entry)
    backend = _StubBackend(results=[success])

    client = MagicMock()
    client.claim.return_value = _claim_ok()
    client.complete.side_effect = MovieClaimUnavailable("upstream timeout")
    client.release.return_value = ReleaseResult(released=True, server_time_ms=1)
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    # Result was acked exactly once with the persist outcome.
    assert len(success.ack_calls) == 1
    assert success.ack_calls[0][0] == "reported"
    assert out["failed"] == 0
    # Fallback release MUST fire for the held href (not via the
    # ``finally:`` sweep — that branch already discarded the href —
    # but inline, so peers see the slot free without a TTL wait).
    client.complete.assert_called_once()
    client.release.assert_called_once()
    args, kwargs = client.release.call_args
    assert args[0] == entry["href"]
    assert args[1] == state.runtime_holder_id
    assert kwargs.get("date") and len(kwargs["date"]) == 10  # YYYY-MM-DD


def test_complete_returning_false_falls_back_to_release(monkeypatch):
    """``completed=False`` is a stale-holder signal → release the lease.

    The Worker returns ``completed=False`` when the active claim has
    already been re-leased by another runner (e.g. our TTL expired
    mid-fetch and a peer picked the slot up).  In that case our
    ``complete`` call did NOT free the href on the Worker, so the
    runner must still issue ``release`` before forgetting about it
    locally.  Otherwise the next ``GC`` alarm is the only thing that
    can free the lease, and peers wait minutes for nothing.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/stale-holder")
    success = _make_success_result(entry)
    backend = _StubBackend(results=[success])

    client = MagicMock()
    client.claim.return_value = _claim_ok(href=entry["href"])
    client.complete.return_value = CompleteResult(
        completed=False, href=entry["href"], server_time_ms=1,
    )
    client.release.return_value = ReleaseResult(released=True, server_time_ms=1)
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert out["failed"] == 0
    assert len(success.ack_calls) == 1
    assert success.ack_calls[0][0] == "reported"
    client.complete.assert_called_once()
    client.release.assert_called_once()
    args, _ = client.release.call_args
    assert args[0] == entry["href"]


def test_complete_unexpected_error_falls_back_to_release(monkeypatch):
    """Non-:class:`MovieClaimUnavailable` exceptions also trigger the fallback.

    The bug applied uniformly to *every* unconfirmed completion: a
    bare ``RuntimeError`` from a malformed Worker response was just
    as capable of leaking the lease as a network timeout.  The
    ``except Exception`` branch in ``_complete_movie_claim`` must
    surface ``False`` so the caller releases.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/oops")
    success = _make_success_result(entry)
    backend = _StubBackend(results=[success])

    client = MagicMock()
    client.claim.return_value = _claim_ok(href=entry["href"])
    client.complete.side_effect = RuntimeError("unexpected boom")
    client.release.return_value = ReleaseResult(released=True, server_time_ms=1)
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert out["failed"] == 0
    assert len(success.ack_calls) == 1
    assert success.ack_calls[0][0] == "reported"
    client.complete.assert_called_once()
    client.release.assert_called_once()


def test_release_unavailable_does_not_break_failure_path(monkeypatch):
    """Both ``report_failure`` and the fallback ``release`` may raise.

    The runner must still ack the result and surface ``failed=1``;
    no claim-coordinator outage is allowed to leak into the
    persistence pipeline.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry()
    failure = _make_failure_result(entry)
    backend = _StubBackend(results=[failure])

    client = MagicMock()
    client.claim.return_value = _claim_ok()
    client.report_failure.side_effect = MovieClaimUnavailable("upstream 5xx")
    client.release.side_effect = MovieClaimUnavailable("upstream 5xx")
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert out["failed"] == 1
    assert len(failure.ack_calls) == 1
    assert failure.ack_calls[0][0] == "failed"


# ── 5. mixed batch: acquired + already_completed + contended ───────────────


def test_mixed_claim_outcomes_are_partitioned_correctly(monkeypatch):
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)

    e_keep = _entry("KEEP-1", href="/v/keep")
    e_done = _entry("DONE-1", href="/v/done")
    e_busy = _entry("BUSY-1", href="/v/busy")
    backend = _StubBackend(results=[_make_success_result(e_keep)])

    def fake_claim(href, holder, *, ttl_ms, date):
        if href == "/v/keep":
            return _claim_ok(href)
        if href == "/v/done":
            return _claim_already_completed()
        return _claim_contended()

    client = MagicMock()
    client.claim.side_effect = fake_claim
    client.complete.return_value = CompleteResult(
        completed=True, href="/v/keep", server_time_ms=1,
    )
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend,
        entries=[e_keep, e_done, e_busy],
        phase=1,
        **_common_kwargs(),
    )

    # Only the ``acquired=True`` candidate hits the backend.
    assert len(backend.submitted) == 1
    submitted_href = backend.submitted[0].meta["entry"]["href"]
    assert submitted_href == "/v/keep"

    # Exactly one complete (for the kept href), zero releases.
    client.complete.assert_called_once()
    client.release.assert_not_called()

    # already_completed is included in skipped_history; contended is
    # tracked separately (logged + skipped) and does NOT inflate
    # skipped_history.
    assert out["skipped_history"] >= 1
    assert out["failed"] == 0


# ── 6. shard_date pinning across calls ──────────────────────────────────────


def test_shard_date_is_pinned_for_complete_and_release(monkeypatch):
    """All claim/complete/release calls in one phase share one ``date``.

    This is the "pin the shard date at task dispatch time" contract from
    ``movie_claim_client.current_shard_date``: re-evaluating it at each
    step would re-fragment claims across midnight.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)

    e_ok = _entry("OK-1", href="/v/ok")
    e_fail = _entry("FAIL-1", href="/v/fail")
    backend = _StubBackend(
        results=[_make_success_result(e_ok), _make_failure_result(e_fail)],
    )

    client = MagicMock()
    client.claim.side_effect = [_claim_ok("/v/ok"), _claim_ok("/v/fail")]
    client.complete.return_value = CompleteResult(
        completed=True, href="/v/ok", server_time_ms=1,
    )
    client.report_failure.return_value = ReportFailureResult(
        fail_count=1, cooldown_until=0, dead_lettered=False, server_time_ms=1,
    )
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    process_detail_entries(
        backend=backend, entries=[e_ok, e_fail], phase=1, **_common_kwargs(),
    )

    # Collect the ``date`` passed to every call site; they MUST all match.
    claim_dates = {kw["date"] for _, kw in client.claim.call_args_list}
    complete_dates = {kw["date"] for _, kw in client.complete.call_args_list}
    report_dates = {kw["date"] for _, kw in client.report_failure.call_args_list}
    all_dates = claim_dates | complete_dates | report_dates
    assert len(all_dates) == 1
    pinned = next(iter(all_dates))
    assert len(pinned) == 10 and pinned[4] == "-" and pinned[7] == "-"


# ── 7. P2-A: cooldown rejection + error_kind classifier ──────────────────


def test_claim_in_cooldown_skips_candidate(monkeypatch):
    """P2-A: ``acquired=False`` with cooldown is treated as contention.

    The current contract: the runner just skips the candidate (logged
    as "currently held") without retrying within the cooldown window.
    The runner does NOT need a separate cooldown branch because the
    Worker's ``cooldown_until`` already encodes the back-off — the next
    ingestion pass will re-attempt the claim and either get the slot
    or hit the cooldown again.  Either way: no fetch, no
    ``complete``/``release`` since we never acquired the lease.
    """
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/in-cooldown")
    backend = _StubBackend(results=[])

    client = MagicMock()
    client.claim.return_value = _claim_in_cooldown(href=entry["href"])
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert backend.submitted == []
    client.complete.assert_not_called()
    client.release.assert_not_called()
    client.report_failure.assert_not_called()
    assert out["failed"] == 0


@pytest.mark.parametrize(
    "error_msg, expected_kind",
    [
        ("connection timeout reached", "timeout"),
        ("Read timed out after 30s", "timeout"),
        ("login required: redirected", "login_required"),
        ("auth check failed", "login_required"),
        ("CF challenge served", "cf_bypass"),
        ("Cloudflare 1020", "cf_bypass"),
        ("proxy connect error", "proxy_error"),
        ("HTTP 404 not found", "not_found"),
        ("got HTTP 500 from upstream", "fetch_error"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_classify_fetch_error_kind(error_msg, expected_kind):
    assert detail_runner._classify_fetch_error_kind(error_msg) == expected_kind


def test_dead_lettered_failure_still_releases_local_state(monkeypatch):
    """``dead_lettered=True`` is a server-side hint and must not break ack."""
    _patch_runner_filters(monkeypatch)
    _patch_persist(monkeypatch)
    entry = _entry(href="/v/burnt")
    backend = _StubBackend(results=[_make_failure_result(entry, error="HTTP 500")])

    client = MagicMock()
    client.claim.return_value = _claim_ok(href=entry["href"])
    client.report_failure.return_value = ReportFailureResult(
        fail_count=8, cooldown_until=9_999_999, dead_lettered=True, server_time_ms=1,
    )
    monkeypatch.setattr(state, "global_movie_claim_client", client)

    out = process_detail_entries(
        backend=backend, entries=[entry], phase=1, **_common_kwargs(),
    )

    assert out["failed"] == 1
    client.report_failure.assert_called_once()
    client.release.assert_not_called()
    client.complete.assert_not_called()
