"""System, parser, and crawl API services."""

from __future__ import annotations

import importlib
import io
import logging
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from apps.api.infra.security import _validate_target_url
from apps.api.parsers import (
    RUST_PARSERS_AVAILABLE,
    detect_page_type,
    parse_category_page,
    parse_detail_page,
    parse_index_page,
    parse_tag_page,
    parse_top_page,
)
from apps.api.services import context
from javdb.proxy.policy import resolve_proxy_override
from javdb.spider.parser import (
    result_to_dict,
)


# ---------------------------------------------------------------------------
# Login error categorization
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Order matters — first match wins.
    (re.compile(r"403\s+forbidden|cloudflare.*block|cf.*challenge", re.I), "cloudflare_blocked"),
    (re.compile(r"too many.*request|429|temporarily.*ban|ip.*ban|account.*locked", re.I), "ip_banned"),
    (re.compile(r"invalid.*password|incorrect.*password|invalid.*credentials|wrong.*password|incorrect.*email", re.I), "invalid_credentials"),
    (re.compile(r"captcha.*incorrect|captcha.*fail|captcha.*wrong|wrong.*captcha", re.I), "captcha_failed"),
    (re.compile(r"connection.*refused|timed.*out|name.*resolution|dns|connection.*reset|getaddrinfo", re.I), "connection_error"),
]

_FRIENDLY: Dict[str, str] = {
    "cloudflare_blocked": "Cloudflare challenge / 403 block",
    "ip_banned": "IP appears blocked or rate-limited",
    "invalid_credentials": "Wrong username or password",
    "captcha_failed": "Captcha solving failed",
    "connection_error": "Network connection failed",
}


def _categorize(text: str) -> Tuple[str, str]:
    """Return (category, friendly_message) for the given log/error text."""
    for pat, category in _ERROR_PATTERNS:
        if pat.search(text):
            return category, _FRIENDLY[category]
    return "unknown", "Login failed (no specific error pattern matched)"


def _raise_internal_error(detail: str, exc: Exception) -> None:
    context.logger.warning("%s: %s", detail, exc)
    raise HTTPException(status_code=500, detail=detail) from exc


def health_payload() -> Any:
    from apps.api.schemas.payloads import HealthResponse

    return HealthResponse(rust_core_available=RUST_PARSERS_AVAILABLE)


def _runtime_facade():
    return importlib.import_module("apps.api.services.runtime")


async def run_health_check_payload(payload: Any, username: str) -> Dict[str, Any]:
    command = ["python3", "-m", "apps.cli.ops.health_check"]
    if payload.check_smtp:
        command.append("--check-smtp")
    proxy_override = resolve_proxy_override(
        bool(getattr(payload, "use_proxy", False)),
        bool(getattr(payload, "no_proxy", False)),
    )
    if proxy_override is True:
        command.append("--use-proxy")
    elif proxy_override is False:
        command.append("--no-proxy")
    proc = subprocess.run(
        command,
        cwd=context.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    context.audit_logger.info("health_check username=%s code=%s", username, proc.returncode)
    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "exit_code": proc.returncode,
        "output": (proc.stdout or "")[-4000:],
    }


async def refresh_javdb_session_payload(username: str) -> Dict[str, Any]:
    proc = subprocess.run(
        ["python3", "-m", "apps.cli.login"],
        cwd=context.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    context.audit_logger.info("javdb_refresh username=%s code=%s", username, proc.returncode)
    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "output": ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:],
    }


_LOGIN_LOGGER_NAMES = (
    "javdb.spider.auth.login",
    "javdb.spider.fetch.session",
    "javdb.spider",
)


