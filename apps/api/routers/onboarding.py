"""Onboarding endpoints: status, test, complete, dismiss-hint."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.api.schemas.capabilities_payloads import (
    DismissHintPayload,
    OnboardingStatusResponse,
    OnboardingTestPayload,
    OnboardingTestResponse,
)
from apps.api.infra.auth import _require_auth, require_role
from apps.api.services import config_service
from javdb.storage.repos.system_state_repo import SystemStateRepo
from javdb.storage.db import db_connection


REQUIRED_COMPONENTS = ("javdb_session", "qb")
SKIPPABLE_COMPONENTS = ("smtp", "pikpak", "rclone", "proxy")


def _is_configured(component: str) -> bool:
    cfg = config_service.load_runtime_config()
    if component == "javdb_session":
        return bool(cfg.get("JAVDB_SESSION_COOKIE") or cfg.get("JAVDB_USERNAME"))
    if component == "qb":
        return bool(cfg.get("QB_URL"))
    if component == "smtp":
        return bool(cfg.get("SMTP_HOST") or cfg.get("SMTP_SERVER"))
    if component == "pikpak":
        return bool(cfg.get("PIKPAK_EMAIL") or cfg.get("PIKPAK_USERNAME"))
    if component == "rclone":
        return bool(cfg.get("RCLONE_FOLDER_PATH") or cfg.get("RCLONE_REMOTE"))
    if component == "proxy":
        mode = str(cfg.get("PROXY_MODE", "")).lower()
        if mode in ("pool", "single") and (cfg.get("PROXY_HTTP") or cfg.get("PROXY_POOL")):
            return True
        return bool(cfg.get("PROXY_HTTP") or cfg.get("PROXY_POOL"))
    return False


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _read_onboarded() -> bool:
    with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
        return SystemStateRepo(conn).get("onboarded") == "true"


def _test_javdb() -> tuple[bool, str, dict | None]:
    cfg = config_service.load_runtime_config()
    cookie = cfg.get("JAVDB_SESSION_COOKIE")
    if not cookie:
        return False, "JAVDB_SESSION_COOKIE not set", None
    return True, "cookie present", {"length": len(str(cookie))}


def _test_qb() -> tuple[bool, str, dict | None]:
    cfg = config_service.load_runtime_config()
    url = cfg.get("QB_URL")
    if not url:
        return False, "QB_URL not set", None
    username = str(cfg.get("QB_USERNAME") or "").strip()
    password = str(cfg.get("QB_PASSWORD") or "").strip()
    verify_tls = bool(cfg.get("QB_VERIFY_TLS", True))
    base = str(url).rstrip("/")

    import requests
    from javdb.integrations.qb.client import (
        LOGIN_REJECTED,
        LOGIN_SUCCESS,
        try_login_base_urls,
        try_ping_base_urls,
    )

    session = requests.Session()
    session.verify = verify_tls
    try:
        if username and password:
            outcome, login_url, err = try_login_base_urls(
                [base],
                username,
                password,
                post_fn=session.post,
                timeout=5,
                verify=verify_tls,
            )
            if outcome == LOGIN_REJECTED:
                return False, "qB auth failed: credentials rejected", {"url": base}
            if outcome != LOGIN_SUCCESS or not login_url:
                return False, f"qB unreachable: {err}", {"url": base}
            reachable = login_url
        else:
            ping_url, ping_err = try_ping_base_urls(
                [base],
                get_fn=session.get,
                timeout=5,
                verify=verify_tls,
            )
            if not ping_url:
                return False, f"qB unreachable: {ping_err}", {"url": base}
            reachable = ping_url

        version_resp = session.get(
            f"{reachable}/api/v2/app/version",
            timeout=5,
            verify=verify_tls,
        )
        if version_resp.status_code == 200 and version_resp.text:
            return True, f"qBittorrent {version_resp.text}", {"url": reachable}
        return True, "qBittorrent reachable", {"url": reachable}
    except Exception as exc:
        return False, f"connect failed: {exc}", {"url": base}


def _test_proxy() -> tuple[bool, str, dict | None]:
    cfg = config_service.load_runtime_config()
    mode = str(cfg.get("PROXY_MODE", "")).lower()
    pool = cfg.get("PROXY_POOL")
    single = cfg.get("PROXY_HTTP")

    proxy_url: str | None = None
    proxy_label: str = ""
    if mode == "pool" and isinstance(pool, list) and pool:
        first = pool[0]
        if isinstance(first, dict):
            proxy_url = str(first.get("http") or first.get("https") or "") or None
            proxy_label = str(first.get("name") or proxy_url or "")
    elif mode == "single" and single:
        proxy_url = str(single)
        proxy_label = proxy_url

    if not proxy_url:
        return False, "no proxy configured (PROXY_MODE/PROXY_POOL/PROXY_HTTP missing)", None

    try:
        import requests
        r = requests.get(
            "https://api.ipify.org",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=8,
        )
        if r.status_code == 200:
            return True, f"egress IP: {r.text}", {"proxy": proxy_label}
        return False, f"proxy returned HTTP {r.status_code}", {"proxy": proxy_label}
    except Exception as exc:
        return False, f"proxy test failed: {exc}", {"proxy": proxy_label}


def _test_smtp() -> tuple[bool, str, dict | None]:
    cfg = config_service.load_runtime_config()
    host = cfg.get("SMTP_HOST") or cfg.get("SMTP_SERVER")
    if not host:
        return False, "SMTP_HOST/SMTP_SERVER not set", None
    port = int(cfg.get("SMTP_PORT") or 587)
    import smtplib
    try:
        with smtplib.SMTP(str(host), port, timeout=8) as smtp:
            smtp.ehlo()
        return True, f"SMTP {host}:{port} reachable", {"host": str(host), "port": port}
    except Exception as exc:
        return False, f"SMTP test failed: {exc}", {"host": str(host), "port": port}


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


@router.post("/complete", response_model=OnboardingStatusResponse)
def mark_complete(_user=Depends(require_role("admin"))) -> OnboardingStatusResponse:
    with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put("onboarded", "true")
    return get_status(_user=_user)


@router.post("/dismiss-hint", response_model=dict)
def dismiss_hint(payload: DismissHintPayload, _user=Depends(require_role("admin"))) -> dict:
    with db_connection.get_db(db_connection.OPERATIONS_DB_PATH) as conn:
        repo = SystemStateRepo(conn)
        hints: list[str] = repo.get_json("dismissed_hints", default=[]) or []
        if payload.hint_id not in hints:
            hints.append(payload.hint_id)
            repo.put_json("dismissed_hints", hints)
    return {"dismissed_hints": hints}
