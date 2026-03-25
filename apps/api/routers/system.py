"""System, parser, crawl, and maintenance routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.payloads import (
    CrawlIndexPayload,
    HealthCheckPayload,
    HealthResponse,
    HtmlPayload,
    UrlPayload,
)
from apps.api.services import system_service

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return system_service.health_payload()


@router.post("/health-check")
async def run_health_check(
    payload: HealthCheckPayload,
    current=Depends(require_role("admin")),
):
    return await system_service.run_health_check_payload(payload, current["sub"])


@router.post("/login/refresh")
async def refresh_javdb_session(current=Depends(require_role("admin"))):
    return await system_service.refresh_javdb_session_payload(current["sub"])


@router.post("/parse/index")
async def api_parse_index(
    payload: HtmlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.parse_index_payload(payload)


@router.post("/parse/detail")
async def api_parse_detail(
    payload: HtmlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.parse_detail_payload(payload)


@router.post("/parse/category")
async def api_parse_category(
    payload: HtmlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.parse_category_payload(payload)


@router.post("/parse/top")
async def api_parse_top(
    payload: HtmlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.parse_top_payload(payload)


@router.post("/parse/tags")
async def api_parse_tags(
    payload: HtmlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.parse_tags_payload(payload)


@router.post("/detect-page-type")
async def api_detect_page_type(
    payload: HtmlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.detect_page_type_payload(payload)


@router.post("/parse/url")
async def api_parse_url(
    payload: UrlPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.parse_url_payload(payload)


@router.post("/crawl/index")
async def api_crawl_index(
    payload: CrawlIndexPayload,
    _: Dict[str, Any] = Depends(_require_auth),
):
    return await system_service.crawl_index_payload(payload)


__all__ = [
    "api_crawl_index",
    "api_detect_page_type",
    "api_parse_category",
    "api_parse_detail",
    "api_parse_index",
    "api_parse_tags",
    "api_parse_top",
    "api_parse_url",
    "health_check",
    "refresh_javdb_session",
    "router",
    "run_health_check",
]
