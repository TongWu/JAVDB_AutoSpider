"""Entry point for running the spider package."""
import atexit
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
os.chdir(_REPO_ROOT)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from javdb.storage.db import close_db  # noqa: E402

atexit.register(close_db)

from javdb.spider.app.main import main  # noqa: E402

main()
