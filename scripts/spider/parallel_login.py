"""Shared login-queue routing for parallel spider and migration workers."""

from __future__ import annotations

from typing import Optional


def use_login_queue_priority(
    login_proxy_name: Optional[str],
    worker_proxy_name: str,
    logged_in_worker_id: Optional[int],
    worker_id: int,
) -> bool:
    """True if this worker should drain ``login_queue`` before the shared task queue."""
    if logged_in_worker_id is not None and logged_in_worker_id == worker_id:
        return True
    if login_proxy_name and worker_proxy_name == login_proxy_name:
        return True
    return False


def should_delegate_login_task(
    login_proxy_name: Optional[str],
    worker_proxy_name: str,
) -> bool:
    """True if login-required work must be forwarded to the named login proxy worker."""
    return bool(login_proxy_name and worker_proxy_name != login_proxy_name)
