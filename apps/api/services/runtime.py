"""FastAPI bootstrap and compatibility facade for the canonical API stack."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.infra.auth import (
    ACCESS_TOKEN_EXPIRE_SECONDS,
    ACTIVE_TOKENS,
    API_SECRET_KEY,
    MAX_SESSIONS_PER_USER,
    PASSWORD_CTX,
    READONLY_USERNAME,
    REFRESH_TOKEN_EXPIRE_SECONDS,
    REVOKED_JTI,
    USERS,
    _AUTH_LOCK,
    _access_token_from_request,
    _bearer_token,
    _jwt_decode,
    _jwt_encode,
    _prune_revoked_jti,
    _prune_sessions,
    _rate_limit,
    _require_auth,
    _require_auth_or_token,
    _verify_csrf,
    require_role,
)
from apps.api.infra.security import (
    ALLOWED_HOSTS,
    _is_valid_javdb_host,
    _resolve_public_target_or_422,
    _sanitize_output_filename,
    _validate_target_url,
)
from apps.api.parsers import RUST_PARSERS_AVAILABLE
from apps.api.routers.auth import login, logout, refresh_token, router as auth_router
from apps.api.routers.config import (
    get_config,
    get_config_meta,
    router as config_router,
    update_config,
)
from apps.api.routers.explore import (
    explore_download_magnet,
    explore_index_status,
    explore_one_click,
    explore_proxy_page,
    explore_resolve,
    explore_search_by_video_code,
    explore_sync_cookie,
    router as explore_router,
)
from apps.api.routers.system import (
    api_crawl_index,
    api_detect_page_type,
    api_parse_category,
    api_parse_detail,
    api_parse_index,
    api_parse_tags,
    api_parse_top,
    api_parse_url,
    health_check,
    refresh_javdb_session,
    router as system_router,
    run_health_check,
)
from apps.api.routers.tasks import (
    api_get_spider_job_status,
    api_submit_spider_job,
    get_task,
    get_task_stream,
    list_tasks,
    router as tasks_router,
    task_stats,
    trigger_adhoc,
    trigger_daily,
)
from apps.api.schemas.payloads import (
    AdhocTaskPayload,
    CrawlIndexPayload,
    DailyTaskPayload,
    ExploreCookiePayload,
    ExploreIndexStatusPayload,
    ExploreMagnetPayload,
    ExploreOneClickPayload,
    ExploreResolvePayload,
    HealthCheckPayload,
    HealthResponse,
    HtmlPayload,
    LoginPayload,
    SpiderJobPayload,
    UrlPayload,
    VideoCodeSearchPayload,
)
from apps.api.services import config_service, context, explore_service, spider_jobs, task_service
from packages.python.javdb_platform.spider_gateway import create_gateway

RUST_CORE_AVAILABLE = RUST_PARSERS_AVAILABLE
_payload_to_cli_args = spider_jobs._payload_to_cli_args

app = FastAPI(
    title="JAVDB AutoSpider API",
    version="0.2.0",
    description="Fullstack API for config, tasks and parsing.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=context.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(_: Request, exc: Exception):
    context.logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.middleware("http")
async def auth_csrf_middleware(request: Request, call_next):
    if request.url.path in {
        "/api/health",
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/explore/proxy-page",
    }:
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        try:
            if not request.url.path.startswith("/api/explore/"):
                _verify_csrf(request)
        except Exception as exc:
            if hasattr(exc, "status_code") and hasattr(exc, "detail"):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )
            raise
    return await call_next(request)


for router in (
    system_router,
    auth_router,
    config_router,
    tasks_router,
    explore_router,
):
    app.include_router(router)


__all__ = [
    "ACCESS_TOKEN_EXPIRE_SECONDS",
    "ACTIVE_TOKENS",
    "ALLOWED_HOSTS",
    "API_SECRET_KEY",
    "AdhocTaskPayload",
    "CrawlIndexPayload",
    "DailyTaskPayload",
    "ExploreCookiePayload",
    "ExploreIndexStatusPayload",
    "ExploreMagnetPayload",
    "ExploreOneClickPayload",
    "ExploreResolvePayload",
    "HealthCheckPayload",
    "HealthResponse",
    "HtmlPayload",
    "LoginPayload",
    "MAX_SESSIONS_PER_USER",
    "PASSWORD_CTX",
    "READONLY_USERNAME",
    "REFRESH_TOKEN_EXPIRE_SECONDS",
    "REVOKED_JTI",
    "RUST_CORE_AVAILABLE",
    "SpiderJobPayload",
    "USERS",
    "UrlPayload",
    "VideoCodeSearchPayload",
    "_AUTH_LOCK",
    "_access_token_from_request",
    "_bearer_token",
    "_is_valid_javdb_host",
    "_jwt_decode",
    "_jwt_encode",
    "_payload_to_cli_args",
    "_prune_revoked_jti",
    "_prune_sessions",
    "_rate_limit",
    "_require_auth",
    "_require_auth_or_token",
    "_resolve_public_target_or_422",
    "_sanitize_output_filename",
    "_validate_target_url",
    "_verify_csrf",
    "api_crawl_index",
    "api_detect_page_type",
    "api_get_spider_job_status",
    "api_parse_category",
    "api_parse_detail",
    "api_parse_index",
    "api_parse_tags",
    "api_parse_top",
    "api_parse_url",
    "api_submit_spider_job",
    "app",
    "auth_csrf_middleware",
    "config_service",
    "create_gateway",
    "explore_download_magnet",
    "explore_index_status",
    "explore_one_click",
    "explore_proxy_page",
    "explore_resolve",
    "explore_search_by_video_code",
    "explore_service",
    "explore_sync_cookie",
    "get_config",
    "get_config_meta",
    "get_task",
    "get_task_stream",
    "global_exception_handler",
    "health_check",
    "list_tasks",
    "login",
    "logout",
    "refresh_javdb_session",
    "refresh_token",
    "require_role",
    "run_health_check",
    "spider_jobs",
    "task_service",
    "task_stats",
    "trigger_adhoc",
    "trigger_daily",
    "update_config",
]
