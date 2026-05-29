"""qB file-filter service package."""

from javdb.integrations.qb.file_filter.options import QbFileFilterOptions
from javdb.integrations.qb.file_filter.result import QbFileFilterResult

# NOTE: ``run_file_filter`` at the *package* level is the legacy programmatic
# API (``run_file_filter(min_size_mb=..., ...) -> dict``) consumed by the REST
# API handler ``apps/api/routers/operations.py`` and patched in
# ``tests/unit/test_operations_endpoints.py`` at
# ``javdb.integrations.qb.file_filter.run_file_filter``. The CLI service
# entrypoint is the distinct ``service.run_file_filter_cli(options)`` which the
# ``apps.cli.qb.file_filter`` adapter imports straight from ``.service``.
from javdb.integrations.qb.file_filter.service import run_file_filter_api as run_file_filter

__all__ = ["QbFileFilterOptions", "QbFileFilterResult", "run_file_filter"]
