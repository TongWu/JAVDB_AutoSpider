"""Entry point for running the spider package."""
import atexit
from compat import activate_repo_root

activate_repo_root()

from javdb.storage.db.db_connection import close_db  # noqa: E402

atexit.register(close_db)

from javdb.spider.app.main import main  # noqa: E402

main()
