from __future__ import annotations

from apps.cli.rclone.manager import options_from_args, parse_args
from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult


def test_rclone_options_defaults_for_report():
    options = options_from_args(parse_args(["--report"]))

    assert options.report is True
    assert options.scan is False
    assert options.execute is False
    assert options.execute_soft_delete is False
    assert options.validate is False
    assert options.workers == 4
    assert options.log_level == "INFO"


def test_rclone_parse_rejects_scan_execute_without_report():
    try:
        parse_args(["--scan", "--execute"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser error")


def test_rclone_result_exit_code():
    assert RcloneManagerResult(exit_code=0).exit_code == 0
    assert RcloneManagerResult(exit_code=1, error_reason="failed").exit_code == 1


def test_rclone_options_years_tuple():
    options = RcloneManagerOptions(scan=True, years=("2025", "2026"))

    assert options.years == ("2025", "2026")
