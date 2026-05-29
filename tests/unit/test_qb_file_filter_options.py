from __future__ import annotations

import pytest

from javdb.integrations.qb.file_filter.options import QbFileFilterOptions
from javdb.integrations.qb.file_filter.result import QbFileFilterResult


def test_qb_file_filter_options_defaults():
    options = QbFileFilterOptions(min_size_mb=100.0)

    assert options.days == 2
    assert options.proxy_override is None
    assert options.dry_run is False
    assert options.category is None
    assert options.categories is None
    assert options.delete_local_files is False


def test_qb_file_filter_result_exit_code_for_all_errors():
    result = QbFileFilterResult(torrents_processed=0, errors=2)

    assert result.exit_code == 1


def test_qb_file_filter_result_exit_code_for_pending_metadata():
    result = QbFileFilterResult(torrents_processed=0, pending_metadata=5, errors=0)

    assert result.exit_code == 0


def test_qb_file_filter_result_exit_code_with_errors_but_processed():
    result = QbFileFilterResult(torrents_processed=1, errors=2)

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# CLI parser contract (apps.cli.qb.file_filter)
# ---------------------------------------------------------------------------


def test_file_filter_parse_args_defaults():
    from apps.cli.qb.file_filter import (
        QB_FILE_FILTER_MIN_SIZE_MB,
        options_from_args,
        parse_args,
    )

    options = options_from_args(parse_args([]))

    assert options.min_size_mb == QB_FILE_FILTER_MIN_SIZE_MB
    assert options.days == 2
    assert options.proxy_override is None
    assert options.dry_run is False
    assert options.category is None
    assert options.categories is None
    assert options.delete_local_files is False


def test_file_filter_parse_args_flags():
    from apps.cli.qb.file_filter import options_from_args, parse_args

    options = options_from_args(
        parse_args(
            [
                "--min-size",
                "50",
                "--days",
                "5",
                "--no-proxy",
                "--dry-run",
                "--category",
                "JavDB",
                "--categories",
                '["Ad Hoc", "Daily Ingestion"]',
                "--delete-local-files",
            ]
        )
    )

    assert options.min_size_mb == 50.0
    assert options.days == 5
    assert options.proxy_override is False
    assert options.dry_run is True
    assert options.category == "JavDB"
    assert options.categories == ["Ad Hoc", "Daily Ingestion"]
    assert options.delete_local_files is True


def test_file_filter_parse_categories_filters_empty():
    from apps.cli.qb.file_filter import options_from_args, parse_args

    options = options_from_args(parse_args(["--categories", '["Ad Hoc", ""]']))

    assert options.categories == ["Ad Hoc"]


def test_file_filter_main_invalid_categories_exits_1():
    from apps.cli.qb.file_filter import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--categories", "{not-a-list}"])

    assert exc_info.value.code != 0


def test_file_filter_main_non_list_categories_exits_1():
    from apps.cli.qb.file_filter import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--categories", '"a-string"'])

    assert exc_info.value.code != 0
