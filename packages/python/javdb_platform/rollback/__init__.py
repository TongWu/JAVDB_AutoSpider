"""Rollback library — public surface used by the CLI and HTTP endpoints.

See :mod:`packages.python.javdb_platform.rollback.core` for the full
docstring and the extracted planning / apply logic.
"""

from packages.python.javdb_platform.rollback.core import (  # noqa: F401
    RollbackPlan,
    RollbackRequest,
    RollbackResult,
    apply_rollback,
    plan_rollback,
)

__all__ = [
    "RollbackPlan",
    "RollbackRequest",
    "RollbackResult",
    "apply_rollback",
    "plan_rollback",
]
