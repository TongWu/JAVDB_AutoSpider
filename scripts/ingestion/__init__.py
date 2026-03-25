"""Compatibility package for ``scripts.ingestion``."""

from compat import extend_package_path

extend_package_path(__path__, "packages", "python", "javdb_ingestion")
