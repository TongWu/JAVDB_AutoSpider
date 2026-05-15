"""Thin spider entrypoint wrapper."""

from packages.python.javdb_spider.app.run_service import (
    SpiderRunService,
    create_detail_backend,
)


def main():
    return SpiderRunService().run()


__all__ = ["SpiderRunService", "create_detail_backend", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
