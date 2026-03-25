"""Canonical rclone manager CLI entrypoint."""

from packages.python.javdb_integrations.rclone_manager import main


if __name__ == "__main__":
    raise SystemExit(main())
