"""
Thin FastAPI REST layer wrapping the parsing API.

Run with::

    uvicorn api.server:app --reload --port 8100
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Literal, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from api.parsers import (
    parse_index_page,
    parse_detail_page,
    parse_category_page,
    parse_top_page,
    parse_tag_page,
    detect_page_type,
    RUST_PARSERS_AVAILABLE,
)
from utils.rust_adapters.parser_adapter import result_to_dict
from utils.spider_gateway import create_gateway

RUST_CORE_AVAILABLE = RUST_PARSERS_AVAILABLE


def _build_allowed_hosts() -> frozenset[str]:
    """Derive allowed target hosts from config.BASE_URL + javdb.com defaults."""
    hosts = {'javdb.com', 'www.javdb.com'}
    try:
        import config as cfg
        base_url = getattr(cfg, 'BASE_URL', '')
        if base_url:
            parsed = urlparse(base_url)
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
    except ImportError:
        pass
    return frozenset(hosts)


_ALLOWED_HOSTS = _build_allowed_hosts()


def _validate_target_url(url: str) -> None:
    """Reject URLs whose scheme/host fall outside the allowlist (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise HTTPException(
            status_code=400,
            detail=f'URL scheme must be http or https, got {parsed.scheme!r}',
        )
    host = (parsed.hostname or '').lower()
    if host not in _ALLOWED_HOSTS:
        raise HTTPException(
            status_code=400,
            detail=f'Host {host!r} is not in the allowed domain list',
        )


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


class UrlPayload(BaseModel):
    """POST body for the fetch-and-parse endpoint."""
    url: str
    page_num: int = 1
    use_proxy: bool = True
    use_cf_bypass: bool = True
    use_cookie: bool = False


class CrawlIndexPayload(BaseModel):
    """POST body for multi-page index crawl."""
    url: str
    start_page: int = 1
    end_page: Optional[int] = None
    crawl_all: bool = False
    use_proxy: bool = True
    use_cf_bypass: bool = True
    use_cookie: bool = False
    max_consecutive_empty: int = 2
    page_delay: float = 1.0


class SpiderJobPayload(BaseModel):
    """POST body to submit a full spider run."""
    url: Optional[str] = None
    start_page: int = 1
    end_page: Optional[int] = None
    crawl_all: bool = False
    phase: Literal['1', '2', 'all'] = 'all'
    ignore_history: bool = False
    use_history: bool = False
    ignore_release_date: bool = False
    use_proxy: bool = True
    no_rclone_filter: bool = False
    disable_all_filters: bool = False
    enable_dedup: bool = False
    enable_redownload: bool = False
    redownload_threshold: Optional[float] = None
    dry_run: bool = False
    max_movies_phase1: Optional[int] = None
    max_movies_phase2: Optional[int] = None


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
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/detail')
async def api_parse_detail(payload: HtmlPayload):
    """Parse a movie detail page and return full metadata."""
    try:
        result = parse_detail_page(payload.html)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/category')
async def api_parse_category(payload: HtmlPayload):
    """Parse a category page (maker, publisher, series, director, etc.)."""
    try:
        result = parse_category_page(payload.html, payload.page_num)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/top')
async def api_parse_top(payload: HtmlPayload):
    """Parse a top / ranking page."""
    try:
        result = parse_top_page(payload.html, payload.page_num)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/tags')
async def api_parse_tags(payload: HtmlPayload):
    """Parse a tag filter page, returning movie list + full filter panel
    with tag ID ↔ name mappings."""
    try:
        result = parse_tag_page(payload.html, payload.page_num)
        return result_to_dict(result)
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


@app.post('/api/parse/url')
async def api_parse_url(payload: UrlPayload):
    """Fetch a JavDB URL, auto-detect page type, parse and return structured data."""
    _validate_target_url(payload.url)
    try:
        gw = create_gateway(
            use_proxy=payload.use_proxy,
            use_cf_bypass=payload.use_cf_bypass,
            use_cookie=payload.use_cookie,
        )
        gr = gw.fetch_and_parse(payload.url, page_num=payload.page_num)
        return gr.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Crawl endpoints (multi-page fetch + parse)
# ---------------------------------------------------------------------------

@app.post('/api/crawl/index')
async def api_crawl_index(payload: CrawlIndexPayload):
    """Crawl multiple index pages and return aggregated results."""
    _validate_target_url(payload.url)
    try:
        gw = create_gateway(
            use_proxy=payload.use_proxy,
            use_cf_bypass=payload.use_cf_bypass,
            use_cookie=payload.use_cookie,
        )
        cr = gw.crawl_pages(
            payload.url,
            start_page=payload.start_page,
            end_page=payload.end_page,
            crawl_all=payload.crawl_all,
            max_consecutive_empty=payload.max_consecutive_empty,
            page_delay=payload.page_delay,
        )
        return cr.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Spider Job API (async subprocess execution)
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_JOBS = 2
_job_semaphore = threading.Semaphore(_MAX_CONCURRENT_JOBS)
_MAX_OUTPUT_LINES = 5000
_JOB_TTL_SECONDS = 24 * 3600

_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _cleanup_expired_jobs() -> None:
    """Remove finished jobs older than _JOB_TTL_SECONDS (caller must hold _jobs_lock)."""
    now = datetime.now(timezone.utc)
    expired = [
        jid for jid, job in _jobs.items()
        if job.get('finished_at') and
        (now - datetime.fromisoformat(job['finished_at'])).total_seconds() > _JOB_TTL_SECONDS
    ]
    for jid in expired:
        del _jobs[jid]


def _payload_to_cli_args(payload: SpiderJobPayload) -> list[str]:
    """Convert a SpiderJobPayload to CLI argument list."""
    args: list[str] = []
    if payload.url:
        args.extend(['--url', payload.url])
    if payload.start_page != 1:
        args.extend(['--start-page', str(payload.start_page)])
    if payload.end_page is not None:
        args.extend(['--end-page', str(payload.end_page)])
    if payload.crawl_all:
        args.append('--all')
    if payload.phase != 'all':
        args.extend(['--phase', payload.phase])
    if payload.use_proxy:
        args.append('--use-proxy')
    if payload.ignore_history:
        args.append('--ignore-history')
    if payload.use_history:
        args.append('--use-history')
    if payload.ignore_release_date:
        args.append('--ignore-release-date')
    if payload.no_rclone_filter:
        args.append('--no-rclone-filter')
    if payload.disable_all_filters:
        args.append('--disable-all-filters')
    if payload.enable_dedup:
        args.append('--enable-dedup')
    if payload.enable_redownload:
        args.append('--enable-redownload')
    if payload.redownload_threshold is not None:
        args.extend(['--redownload-threshold', str(payload.redownload_threshold)])
    if payload.dry_run:
        args.append('--dry-run')
    if payload.max_movies_phase1 is not None:
        args.extend(['--max-movies-phase1', str(payload.max_movies_phase1)])
    if payload.max_movies_phase2 is not None:
        args.extend(['--max-movies-phase2', str(payload.max_movies_phase2)])
    return args


def _run_spider_job(job_id: str, cli_args: list[str]) -> None:
    """Run spider subprocess and stream output into the job record."""
    cmd = [sys.executable, '-m', 'scripts.spider'] + cli_args
    logger.info('Spider job %s starting: %s', job_id, ' '.join(cmd))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        with _jobs_lock:
            _jobs[job_id]['pid'] = process.pid

        output_lines: list[str] = []
        csv_path: Optional[str] = None
        session_id: Optional[str] = None

        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                stripped = line.rstrip('\n')
                output_lines.append(stripped)
                if stripped.startswith('SPIDER_OUTPUT_CSV='):
                    csv_path = stripped.split('=', 1)[1].strip()
                elif stripped.startswith('SPIDER_SESSION_ID='):
                    session_id = stripped.split('=', 1)[1].strip()
            process.stdout.close()

        return_code = process.wait()

        with _jobs_lock:
            job = _jobs[job_id]
            job['status'] = 'completed' if return_code == 0 else 'failed'
            job['return_code'] = return_code
            job['finished_at'] = datetime.now(timezone.utc).isoformat()
            job['output'] = output_lines[-_MAX_OUTPUT_LINES:]
            if csv_path:
                job['csv_path'] = csv_path
            if session_id:
                job['session_id'] = session_id

    except Exception as exc:
        with _jobs_lock:
            job = _jobs[job_id]
            job['status'] = 'failed'
            job['error'] = str(exc)
            job['finished_at'] = datetime.now(timezone.utc).isoformat()
    finally:
        _job_semaphore.release()


@app.post('/api/jobs/spider')
async def api_submit_spider_job(payload: SpiderJobPayload):
    """Submit a full spider run as an async background job."""
    if payload.url:
        _validate_target_url(payload.url)

    if not _job_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f'Maximum concurrent spider jobs ({_MAX_CONCURRENT_JOBS}) reached, try again later',
        )

    job_id = uuid.uuid4().hex[:12]
    cli_args = _payload_to_cli_args(payload)

    try:
        with _jobs_lock:
            _cleanup_expired_jobs()
            _jobs[job_id] = {
                'job_id': job_id,
                'status': 'running',
                'pid': None,
                'cli_args': cli_args,
                'started_at': datetime.now(timezone.utc).isoformat(),
                'finished_at': None,
                'return_code': None,
                'output': [],
                'csv_path': None,
                'session_id': None,
                'error': None,
            }

        thread = threading.Thread(
            target=_run_spider_job, args=(job_id, cli_args), daemon=True,
        )
        thread.start()
    except Exception as exc:
        logger.error('Failed to start spider job %s: %s', job_id, exc)
        with _jobs_lock:
            _jobs.pop(job_id, None)
        _job_semaphore.release()
        raise HTTPException(
            status_code=503,
            detail='Failed to start spider job, please try again later',
        )

    return {'job_id': job_id, 'status': 'running', 'cli_args': cli_args}


@app.get('/api/jobs/{job_id}/status')
async def api_get_job_status(job_id: str):
    """Query the status and output of a spider job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f'Job {job_id} not found')
    return job
