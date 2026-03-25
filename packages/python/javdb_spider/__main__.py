"""Entry point for running the spider package."""
import atexit
from compat import activate_repo_root

activate_repo_root()

from packages.python.javdb_platform.db import close_db  # noqa: E402

atexit.register(close_db)

from packages.python.javdb_spider.app.main import main  # noqa: E402

main()
