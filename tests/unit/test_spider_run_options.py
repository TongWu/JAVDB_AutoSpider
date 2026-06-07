from __future__ import annotations

from types import SimpleNamespace

from javdb.spider.app.options import SpiderRunOptions, spider_options_from_args


def test_spider_options_from_args_daily_defaults():
    args = SimpleNamespace(
        url=None,
        start_page=1,
        end_page=10,
        all=False,
        ignore_history=False,
        phase="all",
        output_file="Javdb_Test.csv",
        dry_run=False,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        enable_dedup=False,
        enable_redownload=None,
        redownload_threshold=None,
        result_json=None,
        use_history=False,
        from_pipeline=False,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=False,
        no_rclone_filter=False,
        disable_all_filters=False,
        cancel_event=None,
    )

    options = spider_options_from_args(args)

    assert options == SpiderRunOptions(
        mode="daily",
        url=None,
        start_page=1,
        end_page=10,
        parse_all=False,
        ignore_history=False,
        phase="all",
        output_file="Javdb_Test.csv",
        dry_run=False,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        enable_dedup=False,
        enable_redownload=None,
        redownload_threshold=None,
        result_json=None,
        use_history=False,
        from_pipeline=False,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=False,
        no_rclone_filter=False,
        disable_all_filters=False,
        cancel_event=None,
    )


def test_spider_options_from_args_adhoc_url():
    args = SimpleNamespace(
        url="https://javdb.com/actors/EvkJ",
        start_page=1,
        end_page=1,
        all=False,
        ignore_history=False,
        phase="1",
        output_file=None,
        dry_run=True,
        ignore_release_date=True,
        use_proxy=True,
        no_proxy=False,
        always_bypass_time=0,
        enable_dedup=True,
        enable_redownload=False,
        redownload_threshold=None,
        result_json="/tmp/result.json",
        use_history=True,
        from_pipeline=True,
        max_movies_phase1=2,
        max_movies_phase2=3,
        sequential=True,
        no_rclone_filter=True,
        disable_all_filters=True,
        cancel_event=None,
    )

    options = spider_options_from_args(args)

    assert options == SpiderRunOptions(
        mode="adhoc",
        url="https://javdb.com/actors/EvkJ",
        start_page=1,
        end_page=1,
        parse_all=False,
        ignore_history=False,
        phase="1",
        output_file=None,
        dry_run=True,
        ignore_release_date=True,
        use_proxy=True,
        no_proxy=False,
        always_bypass_time=0,
        enable_dedup=True,
        enable_redownload=False,
        redownload_threshold=None,
        result_json="/tmp/result.json",
        use_history=True,
        from_pipeline=True,
        max_movies_phase1=2,
        max_movies_phase2=3,
        sequential=True,
        no_rclone_filter=True,
        disable_all_filters=True,
    )


def test_spider_options_from_args_defaults_enable_redownload_to_none():
    args = SimpleNamespace(
        url=None,
        start_page=1,
        end_page=1,
        all=False,
        ignore_history=False,
        phase="all",
        output_file=None,
        dry_run=False,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        enable_dedup=False,
        redownload_threshold=None,
        result_json=None,
        use_history=False,
        from_pipeline=False,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=False,
        no_rclone_filter=False,
        disable_all_filters=False,
    )

    options = spider_options_from_args(args)

    assert options.enable_redownload is None


def test_spider_options_from_args_preserves_enable_redownload_true():
    args = SimpleNamespace(
        url=None,
        start_page=1,
        end_page=1,
        all=False,
        ignore_history=False,
        phase="all",
        output_file=None,
        dry_run=False,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        enable_dedup=False,
        enable_redownload=True,
        redownload_threshold=None,
        result_json=None,
        use_history=False,
        from_pipeline=False,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=False,
        no_rclone_filter=False,
        disable_all_filters=False,
    )

    options = spider_options_from_args(args)

    assert options.enable_redownload is True
