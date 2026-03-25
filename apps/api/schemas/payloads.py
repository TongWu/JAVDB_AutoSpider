"""Request and response schema definitions for the API layer."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.api.infra.security import (
    _is_valid_javdb_host,
    _sanitize_output_filename,
)


class HtmlPayload(BaseModel):
    html: str = Field(..., max_length=5 * 1024 * 1024)
    page_num: int = Field(1, ge=1, le=9999)


class UrlPayload(BaseModel):
    url: str
    page_num: int = 1
    use_proxy: bool = True
    use_cf_bypass: bool = True
    use_cookie: bool = False


class CrawlIndexPayload(BaseModel):
    url: str
    start_page: int = 1
    end_page: Optional[int] = None
    crawl_all: bool = False
    use_proxy: bool = True
    use_cf_bypass: bool = True
    use_cookie: bool = False
    max_consecutive_empty: int = 2
    page_delay: float = 1.0


class CliProxyOverridePayload(BaseModel):
    use_proxy: bool = False
    no_proxy: bool = False

    @model_validator(mode="after")
    def validate_proxy_override(self):
        if self.use_proxy and self.no_proxy:
            raise ValueError("use_proxy and no_proxy cannot both be true")
        return self


class SpiderJobPayload(CliProxyOverridePayload):
    url: Optional[str] = None
    start_page: int = 1
    end_page: Optional[int] = None
    crawl_all: bool = False
    phase: Literal["1", "2", "all"] = "all"
    ignore_history: bool = False
    use_history: bool = False
    ignore_release_date: bool = False
    no_rclone_filter: bool = False
    disable_all_filters: bool = False
    enable_dedup: bool = False
    enable_redownload: bool = False
    redownload_threshold: Optional[float] = None
    dry_run: bool = False
    max_movies_phase1: Optional[int] = None
    max_movies_phase2: Optional[int] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    rust_core_available: bool = False


class LoginPayload(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class DailyTaskPayload(CliProxyOverridePayload):
    start_page: int = Field(1, ge=1, le=200)
    end_page: int = Field(10, ge=1, le=200)
    all: bool = False
    ignore_history: bool = False
    phase: str = Field("all")
    output_file: Optional[str] = None
    dry_run: bool = False
    ignore_release_date: bool = False
    max_movies_phase1: Optional[int] = Field(None, ge=1, le=10000)
    max_movies_phase2: Optional[int] = Field(None, ge=1, le=10000)
    pikpak_individual: bool = False
    mode: str = Field("pipeline")

    @field_validator("phase")
    @classmethod
    def valid_phase(cls, value: str) -> str:
        if value not in {"1", "2", "all"}:
            raise ValueError("phase must be one of 1, 2, all")
        return value

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"pipeline", "spider"}:
            raise ValueError("mode must be one of pipeline, spider")
        return value

    @field_validator("end_page")
    @classmethod
    def valid_page_range(cls, value: int, info) -> int:
        start_page = info.data.get("start_page", 1)
        if value < start_page:
            raise ValueError("end_page must be >= start_page")
        return value

    @field_validator("output_file")
    @classmethod
    def valid_output_file(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _sanitize_output_filename(value)


class AdhocTaskPayload(CliProxyOverridePayload):
    url: str = Field(..., min_length=1, max_length=2048)
    start_page: int = Field(1, ge=1, le=200)
    end_page: int = Field(1, ge=1, le=200)
    history_filter: bool = False
    date_filter: bool = False
    phase: str = Field("all")
    qb_category: Optional[str] = Field(None, max_length=255)
    dry_run: bool = False
    ignore_release_date: bool = True
    max_movies_phase1: Optional[int] = Field(None, ge=1, le=10000)
    max_movies_phase2: Optional[int] = Field(None, ge=1, le=10000)

    @field_validator("phase")
    @classmethod
    def valid_phase(cls, value: str) -> str:
        if value not in {"1", "2", "all"}:
            raise ValueError("phase must be one of 1, 2, all")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not _is_valid_javdb_host(value):
            raise ValueError("url must target a valid javdb.com host")
        return value

    @field_validator("end_page")
    @classmethod
    def valid_page_range(cls, value: int, info) -> int:
        start_page = info.data.get("start_page", 1)
        if value < start_page:
            raise ValueError("end_page must be >= start_page")
        return value


class HealthCheckPayload(CliProxyOverridePayload):
    check_smtp: bool = True


class ExploreResolvePayload(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    page_num: int = Field(1, ge=1, le=9999)
    use_proxy: bool = True
    use_cookie: bool = True

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not _is_valid_javdb_host(value):
            raise ValueError("url must target a valid javdb.com host")
        return value


class ExploreCookiePayload(BaseModel):
    cookie: str = Field(..., min_length=1, max_length=4096)


class ExploreMagnetPayload(BaseModel):
    magnet: str = Field(..., min_length=1, max_length=4096)
    title: str = Field("", max_length=255)
    category: Optional[str] = Field(None, max_length=255)

    @field_validator("magnet")
    @classmethod
    def valid_magnet(cls, value: str) -> str:
        if not value.startswith("magnet:?"):
            raise ValueError("magnet must start with magnet:?")
        return value


class ExploreOneClickPayload(BaseModel):
    detail_url: str = Field(..., min_length=1, max_length=2048)
    use_proxy: bool = True
    use_cookie: bool = True
    category: Optional[str] = Field(None, max_length=255)

    @field_validator("detail_url")
    @classmethod
    def valid_detail_url(cls, value: str) -> str:
        if not _is_valid_javdb_host(value):
            raise ValueError("detail_url must target a valid javdb.com host")
        return value


class ExploreIndexStatusPayload(BaseModel):
    movies: list[dict[str, str]] = Field(default_factory=list)
    use_proxy: bool = True
    use_cookie: bool = True


__all__ = [
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
    "SpiderJobPayload",
    "UrlPayload",
]
