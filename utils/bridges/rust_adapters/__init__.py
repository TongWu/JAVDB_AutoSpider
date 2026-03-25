"""Compatibility package for ``utils.bridges.rust_adapters``."""

from compat import extend_package_path

extend_package_path(
    __path__,
    "packages",
    "python",
    "javdb_platform",
    "bridges",
    "rust_adapters",
)
