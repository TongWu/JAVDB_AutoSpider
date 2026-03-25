"""Entry point for running spider as a package: python3 scripts/spider"""
import atexit
import os
import sys

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
os.chdir(_project_root)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from utils.infra.db import close_db  # noqa: E402

atexit.register(close_db)

from scripts.spider.app.main import main  # noqa: E402

main()
