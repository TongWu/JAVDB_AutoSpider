#!/usr/bin/env python3
"""Compatibility entry point — prefer ``migrate_v7_to_v8.py``.

Actor backfill is implemented as ``migrate_v7_to_v8.py --backfill-actors`` (and
can be combined with the v7→v8 schema step in one command).

This script forwards all CLI arguments with ``--backfill-actors`` prepended.

Usage:
    python3 migration/backfill_movie_actors.py [--history-db PATH] [--dry-run] [--limit N] [--no-proxy]
"""

from __future__ import annotations

import os
import runpy
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_target = os.path.join(_script_dir, "migrate_v7_to_v8.py")

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--backfill-actors", *sys.argv[1:]]
    runpy.run_path(_target, run_name="__main__")
