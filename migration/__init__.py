"""Compatibility package for migration entrypoints and tools."""

from compat import extend_package_path

extend_package_path(__path__, "packages", "python", "javdb_migrations")
