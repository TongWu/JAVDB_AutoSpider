"""Canonical health check CLI entrypoint."""

from packages.python.javdb_integrations.health_check import main


if __name__ == "__main__":
    raise SystemExit(main())
