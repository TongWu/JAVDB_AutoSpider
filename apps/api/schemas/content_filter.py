"""Pydantic schemas for content-filter CRUD endpoints (ADR-040 Phase 4 / WS4a)."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, StrictBool


class ContentFilterRuleCreate(BaseModel):
    dimension: str
    mode: str
    value: str = ""


class ContentFilterRuleEnabledUpdate(BaseModel):
    # StrictBool (not bool): the TS Worker route rejects a non-boolean `enabled`
    # with 422, so Pydantic must too rather than coercing 1 / "true" -> True.
    # Keeps PUT /api/content-filter/{id} behaviourally identical across backends.
    enabled: StrictBool


class ContentFilterRuleResponse(BaseModel):
    id: int
    dimension: str
    mode: str
    value: str
    enabled: bool


class ContentFilterRuleListResponse(BaseModel):
    items: List[ContentFilterRuleResponse]
    total: int
