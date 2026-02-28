"""Entry point for running spider as a package: python3 scripts/spider"""
import os
import sys

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
os.chdir(_project_root)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from scripts.spider.main import main  # noqa: E402

main()
