#!/usr/bin/env python3
"""
Thin shim for backward compatibility: delegates to the spider package.
Use either:
  python scripts/spider.py [args]
  python -m scripts.spider [args]
"""
import os
import sys

# Run from project root so package and utils resolve
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scripts.spider.main import main  # noqa: E402

if __name__ == "__main__":
    main()
