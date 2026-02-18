"""
Thin FastAPI REST layer wrapping the parsing API.

Run with::

    uvicorn api.server:app --reload --port 8100
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from javdb_rust_core import (
        parse_index_page,
        parse_detail_page,
        parse_category_page,
        parse_top_page,
        parse_tag_page,
        detect_page_type,
    )
    RUST_CORE_AVAILABLE = True
    logger.info("✅ Rust core loaded successfully - API server using high-performance Rust implementation")
except ImportError as e:
    from api.parsers import (
        parse_index_page,
        parse_detail_page,
        parse_category_page,
        parse_top_page,
        parse_tag_page,
        detect_page_type,
    )
    RUST_CORE_AVAILABLE = False
    logger.warning(f"⚠️  Rust core not available (ImportError: {e}) - API server falling back to pure-Python implementation")


def _result_to_dict(result):
    """Convert a parser result to dict.

    Rust PyO3 objects expose ``to_dict()``; Python dataclasses use
    ``dataclasses.asdict()``.
    """
    if hasattr(result, 'to_dict'):
        return result.to_dict()
    return asdict(result)

app = FastAPI(
    title='JAVDB AutoSpider API',
    version='0.1.0',
    description='Structured parsing API for JavDB HTML pages.',
)


# ---------------------------------------------------------------------------
# Request / response schemas (Pydantic models for FastAPI validation)
# ---------------------------------------------------------------------------

class HtmlPayload(BaseModel):
    """POST body for all parse endpoints."""
    html: str
    page_num: int = 1


class HealthResponse(BaseModel):
    status: str = 'ok'
    rust_core_available: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get('/api/health', response_model=HealthResponse)
async def health_check():
    """Simple liveness probe with Rust core status."""
    return HealthResponse(rust_core_available=RUST_CORE_AVAILABLE)


@app.post('/api/parse/index')
async def api_parse_index(payload: HtmlPayload):
    """Parse a normal index / home page and return all movie entries."""
    try:
        result = parse_index_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/detail')
async def api_parse_detail(payload: HtmlPayload):
    """Parse a movie detail page and return full metadata."""
    try:
        result = parse_detail_page(payload.html)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/category')
async def api_parse_category(payload: HtmlPayload):
    """Parse a category page (maker, publisher, series, director, etc.)."""
    try:
        result = parse_category_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/top')
async def api_parse_top(payload: HtmlPayload):
    """Parse a top / ranking page."""
    try:
        result = parse_top_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/tags')
async def api_parse_tags(payload: HtmlPayload):
    """Parse a tag filter page, returning movie list + full filter panel
    with tag ID ↔ name mappings."""
    try:
        result = parse_tag_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/detect-page-type')
async def api_detect_page_type(payload: HtmlPayload):
    """Detect the type of a JavDB page from its HTML."""
    try:
        page_type = detect_page_type(payload.html)
        return {'page_type': page_type}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
