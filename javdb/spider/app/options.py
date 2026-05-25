from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SpiderRunOptions:
    mode: Literal["daily", "adhoc"]
    url: str | None
    start_page: int | None
    end_page: int | None
    parse_all: bool
    ignore_history: bool
    phase: Literal["1", "2", "all"]
    output_file: str | None
    dry_run: bool
    ignore_release_date: bool
    use_proxy: bool
    no_proxy: bool
    always_bypass_time: int | None
    enable_dedup: bool
    enable_redownload: bool | None
    redownload_threshold: float | None
    result_json: str | None
    use_history: bool = False
    from_pipeline: bool = False
    max_movies_phase1: int | None = None
    max_movies_phase2: int | None = None
    sequential: bool = False
    no_rclone_filter: bool = False
    disable_all_filters: bool = False


def spider_options_from_args(args) -> SpiderRunOptions:
    url = getattr(args, "url", None)
    return SpiderRunOptions(
        mode="adhoc" if url else "daily",
        url=url,
        start_page=getattr(args, "start_page", None),
        end_page=getattr(args, "end_page", None),
        parse_all=bool(getattr(args, "all", False)),
        ignore_history=bool(getattr(args, "ignore_history", False)),
        phase=str(getattr(args, "phase", None) or "all"),
        output_file=getattr(args, "output_file", None),
        dry_run=bool(getattr(args, "dry_run", False)),
        ignore_release_date=bool(getattr(args, "ignore_release_date", False)),
        use_proxy=bool(getattr(args, "use_proxy", False)),
        no_proxy=bool(getattr(args, "no_proxy", False)),
        always_bypass_time=getattr(args, "always_bypass_time", None),
        enable_dedup=bool(getattr(args, "enable_dedup", False)),
        enable_redownload=getattr(args, "enable_redownload", None),
        redownload_threshold=getattr(args, "redownload_threshold", None),
        result_json=getattr(args, "result_json", None),
        use_history=bool(getattr(args, "use_history", False)),
        from_pipeline=bool(getattr(args, "from_pipeline", False)),
        max_movies_phase1=getattr(args, "max_movies_phase1", None),
        max_movies_phase2=getattr(args, "max_movies_phase2", None),
        sequential=bool(getattr(args, "sequential", False)),
        no_rclone_filter=bool(getattr(args, "no_rclone_filter", False)),
        disable_all_filters=bool(getattr(args, "disable_all_filters", False)),
    )
