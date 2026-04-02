"""Explore and one-click routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from apps.api.infra.auth import _require_auth, _require_auth_or_token, require_role
from apps.api.schemas.payloads import (
    ExploreCookiePayload,
    ExploreIndexStatusPayload,
    ExploreMagnetPayload,
    ExploreOneClickPayload,
    ExploreResolvePayload,
    VideoCodeSearchPayload,
)
from apps.api.services import explore_service
from apps.api.services import video_code_search_service

router = APIRouter(prefix="/api/explore")


@router.post("/sync-cookie")
async def explore_sync_cookie(
    payload: ExploreCookiePayload,
    current=Depends(require_role("admin")),
):
    return explore_service.sync_cookie_payload(payload.cookie, current["sub"])


@router.get("/proxy-page", response_class=HTMLResponse)
async def explore_proxy_page(url: str, current=Depends(_require_auth_or_token)):
    return await explore_service.proxy_page_payload(url, current["sub"])


@router.post("/resolve")
async def explore_resolve(
    payload: ExploreResolvePayload,
    current=Depends(_require_auth),
):
    return await explore_service.resolve_payload(payload, current["sub"])


@router.post("/download-magnet")
async def explore_download_magnet(
    payload: ExploreMagnetPayload,
    current=Depends(require_role("admin")),
):
    return await explore_service.download_magnet_payload(payload, current["sub"])


@router.post("/one-click")
async def explore_one_click(
    payload: ExploreOneClickPayload,
    current=Depends(require_role("admin")),
):
    return await explore_service.one_click_payload(payload, current["sub"])


@router.post("/index-status")
async def explore_index_status(
    payload: ExploreIndexStatusPayload,
    current=Depends(_require_auth),
):
    return await explore_service.index_status_payload(payload, current["sub"])


@router.post("/search-by-video-code")
async def explore_search_by_video_code(
    payload: VideoCodeSearchPayload,
    current=Depends(_require_auth),
):
    return await video_code_search_service.search_by_video_code(
        payload.video_code,
        use_proxy=payload.use_proxy,
        use_cookie=payload.use_cookie,
        f=payload.f,
    )


__all__ = [
    "explore_download_magnet",
    "explore_index_status",
    "explore_one_click",
    "explore_proxy_page",
    "explore_resolve",
    "explore_search_by_video_code",
    "explore_sync_cookie",
    "router",
]
