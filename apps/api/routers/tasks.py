"""Task and spider-job routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.payloads import (
    AdhocTaskPayload,
    DailyTaskPayload,
    JobSummaryResponse,
    ListTasksResponse,
    SpiderJobPayload,
    SpiderJobStatusResponse,
    SpiderJobSubmitResponse,
    TaskStatsResponse,
    TriggerTaskResponse,
)
from apps.api.services import spider_jobs, task_service

router = APIRouter(prefix="/api")


@router.post("/tasks/daily", response_model=TriggerTaskResponse)
async def trigger_daily(
    payload: DailyTaskPayload,
    current=Depends(require_role("admin")),
):
    return task_service.trigger_daily_task(payload, current["sub"])


@router.post("/tasks/adhoc", response_model=TriggerTaskResponse)
async def trigger_adhoc(
    payload: AdhocTaskPayload,
    current=Depends(require_role("admin")),
):
    return task_service.trigger_adhoc_task(payload, current["sub"])


@router.get("/tasks", response_model=ListTasksResponse)
async def list_tasks(limit: int = 200, current=Depends(_require_auth)):
    return task_service.list_tasks_payload(limit, current["sub"])


@router.get("/tasks/stats", response_model=TaskStatsResponse)
async def task_stats(current=Depends(_require_auth)):
    return task_service.task_stats_payload(current["sub"])


@router.get("/tasks/{job_id}", response_model=JobSummaryResponse)
async def get_task(job_id: str, current=Depends(_require_auth)):
    return task_service.get_task_payload(job_id, current["sub"])


@router.get("/tasks/{job_id}/stream")
async def get_task_stream(
    job_id: str,
    offset: int = 0,
    current=Depends(_require_auth),
):
    return task_service.get_task_stream_payload(job_id, offset, current["sub"])


@router.post("/jobs/spider", response_model=SpiderJobSubmitResponse)
async def api_submit_spider_job(
    payload: SpiderJobPayload,
    _: dict = Depends(require_role("admin")),
):
    return spider_jobs.submit_spider_job(payload)


@router.get("/jobs/{job_id}/status", response_model=SpiderJobStatusResponse)
async def api_get_spider_job_status(job_id: str, _: dict = Depends(_require_auth)):
    return spider_jobs.get_spider_job_status(job_id)


__all__ = [
    "api_get_spider_job_status",
    "api_submit_spider_job",
    "get_task",
    "get_task_stream",
    "list_tasks",
    "router",
    "task_stats",
    "trigger_adhoc",
    "trigger_daily",
]
