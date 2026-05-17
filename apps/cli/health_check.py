"""Canonical health check CLI entrypoint."""

from javdb.infra.health_check import main


if __name__ == "__main__":
    raise SystemExit(main())
