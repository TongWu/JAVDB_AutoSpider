"""Thin spider entrypoint wrapper."""

from packages.python.javdb_spider.app.run_service import SpiderRunService
from packages.python.javdb_spider.detail.parallel_mode import build_parallel_detail_backend
from packages.python.javdb_spider.detail.sequential_mode import build_sequential_detail_backend


def create_detail_backend(
    *,
    use_parallel: bool,
    use_cookie: bool,
    is_adhoc_mode: bool,
    session,
    use_proxy: bool,
    use_cf_bypass: bool,
):
    """Create the detail backend chosen by the current runtime mode."""

    if use_parallel:
        return build_parallel_detail_backend(
            use_cookie=use_cookie,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        )

    return build_sequential_detail_backend(
        session,
        use_cookie=use_cookie,
        is_adhoc_mode=is_adhoc_mode,
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
    )


def main():
    return SpiderRunService().run()


__all__ = ["SpiderRunService", "create_detail_backend", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
