"""Schemas for multi-source magnet aggregation (ADR-054 WS3)."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class AggregateMagnetsPayload(BaseModel):
    video_code: str = Field(..., min_length=1, max_length=64)

    @field_validator("video_code", mode="before")
    @classmethod
    def strip_video_code(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("video_code")
    @classmethod
    def reject_url_like_video_code(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or "/" in value or "\\" in value:
            raise ValueError("video_code must be a video code, not a URL")
        return value


class AggregatedMagnet(BaseModel):
    magnet_uri: str
    name: str
    size: str = ""
    tags: list[str] = Field(default_factory=list)
    file_count: int = 0
    info_hash: str | None = None
    sources: list[str] = Field(default_factory=list)
    quality_score: float = 0.0
    quality_reasons: list[str] = Field(default_factory=list)


class AggregateMagnetsResponse(BaseModel):
    video_code: str
    magnets: list[AggregatedMagnet]
