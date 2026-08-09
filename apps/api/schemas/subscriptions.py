"""Pydantic schemas for subscriptions + new-works endpoints (ADR-054 WS2)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ActorSubscriptionUpsert(BaseModel):
    actor_name: Optional[str] = None
    active: bool = True


class ActorSubscriptionResponse(BaseModel):
    actor_href: str
    actor_name: Optional[str] = None
    active: bool
    last_seen_href: Optional[str] = None
    last_checked_at: Optional[str] = None
    created_at: str
    updated_at: str


class ActorSubscriptionListResponse(BaseModel):
    items: List[ActorSubscriptionResponse] = Field(max_length=500)
    total: int


class ActorSubscriptionDeleteResponse(BaseModel):
    deleted: bool


class NewWorkResponse(BaseModel):
    video_code: str
    href: str
    actor_href: str
    title: Optional[str] = None
    release_date: Optional[str] = None
    discovered_at: str
    dismissed: bool


class NewWorkListResponse(BaseModel):
    items: List[NewWorkResponse] = Field(max_length=200)
    total: int


class NewWorkDismissResponse(BaseModel):
    dismissed: bool
