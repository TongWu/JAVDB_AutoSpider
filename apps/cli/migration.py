"""Canonical migration CLI entrypoint."""

from javdb.migrations.migrate_to_current import main


if __name__ == "__main__":
    raise SystemExit(main())
