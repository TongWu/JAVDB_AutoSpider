#!/usr/bin/env python3
"""Compatibility entry point — prefer ``migration/migrate_to_current.py``.

Actor backfill is implemented as ``migrate_to_current.py --backfill-actors`` (and
can be combined with the schema step in one command).

This script forwards all CLI arguments with ``--backfill-actors`` prepended.

Usage:
    python3 packages/python/javdb_migrations/tools/backfill_movie_actors.py [--history-db PATH] [--dry-run] [--limit N] [--no-proxy]
"""

from __future__ import annotations

import os
import runpy
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_migration_dir = os.path.dirname(_script_dir)
_target = os.path.join(_migration_dir, "migrate_to_current.py")

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--backfill-actors", *sys.argv[1:]]
    runpy.run_path(_target, run_name="__main__")
