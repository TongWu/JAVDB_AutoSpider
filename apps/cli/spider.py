"""Canonical spider CLI entrypoint."""

from __future__ import annotations

import atexit

from javdb.storage.db.db_connection import close_db
from javdb.spider.app.main import main

atexit.register(close_db)


if __name__ == "__main__":
    main()
