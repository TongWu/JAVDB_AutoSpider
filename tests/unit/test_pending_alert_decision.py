from __future__ import annotations

import json

import pytest

from apps.cli.db import pending_alert_decision as decision
from javdb.storage.sessions.pending_verify import (
    F_DERIVED_RECOMPUTE_DRIFT,
    F_KIND,
    F_PENDING_RESIDUAL_COUNT,
    F_RUN_ATTEMPT,
    F_RUN_ID,
    F_SESSION_ID,
    F_STATS_READ_ERROR,
    KIND_PENDING_SESSION_VERIFY,
)


def _write_jsonl(tmp_path, records: list[dict | str]):
    path = tmp_path / "D1" / "d1_drift.jsonl"
    path.parent.mkdir(exist_ok=True)
    lines = [
        item if isinstance(item, str) else json.dumps(item)
        for item in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_decide_returns_empty_when_file_missing(tmp_path):
    assert decision.decide_pending_alert(tmp_path / "missing.jsonl", "1", "1") == ""


def test_decide_fails_fast_on_malformed_jsonl(tmp_path):
    path = _write_jsonl(tmp_path, ["{not json"])

    with pytest.raises(ValueError, match="Malformed JSONL"):
        decision.decide_pending_alert(path, "1", "1")


def test_decide_ignores_nonmatching_records(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {
                F_KIND: "rollback_summary",
                F_RUN_ID: "1",
                F_RUN_ATTEMPT: "1",
                F_PENDING_RESIDUAL_COUNT: 9,
            },
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_RUN_ID: "2",
                F_RUN_ATTEMPT: "1",
                F_PENDING_RESIDUAL_COUNT: 9,
            },
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_RUN_ID: "1",
                F_RUN_ATTEMPT: "2",
                F_PENDING_RESIDUAL_COUNT: 9,
            },
        ],
    )

    assert decision.decide_pending_alert(path, "1", "1") == ""


def test_decide_alerts_when_pending_stats_unavailable(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_RUN_ID: "1",
                F_RUN_ATTEMPT: "1",
                F_SESSION_ID: "S-stats",
                F_STATS_READ_ERROR: True,
                F_PENDING_RESIDUAL_COUNT: 0,
            },
        ],
    )

    assert (
        decision.decide_pending_alert(path, "1", "1")
        == "stats_read_error=True session=S-stats"
    )


def test_decide_returns_first_critical_field_for_matching_run(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_RUN_ID: "1",
                F_RUN_ATTEMPT: "1",
                F_SESSION_ID: "S1",
                F_DERIVED_RECOMPUTE_DRIFT: 2,
                F_PENDING_RESIDUAL_COUNT: 0,
            },
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_RUN_ID: "1",
                F_RUN_ATTEMPT: "1",
                F_SESSION_ID: "S2",
                F_PENDING_RESIDUAL_COUNT: 5,
            },
        ],
    )

    assert (
        decision.decide_pending_alert(path, "1", "1")
        == "derived_recompute_drift=2 session=S1"
    )


def test_main_prints_decision_from_reports_dir(tmp_path, monkeypatch, capsys):
    _write_jsonl(
        tmp_path,
        [
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_RUN_ID: "100",
                F_RUN_ATTEMPT: "3",
                F_SESSION_ID: "S3",
                F_PENDING_RESIDUAL_COUNT: 1,
            },
        ],
    )
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")

    assert decision.main([]) == 0
    assert capsys.readouterr().out.strip() == "pending_residual_count=1 session=S3"
