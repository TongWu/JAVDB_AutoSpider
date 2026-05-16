"""Compatibility package for ``migration.tools``."""

from compat import extend_package_path

extend_package_path(__path__, "javdb", "migrations", "tools")
