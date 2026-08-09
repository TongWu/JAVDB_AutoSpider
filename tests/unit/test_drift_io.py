"""Unit tests for the shared drift JSONL IO helpers."""

from __future__ import annotations

import json
import threading

import pytest

from javdb.storage import drift_io


def test_drift_log_path_resolves_reports_dir_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "first"))
    assert (
        drift_io.drift_log_path()
        == str(tmp_path / "first" / "D1" / "d1_drift.jsonl")
    )

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "second"))
    assert drift_io.drift_log_path() == str(
        tmp_path / "second" / "D1" / "d1_drift.jsonl"
    )


def test_append_jsonl_record_creates_directory_and_writes_lines(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    drift_io.append_jsonl_record({"a": 1, "b": "x"})
    drift_io.append_jsonl_record({"a": 2})

    path = tmp_path / "D1" / "d1_drift.jsonl"
    assert path.exists()
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"a": 1, "b": "x"},
        {"a": 2},
    ]


def test_append_jsonl_record_honours_explicit_reports_dir(tmp_path):
    drift_io.append_jsonl_record(
        {"k": "v"}, reports_dir=str(tmp_path), filename="custom.jsonl",
    )

    path = tmp_path / "D1" / "custom.jsonl"
    assert path.exists()
    assert json.loads(path.read_text().strip()) == {"k": "v"}


def test_append_jsonl_record_lock_serializes_concurrent_appends(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    def write_record(i: int) -> None:
        drift_io.append_jsonl_record({"i": i})

    threads = [
        threading.Thread(target=write_record, args=(i,))
        for i in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = drift_io.read_jsonl(str(tmp_path / "D1" / "d1_drift.jsonl"))
    assert sorted(record["i"] for record in records) == list(range(20))


def test_append_jsonl_record_refuses_default_reports_dir_under_pytest(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("REPORTS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    drift_io.append_jsonl_record({"would": "pollute"})

    assert not (tmp_path / "reports" / "D1" / "d1_drift.jsonl").exists()


@pytest.mark.parametrize(
    ("env_reports_dir", "arg_reports_dir"),
    [
        ("reports", None),
        (None, "reports"),
    ],
)
def test_append_jsonl_record_refuses_explicit_tracked_default_under_pytest(
    tmp_path, monkeypatch, env_reports_dir, arg_reports_dir,
):
    monkeypatch.chdir(tmp_path)
    if env_reports_dir is None:
        monkeypatch.delenv("REPORTS_DIR", raising=False)
    else:
        monkeypatch.setenv("REPORTS_DIR", env_reports_dir)

    drift_io.append_jsonl_record(
        {"would": "pollute"}, reports_dir=arg_reports_dir,
    )

    assert not (tmp_path / "reports" / "D1" / "d1_drift.jsonl").exists()


@pytest.mark.parametrize(
    ("reports_dir", "filename"),
    [
        ("reports", "d1_drift.jsonl"),
        ("reports", "custom.jsonl"),
    ],
)
def test_append_jsonl_record_refuses_tracked_reports_dir_under_pytest(
    tmp_path, monkeypatch, reports_dir, filename,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPORTS_DIR", raising=False)

    drift_io.append_jsonl_record(
        {"would": "pollute"}, reports_dir=reports_dir, filename=filename,
    )

    assert not (tmp_path / "reports" / "D1" / filename).exists()


def test_append_jsonl_record_swallows_write_failures(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("simulated FS failure")

    monkeypatch.setattr(drift_io.os, "makedirs", boom)

    drift_io.append_jsonl_record({"k": "v"})


def test_read_jsonl_warns_and_skips_malformed_lines(tmp_path, caplog):
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        '{"a": 1}\n'
        'NOT JSON\n'
        '\n'
        '{"b": 2}\n',
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        records = drift_io.read_jsonl(str(path))

    assert records == [{"a": 1}, {"b": 2}]
    assert "Skipping malformed JSONL line" in caplog.text


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (None, None, True),
        (None, 1, False),
        (42, 42, True),
        (42, 43, False),
        (42, 42.0, True),
        ("abc", "abc", True),
        ("abc", "def", False),
        (9007199254740993, 9007199254740992, False),
    ],
)
def test_values_equal(left, right, expected):
    assert drift_io._values_equal(left, right) is expected


def test_row_to_dict_accepts_dict_none_mapping_and_sequence():
    class RowMapping:
        def keys(self):
            return ["a", "b"]

        def __getitem__(self, key):
            return {"a": 1, "b": 2}[key]

    assert drift_io._row_to_dict(None) == {}
    assert drift_io._row_to_dict({"a": 1}) == {"a": 1}
    assert drift_io._row_to_dict(RowMapping()) == {"a": 1, "b": 2}
    assert drift_io._row_to_dict([("x", 3)]) == {"x": 3}
