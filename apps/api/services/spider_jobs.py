"""Background spider job services."""

from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from apps.api.infra.security import _validate_target_url
from apps.api.services import context
from packages.python.javdb_platform.proxy_policy import resolve_proxy_override

MAX_CONCURRENT_SPIDER_JOBS = 2
MAX_OUTPUT_LINES = 5000
SPIDER_JOB_TTL_SECONDS = 24 * 3600

_spider_job_semaphore = threading.Semaphore(MAX_CONCURRENT_SPIDER_JOBS)
_spider_jobs: Dict[str, dict] = {}
_spider_jobs_lock = threading.Lock()


def _cleanup_expired_spider_jobs() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        job_id
        for job_id, job in _spider_jobs.items()
        if job.get("finished_at")
        and (
            now - datetime.fromisoformat(job["finished_at"])
        ).total_seconds() > SPIDER_JOB_TTL_SECONDS
    ]
    for job_id in expired:
        del _spider_jobs[job_id]


def _payload_to_cli_args(payload: Any) -> list[str]:
    args: list[str] = []
    if payload.url:
        args.extend(["--url", payload.url])
    if payload.start_page != 1:
        args.extend(["--start-page", str(payload.start_page)])
    if payload.end_page is not None:
        args.extend(["--end-page", str(payload.end_page)])
    if payload.crawl_all:
        args.append("--all")
    if payload.phase != "all":
        args.extend(["--phase", payload.phase])
    proxy_override = resolve_proxy_override(
        bool(getattr(payload, "use_proxy", False)),
        bool(getattr(payload, "no_proxy", False)),
    )
    if proxy_override is True:
        args.append("--use-proxy")
    elif proxy_override is False:
        args.append("--no-proxy")
    if payload.ignore_history:
        args.append("--ignore-history")
    if payload.use_history:
        args.append("--use-history")
    if payload.ignore_release_date:
        args.append("--ignore-release-date")
    if payload.no_rclone_filter:
        args.append("--no-rclone-filter")
    if payload.disable_all_filters:
        args.append("--disable-all-filters")
    if payload.enable_dedup:
        args.append("--enable-dedup")
    if payload.enable_redownload:
        args.append("--enable-redownload")
    if payload.redownload_threshold is not None:
        args.extend(["--redownload-threshold", str(payload.redownload_threshold)])
    if payload.dry_run:
        args.append("--dry-run")
    if payload.max_movies_phase1 is not None:
        args.extend(["--max-movies-phase1", str(payload.max_movies_phase1)])
    if payload.max_movies_phase2 is not None:
        args.extend(["--max-movies-phase2", str(payload.max_movies_phase2)])
    return args


def _run_spider_job(job_id: str, cli_args: list[str]) -> None:
    cmd = [sys.executable, "-m", "apps.cli.spider"] + cli_args
    context.logger.info("Spider job %s starting: %s", job_id, " ".join(cmd))
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with _spider_jobs_lock:
            _spider_jobs[job_id]["pid"] = process.pid

        output_lines: list[str] = []
        csv_path: Optional[str] = None
        session_id: Optional[str] = None
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                stripped = line.rstrip("\n")
                output_lines.append(stripped)
                if stripped.startswith("SPIDER_OUTPUT_CSV="):
                    csv_path = stripped.split("=", 1)[1].strip()
                elif stripped.startswith("SPIDER_SESSION_ID="):
                    session_id = stripped.split("=", 1)[1].strip()
            process.stdout.close()

        return_code = process.wait()
        with _spider_jobs_lock:
            job = _spider_jobs[job_id]
            job["status"] = "completed" if return_code == 0 else "failed"
            job["return_code"] = return_code
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            job["output"] = output_lines[-MAX_OUTPUT_LINES:]
            if csv_path:
                job["csv_path"] = csv_path
            if session_id:
                job["session_id"] = session_id
    except Exception as exc:
        with _spider_jobs_lock:
            job = _spider_jobs[job_id]
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        _spider_job_semaphore.release()


def submit_spider_job(payload: Any) -> Dict[str, Any]:
    if payload.url:
        _validate_target_url(payload.url)
    if not _spider_job_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Maximum concurrent spider jobs ({MAX_CONCURRENT_SPIDER_JOBS}) "
                "reached, try again later"
            ),
        )
    job_id = uuid.uuid4().hex[:12]
    cli_args = _payload_to_cli_args(payload)
    try:
        with _spider_jobs_lock:
            _cleanup_expired_spider_jobs()
            _spider_jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "pid": None,
                "cli_args": cli_args,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "return_code": None,
                "output": [],
                "csv_path": None,
                "session_id": None,
                "error": None,
            }
        thread = threading.Thread(
            target=_run_spider_job,
            args=(job_id, cli_args),
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        context.logger.error("Failed to start spider job %s: %s", job_id, exc)
        with _spider_jobs_lock:
            _spider_jobs.pop(job_id, None)
        _spider_job_semaphore.release()
        raise HTTPException(
            status_code=503,
            detail="Failed to start spider job, please try again later",
        ) from exc
    return {"job_id": job_id, "status": "running", "cli_args": cli_args}


def get_spider_job_status(job_id: str) -> Dict[str, Any]:
    with _spider_jobs_lock:
        job = _spider_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


__all__ = [
    "_payload_to_cli_args",
    "get_spider_job_status",
    "submit_spider_job",
]
