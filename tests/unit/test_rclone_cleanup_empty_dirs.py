import subprocess

import pytest

from scripts import rclone_cleanup_empty_dirs as cleaner


def test_select_year_dirs_keeps_four_digit_years_and_unknown():
    year_dirs, skipped_dirs = cleaner.select_year_dirs(
        ["2026", "temp", "2018", "未知", "202", "manual", "1999"]
    )

    assert year_dirs == ["1999", "2018", "2026", "未知"]
    assert skipped_dirs == ["202", "manual", "temp"]


def test_rmdirs_year_uses_leave_root_workers_and_dry_run(monkeypatch):
    calls = []

    def fake_run_rclone(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(["rclone", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(cleaner, "run_rclone", fake_run_rclone)

    cleaner.rmdirs_year(
        "remote/2026",
        workers=64,
        dry_run=True,
        fast_list=True,
    )

    assert calls == [
        (
            [
                "--dry-run",
                "--fast-list",
                "--checkers",
                "64",
                "rmdirs",
                "--leave-root",
                "remote/2026",
            ],
            {"check": True, "capture": True, "timeout": None},
        )
    ]


def test_rmdirs_year_omits_optional_flags_by_default(monkeypatch):
    calls = []

    def fake_run_rclone(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(["rclone", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(cleaner, "run_rclone", fake_run_rclone)

    cleaner.rmdirs_year(
        "remote/2026",
        workers=32,
        dry_run=False,
        fast_list=False,
    )

    assert calls[0][0] == [
        "--checkers",
        "32",
        "rmdirs",
        "--leave-root",
        "remote/2026",
    ]


def test_parse_args_rejects_non_positive_workers(monkeypatch):
    monkeypatch.setattr(
        cleaner.sys,
        "argv",
        ["rclone_cleanup_empty_dirs.py", "remote", "--workers", "0"],
    )
    with pytest.raises(SystemExit):
        cleaner.parse_args()


def test_main_processes_years_one_by_one_and_skips_non_year_dirs(monkeypatch):
    calls = []

    monkeypatch.setattr(cleaner, "list_dirs", lambda root: ["temp", "2026", "未知", "2018"])
    monkeypatch.setattr(
        cleaner,
        "run_rclone",
        lambda args, **kwargs: subprocess.CompletedProcess(["rclone", *args], 0),
    )

    def fake_rmdirs_year(year_path, *, workers, dry_run, fast_list):
        calls.append((year_path, workers, dry_run, fast_list))
        return subprocess.CompletedProcess(["rclone"], 0, stdout="", stderr="")

    monkeypatch.setattr(cleaner, "rmdirs_year", fake_rmdirs_year)
    monkeypatch.setattr(
        cleaner,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "root": "remote",
                "workers": 32,
                "dry_run": True,
                "fast_list": False,
                "verbose": False,
            },
        )(),
    )

    assert cleaner.main() == 0
    assert calls == [
        ("remote/2018", 32, True, False),
        ("remote/2026", 32, True, False),
        ("remote/未知", 32, True, False),
    ]
