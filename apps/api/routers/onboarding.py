"""Onboarding endpoints: status, test, complete, dismiss-hint."""
from __future__ import annotations

import os
from fastapi import APIRouter, Depends

from apps.api.schemas.capabilities_payloads import (
    OnboardingStatusResponse,
    OnboardingTestPayload,
    OnboardingTestResponse,
)
from apps.api.infra.auth import _require_auth, require_role
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo
from packages.python.javdb_platform.db_connection import get_db, OPERATIONS_DB_PATH


REQUIRED_COMPONENTS = ("javdb_session", "qb")
SKIPPABLE_COMPONENTS = ("smtp", "pikpak", "rclone", "proxy")


def _is_configured(component: str) -> bool:
    if component == "javdb_session":
        return bool(os.getenv("JAVDB_SESSION_COOKIE") or os.getenv("JAVDB_USERNAME"))
    if component == "qb":
        return bool(os.getenv("QB_URL"))
    if component == "smtp":
        return bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER"))
    if component == "pikpak":
        return bool(os.getenv("PIKPAK_USERNAME"))
    if component == "rclone":
        return bool(os.getenv("RCLONE_REMOTE"))
    if component == "proxy":
        return bool(os.getenv("PROXY_HTTP") or os.getenv("PROXY_POOL"))
    return False


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _read_onboarded() -> bool:
    with get_db(OPERATIONS_DB_PATH) as conn:
        return SystemStateRepo(conn).get("onboarded") == "true"


def _test_javdb() -> tuple[bool, str, dict | None]:
    cookie = os.getenv("JAVDB_SESSION_COOKIE")
    if not cookie:
        return False, "JAVDB_SESSION_COOKIE not set", None
    return True, "cookie present", {"length": len(cookie)}


def _test_qb() -> tuple[bool, str, dict | None]:
    url = os.getenv("QB_URL")
    if not url:
        return False, "QB_URL not set", None
    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/api/v2/app/version", timeout=5, verify=False)
        if r.status_code == 200:
            return True, f"qB {r.text}", {"url": url}
        return False, f"qB returned HTTP {r.status_code}", {"url": url}
    except Exception as exc:
        return False, f"connect failed: {exc}", {"url": url}


def _test_proxy() -> tuple[bool, str, dict | None]:
    proxy = os.getenv("PROXY_HTTP")
    if not proxy:
        return False, "no proxy configured", None
    try:
        import requests
        r = requests.get("https://api.ipify.org", proxies={"http": proxy, "https": proxy}, timeout=5)
        return r.status_code == 200, f"egress IP: {r.text}", {"proxy": proxy}
    except Exception as exc:
        return False, f"proxy test failed: {exc}", {"proxy": proxy}


def _test_smtp() -> tuple[bool, str, dict | None]:
    host = os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")
    if not host:
        return False, "SMTP_HOST not set", None
    import smtplib
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=5) as smtp:
            smtp.ehlo()
        return True, f"SMTP {host}:{port} reachable", {"host": host, "port": port}
    except Exception as exc:
        return False, f"SMTP test failed: {exc}", {"host": host, "port": port}


_COMPONENT_TESTERS = {
    "javdb": _test_javdb,
    "qb": _test_qb,
    "proxy": _test_proxy,
    "smtp": _test_smtp,
}


@router.post("/test", response_model=OnboardingTestResponse)
def test_component(payload: OnboardingTestPayload, _user=Depends(_require_auth)) -> OnboardingTestResponse:
    ok, message, details = _COMPONENT_TESTERS[payload.component]()
    return OnboardingTestResponse(component=payload.component, ok=ok, message=message, details=details)


@router.get("/status", response_model=OnboardingStatusResponse)
def get_status(_user=Depends(_require_auth)) -> OnboardingStatusResponse:
    required_missing = [c for c in REQUIRED_COMPONENTS if not _is_configured(c)]
    skippable_missing = [c for c in SKIPPABLE_COMPONENTS if not _is_configured(c)]
    return OnboardingStatusResponse(
        completed=_read_onboarded(),
        required_missing=required_missing,
        skippable_missing=skippable_missing,
    )