async def refresh_javdb_session_with_options(payload: Any, username: str) -> Dict[str, Any]:
    """POST /api/login/refresh v2 — proxy selection + categorized errors.

    *payload* is a :class:`~apps.api.schemas.payloads.JavdbLoginRefreshPayload`.
    Defaults (empty body) preserve original behavior: proxy_mode='auto'.
    """
    from apps.api.services import config_service

    cfg = config_service.load_runtime_config()

    if not cfg.get("JAVDB_USERNAME") or not cfg.get("JAVDB_PASSWORD"):
        return {
            "status": "failed",
            "error_category": "no_credentials",
            "message": "JAVDB_USERNAME / JAVDB_PASSWORD not set in config.py",
            "proxy_used": None,
            "attempts": [],
            "output": "",
        }

    # Build list of (proxies_dict_or_None, label) to try.
    proxy_attempts: List[Tuple[Optional[Dict[str, str]], str]] = []
    mode = payload.proxy_mode

    if mode == "none":
        proxy_attempts = [(None, "direct")]
    elif mode == "auto":
        proxy_attempts = [(None, "auto")]  # let attempt_login_refresh decide via config
    elif mode == "single":
        url = payload.proxy_url or cfg.get("PROXY_HTTP")
        if not url:
            return {
                "status": "failed",
                "error_category": "connection_error",
                "message": "single proxy mode but no proxy_url given and no PROXY_HTTP configured",
                "proxy_used": None,
                "attempts": [],
                "output": "",
            }
        proxy_attempts = [({"http": url, "https": url}, "single-override")]
    elif mode == "pool":
        pool = cfg.get("PROXY_POOL") or []
        candidates = pool
        if payload.pool_names:
            name_set = set(payload.pool_names)
            candidates = [p for p in pool if isinstance(p, dict) and p.get("name") in name_set]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            http = entry.get("http")
            https = entry.get("https") or http
            if not http:
                continue
            proxy_attempts.append(
                ({"http": http, "https": https}, entry.get("name") or http)
            )

    if not proxy_attempts:
        return {
            "status": "failed",
            "error_category": "no_proxy_succeeded",
            "message": "No proxy candidates to try",
            "proxy_used": None,
            "attempts": [],
            "output": "",
        }

    if payload.max_attempts:
        proxy_attempts = proxy_attempts[: payload.max_attempts]

    from javdb.spider.fetch.session import attempt_login_refresh  # noqa: PLC0415

    attempts_log: List[Dict[str, Any]] = []
    last_output = ""

    for proxies, name in proxy_attempts:
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(levelname)s %(name)s %(message)s")
        handler.setFormatter(fmt)
        for lname in _LOGIN_LOGGER_NAMES:
            logging.getLogger(lname).addHandler(handler)
        try:
            success, cookie, _proxy_name_returned = attempt_login_refresh(
                explicit_proxies=proxies,
                explicit_proxy_name=name,
                spider_uses_proxy=(proxies is not None),
                publish_to_do=False,
            )
            output = captured.getvalue()
            last_output = output
            if success:
                attempts_log.append({
                    "proxy": name,
                    "success": True,
                    "category": None,
                    "message": "Login successful",
                })
                if cookie:
                    try:
                        config_service.update_config_payload(
                            {"JAVDB_SESSION_COOKIE": cookie}, username
                        )
                    except Exception as exc:  # noqa: BLE001
                        context.logger.warning(
                            "Failed to persist cookie to config: %s", exc
                        )
                context.audit_logger.info(
                    "javdb_refresh_v2 username=%s proxy=%s status=ok", username, name
                )
                return {
                    "status": "ok",
                    "error_category": None,
                    "message": f"Logged in via {name}",
                    "proxy_used": name,
                    "attempts": attempts_log,
                    "output": output[-4000:],
                }
            else:
                category, friendly = _categorize(output)
                attempts_log.append({
                    "proxy": name,
                    "success": False,
                    "category": category,
                    "message": friendly,
                })
                # Wrong credentials → no point burning more proxies on the same creds.
                if category == "invalid_credentials":
                    context.audit_logger.info(
                        "javdb_refresh_v2 username=%s proxy=%s status=failed category=%s",
                        username, name, category,
                    )
                    return {
                        "status": "failed",
                        "error_category": category,
                        "message": friendly,
                        "proxy_used": name,
                        "attempts": attempts_log,
                        "output": output[-4000:],
                    }
        except Exception as exc:  # noqa: BLE001
            output = captured.getvalue() + f"\nException: {exc}"
            last_output = output
            exc_str = str(exc).lower()
            if any(s in exc_str for s in ("timeout", "refused", "resolve", "getaddrinfo")):
                category = "connection_error"
            else:
                category = "unknown"
            attempts_log.append({
                "proxy": name,
                "success": False,
                "category": category,
                "message": str(exc),
            })
        finally:
            for lname in _LOGIN_LOGGER_NAMES:
                logging.getLogger(lname).removeHandler(handler)

    # All attempts exhausted.
    final_category = attempts_log[-1]["category"] if attempts_log else "unknown"
    last_msg = attempts_log[-1]["message"] if attempts_log else "unknown"
    summary_category = "no_proxy_succeeded" if len(attempts_log) > 1 else final_category
    context.audit_logger.info(
        "javdb_refresh_v2 username=%s attempts=%d status=failed category=%s",
        username, len(attempts_log), summary_category,
    )
    return {
        "status": "failed",
        "error_category": summary_category,
        "message": (
            f"All {len(attempts_log)} proxy attempt(s) failed; last error: {last_msg}"
        ),
        "proxy_used": None,
        "attempts": attempts_log,
        "output": last_output[-4000:],
    }


