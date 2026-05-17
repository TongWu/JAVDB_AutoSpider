"""Canonical rclone manager CLI entrypoint."""

from javdb.integrations.rclone.manager import main


if __name__ == "__main__":
    raise SystemExit(main())
