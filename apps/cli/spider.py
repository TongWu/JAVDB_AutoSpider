"""Canonical spider CLI entrypoint."""

from __future__ import annotations

import atexit

from packages.python.javdb_platform.db import close_db
from packages.python.javdb_spider.app.main import main

atexit.register(close_db)


if __name__ == "__main__":
    main()
