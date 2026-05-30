"""Runner-level wiring tests for ADR-040 content filtering."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import javdb.spider.detail.runner as runner
import javdb.spider.runtime.state as state
from javdb.spider.detail.runner import process_detail_entries
from javdb.spider.fetch.backend import FetchRuntimeState
from javdb.spider.fetch.fetch_engine import EngineResult
from javdb.spider.services.content_filter import FilterDecision, Rule


def _entry(video_code: str = "ABC-123", href: str = "/v/abc123") -> dict:
    return {
        "href": href,
        "video_code": video_code,
        "page": 1,
        "title": video_code,
    }


class _Backend:
    def __init__(self, movie_detail: object) -> None:
        self.movie_detail = movie_detail
        self.tasks = []
        self.ack_calls = []

    @property
    def worker_count(self) -> int:
        return 1

    def start(self) -> None:
        return None

    def submit_task(self, task) -> None:
        self.tasks.append(task)

    def mark_done(self) -> None:
        return None

    def runtime_state(self) -> FetchRuntimeState:
        return FetchRuntimeState(use_proxy=False, use_cf_bypass=False)

    def results(self):
        yield EngineResult(
            task=self.tasks[0],
            success=True,
            data={
                "magnet_links": {"subtitle": "magnet-1"},
                "actor_info": "Actor",
                "actor_gender": "female",
                "actor_link": "/actors/a",
                "supporting": "",
                "movie_detail": self.movie_detail,
            },
            _ack_callback=lambda status, changed: self.ack_calls.append((status, changed)),
        )

    def shutdown(self, *, timeout=10):
        return []


def test_runner_skips_persist_when_content_filter_drops(monkeypatch) -> None:
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "global_work_distributor_client", None, raising=False)
    monkeypatch.setattr(
        runner,
        "persist_parsed_detail_result",
        lambda **_kwargs: pytest.fail("filtered detail must not persist"),
    )

    rules = [Rule(id=1, dimension="actor", mode="exclude", value="Bad", enabled=True)]
    movie_detail = SimpleNamespace(actors=[], tags=[], video_code="ABC-123")
    evaluate_calls = []

    def fake_evaluate(detail, loaded_rules):
        evaluate_calls.append((detail, loaded_rules))
        return FilterDecision(keep=False, reasons=["blocked"])

    monkeypatch.setattr(runner, "evaluate", fake_evaluate)

    backend = _Backend(movie_detail)
    result = process_detail_entries(
        backend=backend,
        entries=[_entry()],
        phase=1,
        history_data={},
        history_file="history.csv",
        csv_path="report.csv",
        fieldnames=["href"],
        dry_run=True,
        use_history_for_saving=False,
        is_adhoc_mode=False,
        content_filter_rules=rules,
    )

    assert evaluate_calls == [(movie_detail, rules)]
    assert result["rows"] == []
    assert backend.ack_calls == [("content_filtered", False)]
