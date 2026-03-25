"""API route groups."""

from apps.api.routers.auth import router as auth_router
from apps.api.routers.config import router as config_router
from apps.api.routers.explore import router as explore_router
from apps.api.routers.system import router as system_router
from apps.api.routers.tasks import router as tasks_router

__all__ = [
    "auth_router",
    "config_router",
    "explore_router",
    "system_router",
    "tasks_router",
]
