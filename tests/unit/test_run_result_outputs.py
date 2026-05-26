from __future__ import annotations

import json

from apps.cli.ops import run_result_outputs


def test_extract_spider_outputs_from_result(tmp_path):
    result = tmp_path / "spider-result.json"
    result.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "spider_run_result",
                "generated_at": "2026-05-20T01:00:00Z",
                "csv_path": "reports/DailyReport/x.csv",
                "session_id": "s1",
                "dedup_csv_path": "reports/dedup.csv",
                "stats": {
                    "pages": "1-2",
                    "found": 10,
                    "parsed": 8,
                    "skipped": 1,
                    "failed": 0,
                    "no_new": 1,
                },
                "mode": "daily",
                "url": None,
                "phase": "all",
                "page_range": "1-2",
                "started_at": "2026-05-20T01:00:00Z",
                "finished_at": "2026-05-20T01:02:00Z",
                "exit_code": 0,
                "failure_reason": None,
            }
        ),
        encoding="utf-8",
    )

    outputs = run_result_outputs.outputs_from_result(result)

    assert outputs["csv_filename"] == "reports/DailyReport/x.csv"
    assert outputs["session_id"] == "s1"
    assert outputs["dedup_csv_path"] == "reports/dedup.csv"
    assert outputs["stat_pages"] == "1-2"
    assert outputs["stat_parsed"] == "8"


def test_extract_spider_outputs_preserves_numeric_zero_stats(tmp_path):
    result = tmp_path / "spider-result.json"
    result.write_text(
        json.dumps(
            {
                "csv_path": "",
                "session_id": "",
                "dedup_csv_path": "",
                "stats": {
                    "pages": 0,
                    "found": 0,
                    "parsed": 0,
                    "skipped": 0,
                    "failed": 0,
                    "no_new": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    outputs = run_result_outputs.outputs_from_result(result)

    assert outputs == {
        "stat_pages": "0",
        "stat_found": "0",
        "stat_parsed": "0",
        "stat_skipped": "0",
        "stat_failed": "0",
        "stat_no_new": "0",
    }


def test_write_github_output_file(tmp_path):
    output = tmp_path / "github-output.txt"

    run_result_outputs.write_github_output(
        output,
        {"csv_filename": "reports/x.csv", "session_id": "s1"},
    )

    assert output.read_text(encoding="utf-8") == "csv_filename=reports/x.csv\nsession_id=s1\n"
