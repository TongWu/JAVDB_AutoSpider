"""System, parser, and crawl API services."""

from __future__ import annotations

import importlib
import subprocess
from typing import Any, Dict

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
from packages.python.javdb_platform.bridges.rust_adapters.parser_adapter import (
    result_to_dict,
)


def health_payload() -> Any:
    from apps.api.schemas.payloads import HealthResponse

    return HealthResponse(rust_core_available=RUST_PARSERS_AVAILABLE)


def _runtime_facade():
    return importlib.import_module("apps.api.services.runtime")


async def run_health_check_payload(payload: Any, username: str) -> Dict[str, Any]:
    command = ["python3", "-m", "apps.cli.health_check"]
    if payload.check_smtp:
        command.append("--check-smtp")
    if payload.use_proxy:
        command.append("--use-proxy")
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


async def parse_index_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_index_page(payload.html, payload.page_num))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def parse_detail_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_detail_page(payload.html))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def parse_category_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_category_page(payload.html, payload.page_num))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def parse_top_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_top_page(payload.html, payload.page_num))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def parse_tags_payload(payload: Any) -> Dict[str, Any]:
    try:
        return result_to_dict(parse_tag_page(payload.html, payload.page_num))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def detect_page_type_payload(payload: Any) -> Dict[str, str]:
    try:
        return {"page_type": detect_page_type(payload.html)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    "run_health_check_payload",
]