async def parse_index_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_index_page(payload.html, payload.page_num))
    except Exception as exc:
        _raise_internal_error("Failed to parse index page", exc)


async def parse_detail_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_detail_page(payload.html))
    except Exception as exc:
        _raise_internal_error("Failed to parse detail page", exc)


async def parse_category_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_category_page(payload.html, payload.page_num))
    except Exception as exc:
        _raise_internal_error("Failed to parse category page", exc)


async def parse_top_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_top_page(payload.html, payload.page_num))
    except Exception as exc:
        _raise_internal_error("Failed to parse top page", exc)


async def parse_tags_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_tag_page(payload.html, payload.page_num))
    except Exception as exc:
        _raise_internal_error("Failed to parse tag page", exc)


async def detect_page_type_payload(payload: Any) -> Dict[str, str]:
    try:
        return {"page_type": detect_page_type(payload.html)}
    except Exception as exc:
        _raise_internal_error("Failed to detect page type", exc)


async def parse_url_payload(payload: Any) -> Dict[str, Any]:
    _validate_target_url(payload.url)
    try:
        runtime_module = _runtime_facade()
        gateway = runtime_module.create_gateway(
            use_proxy=payload.use_proxy,
            use_cf_bypass=payload.use_cf_bypass,
            use_cookie=payload.use_cookie,
        )
        result = gateway.fetch_and_parse(payload.url, page_num=payload.page_num)
        return result.to_dict()
    except Exception as exc:
        _raise_internal_error("Failed to fetch and parse URL", exc)


async def crawl_index_payload(payload: Any) -> Dict[str, Any]:
    _validate_target_url(payload.url)
    try:
        runtime_module = _runtime_facade()
        gateway = runtime_module.create_gateway(
            use_proxy=payload.use_proxy,
            use_cf_bypass=payload.use_cf_bypass,
            use_cookie=payload.use_cookie,
        )
        result = gateway.crawl_pages(
            payload.url,
            start_page=payload.start_page,
            end_page=payload.end_page,
            crawl_all=payload.crawl_all,
            max_consecutive_empty=payload.max_consecutive_empty,
            page_delay=payload.page_delay,
        )
        return result.to_dict()
    except Exception as exc:
        _raise_internal_error("Failed to crawl index pages", exc)


__all__ = [
    "crawl_index_payload",
    "detect_page_type_payload",
    "health_payload",
    "parse_category_payload",
    "parse_detail_payload",
    "parse_index_payload",
    "parse_tags_payload",
    "parse_top_payload",
    "parse_url_payload",
    "refresh_javdb_session_payload",
    "refresh_javdb_session_with_options",
    "run_health_check_payload",
]
