from __future__ import annotations

from javdb.integrations.qb.uploader.options import QbUploaderOptions
from javdb.integrations.qb.uploader.result import QbUploaderResult


def test_qb_uploader_options_defaults():
    options = QbUploaderOptions()

    assert options.mode == "daily"
    assert options.input_file is None
    assert options.proxy_override is None
    assert options.from_pipeline is False
    assert options.category is None
    assert options.session_id is None


def test_qb_uploader_result_exit_code_for_all_failed_attempts():
    result = QbUploaderResult(
        total_torrents=3,
        duplicate_count=0,
        attempted=3,
        successfully_added=0,
        failed_count=3,
    )

    assert result.exit_code == 1


def test_qb_uploader_result_exit_code_for_no_work():
    result = QbUploaderResult(total_torrents=0, duplicate_count=0, attempted=0)

    assert result.exit_code == 0


def test_qb_uploader_result_exit_code_for_missing_csv():
    result = QbUploaderResult(error_reason="csv-not-found")

    assert result.exit_code == 1


def test_qb_uploader_result_exit_code_for_unreadable_csv():
    result = QbUploaderResult(csv_ok=False)

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# CLI parser contract (apps.cli.qb.uploader)
# ---------------------------------------------------------------------------


def test_uploader_parse_args_defaults():
    from apps.cli.qb.uploader import options_from_args, parse_args

    options = options_from_args(parse_args([]))

    assert options.mode == "daily"
    assert options.input_file is None
    assert options.proxy_override is None
    assert options.from_pipeline is False
    assert options.category is None
    assert options.session_id is None


def test_uploader_parse_args_flags():
    from apps.cli.qb.uploader import options_from_args, parse_args

    options = options_from_args(
        parse_args(
            [
                "--mode",
                "adhoc",
                "--input-file",
                "custom.csv",
                "--use-proxy",
                "--from-pipeline",
                "--category",
                "Ad Hoc",
                "--session-id",
                "S-1",
            ]
        )
    )

    assert options.mode == "adhoc"
    assert options.input_file == "custom.csv"
    assert options.proxy_override is True
    assert options.from_pipeline is True
    assert options.category == "Ad Hoc"
    assert options.session_id == "S-1"


def test_uploader_parse_args_no_proxy():
    from apps.cli.qb.uploader import options_from_args, parse_args

    options = options_from_args(parse_args(["--no-proxy"]))

    assert options.proxy_override is False
