"""Canonical migration CLI entrypoint."""

from packages.python.javdb_migrations.migrate_to_current import main


if __name__ == "__main__":
    raise SystemExit(main())
