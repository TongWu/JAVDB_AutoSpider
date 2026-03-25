"""Task execution, job metadata, and task list services."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException

from apps.api.infra.security import _sanitize_output_filename
from apps.api.services import config_service, context

JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()

_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _job_meta_path(job_id: str) -> Path:
    filename = _safe_job_log_filename(job_id, ".meta.json")
    return _resolved_path_under_job_log_dir(filename)


def _read_job_meta(job_id: str) -> Dict[str, Any]:
    _validate_job_id(job_id)
    path = _job_meta_path(job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_job_meta(job_id: str, payload: Dict[str, Any]) -> None:
    path = _job_meta_path(job_id)
    safe_payload = dict(payload)
    safe_payload["job_id"] = job_id
    path.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _extract_url_from_command(command: list[str]) -> str:
    for idx, token in enumerate(command):
        if token == "--url" and idx + 1 < len(command):
            return str(command[idx + 1]).strip()
    return ""


def _extract_task_mode(kind: str, command: list[str]) -> str:
    if kind == "adhoc":
        return "pipeline"
    command_text = " ".join(command)
    if "apps.cli.spider" in command_text or "scripts/spider" in command_text:
        return "spider"
    return "pipeline"


def _log_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_log_tail(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def _read_log_chunk(
    path: Path,
    offset: int,
    max_bytes: int = context.JOB_STREAM_MAX_BYTES,
) -> tuple[str, int]:
    if not path.exists():
        return "", 0
    size = _log_offset(path)
    if offset < 0:
        offset = 0
    if offset > size:
        offset = size
    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
        fp.seek(offset)
        chunk = fp.read(max_bytes)
        next_offset = fp.tell()
    return chunk, next_offset


def _job_status_from_process(job: Dict[str, Any]) -> str:
    process: subprocess.Popen = job["process"]
    rc = process.poll()
    if rc is None:
        return "running"
    return "success" if rc == 0 else "failed"


def _infer_created_at_from_job_id(job_id: str) -> str:
    match = re.match(r"^[a-zA-Z]+-(\d{8})-(\d{6})-[a-zA-Z0-9]+$", job_id)
    if not match:
        return ""
    try:
        dt = datetime.strptime(
            f"{match.group(1)}{match.group(2)}",
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return ""


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Invalid job_id")
    for sep in (os.sep, os.altsep):
        if sep and sep in job_id:
            raise HTTPException(status_code=422, detail="Invalid job_id")


def _safe_job_log_filename(job_id: str, extension: str) -> str:
    _validate_job_id(job_id)
    if not extension.startswith(".") or len(extension) < 2:
        raise HTTPException(status_code=500, detail="Invalid log filename extension")
    name = f"{job_id}{extension}"
    for sep in (os.sep, os.altsep):
        if sep and sep in name:
            raise HTTPException(status_code=400, detail="Invalid job_id")
    if ".." in name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    if len(Path(name).parts) != 1:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    return name


def _resolved_path_under_job_log_dir(filename: str) -> Path:
    candidate = (context.RESOLVED_JOB_LOG_DIR / filename).resolve()
    try:
        candidate.relative_to(context.RESOLVED_JOB_LOG_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id") from None
    return candidate


def _safe_log_path(job_id: str) -> Path:
    filename = _safe_job_log_filename(job_id, ".log")
    return _resolved_path_under_job_log_dir(filename)


def _normalize_job_kind(job_id: str) -> str:
    if job_id.startswith("daily-"):
        return "daily"
    if job_id.startswith("adhoc-"):
        return "adhoc"
    return "unknown"


def _job_summary(job_id: str, job: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if job:
        status = _job_status_from_process(job)
        created_at = str(job.get("created_at", ""))
        completed_at = str(job.get("completed_at", ""))
        if status in {"success", "failed"} and not completed_at:
            completed_at = datetime.now(timezone.utc).isoformat()
            job["completed_at"] = completed_at
        command = job.get("command", [])
        kind = str(job.get("kind", _normalize_job_kind(job_id)))
        mode = str(job.get("mode", _extract_task_mode(kind, command)))
        url = str(job.get("url", _extract_url_from_command(command)))
        source = "memory"
    else:
        log_path = _safe_log_path(job_id)
        if not log_path.exists():
            raise HTTPException(status_code=404, detail="job not found")
        meta = _read_job_meta(job_id)
        kind = str(meta.get("kind", _normalize_job_kind(job_id)))
        mode = str(meta.get("mode", _extract_task_mode(kind, [])))
        url = str(meta.get("url", ""))
        command = meta.get("command", [])
        created_at = str(meta.get("created_at") or _infer_created_at_from_job_id(job_id))
        status = str(meta.get("status", "completed"))
        completed_at = str(meta.get("completed_at", ""))
        if not completed_at:
            try:
                completed_at = datetime.fromtimestamp(
                    log_path.stat().st_mtime,
                    tz=timezone.utc,
                ).isoformat()
            except OSError:
                completed_at = ""
        if status not in {"running", "success", "failed", "completed"}:
            status = "completed"
        source = "log"
    return {
        "job_id": job_id,
        "kind": kind,
        "mode": mode,
        "url": url,
        "status": status,
        "created_at": created_at,
        "completed_at": completed_at,
        "command": command,
        "source": source,
    }


def _list_jobs(limit: int = context.DEFAULT_TASK_LIST_LIMIT) -> list[Dict[str, Any]]:
    items: Dict[str, Dict[str, Any]] = {}
    default_daily_url = ""
    try:
        default_daily_url = str(config_service.load_runtime_config().get("BASE_URL", "") or "")
    except Exception:
        default_daily_url = ""
    with JOB_LOCK:
        runtime_jobs = dict(JOBS)
    for job_id, job in runtime_jobs.items():
        items[job_id] = _job_summary(job_id, job)
        if items[job_id]["kind"] == "daily" and not str(items[job_id].get("url", "")):
            items[job_id]["url"] = default_daily_url
    for log_path in context.JOB_LOG_DIR.glob("*.log"):
        job_id = log_path.stem
        if job_id in items:
            continue
        try:
            items[job_id] = _job_summary(job_id, None)
            if items[job_id]["kind"] == "daily" and not str(items[job_id].get("url", "")):
                items[job_id]["url"] = default_daily_url
        except HTTPException:
            continue
    jobs = list(items.values())
    jobs.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    if limit > 0:
        jobs = jobs[:limit]
    return jobs


def _next_schedule_info() -> Dict[str, str]:
    cron_pipeline = os.getenv("CRON_PIPELINE", "").strip()
    cron_spider = os.getenv("CRON_SPIDER", "").strip()
    source = "none"
    if cron_pipeline:
        source = "CRON_PIPELINE"
    elif cron_spider:
        source = "CRON_SPIDER"
    return {
        "source": source,
        "cron_pipeline": cron_pipeline,
        "cron_spider": cron_spider,
    }


def _spawn_job(
    job_prefix: str,
    command: list[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    job_id = f"{job_prefix}-{now.strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"
    log_path = _safe_log_path(job_id)
    with open(log_path, "w", encoding="utf-8") as fp:
        process = subprocess.Popen(
            command,
            cwd=context.REPO_ROOT,
            stdout=fp,
            stderr=subprocess.STDOUT,
            text=True,
        )
    job = {
        "job_id": job_id,
        "status": "running",
        "created_at": now.isoformat(),
        "command": command,
        "kind": job_prefix,
        "mode": _extract_task_mode(job_prefix, command),
        "url": _extract_url_from_command(command),
        "pid": process.pid,
        "process": process,
        "log_path": str(log_path),
    }
    if metadata:
        job.update(metadata)
    _write_job_meta(
        job_id,
        {
            "created_at": job["created_at"],
            "completed_at": "",
            "kind": job.get("kind"),
            "mode": job.get("mode"),
            "url": job.get("url", ""),
            "status": "running",
            "command": command,
            "command_text": shlex.join(command),
        },
    )
    with JOB_LOCK:
        JOBS[job_id] = job
    return {"job_id": job_id, "status": "queued", "created_at": job["created_at"]}


def _get_job(job_id: str) -> Dict[str, Any]:
    _validate_job_id(job_id)
    with JOB_LOCK:
        job = JOBS.get(job_id)
    summary = _job_summary(job_id, job)
    status = summary["status"]
    log_path = _safe_log_path(job_id)
    log_content = _read_log_tail(log_path, 200)
    if job and status in {"success", "failed"}:
        _write_job_meta(
            job_id,
            {
                "created_at": summary["created_at"],
                "completed_at": summary.get("completed_at", ""),
                "kind": summary["kind"],
                "mode": summary["mode"],
                "url": summary["url"],
                "status": status,
                "command": summary["command"],
                "command_text": (
                    shlex.join(summary["command"])
                    if isinstance(summary["command"], list)
                    else ""
                ),
            },
        )
    return {
        "job_id": summary["job_id"],
        "kind": summary["kind"],
        "mode": summary["mode"],
        "url": summary["url"],
        "status": status,
        "created_at": summary["created_at"],
        "completed_at": summary.get("completed_at", ""),
        "command": summary["command"],
        "source": summary["source"],
        "log_size": _log_offset(log_path),
        "log": log_content,
    }


def trigger_daily_task(payload: Any, username: str) -> Dict[str, Any]:
    command = (
        ["python3", "-u", "-m", "apps.cli.pipeline"]
        if payload.mode == "pipeline"
        else ["python3", "-u", "-m", "apps.cli.spider", "--from-pipeline"]
    )
    if payload.start_page:
        command.extend(["--start-page", str(payload.start_page)])
    if payload.end_page:
        command.extend(["--end-page", str(payload.end_page)])
    if payload.all:
        command.append("--all")
    if payload.ignore_history:
        command.append("--ignore-history")
    if payload.phase:
        command.extend(["--phase", payload.phase])
    if payload.output_file:
        try:
            output_file = _sanitize_output_filename(payload.output_file)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid output_file") from exc
        command.extend(["--output-file", output_file])
    if payload.dry_run:
        command.append("--dry-run")
    if payload.ignore_release_date:
        command.append("--ignore-release-date")
    if payload.use_proxy:
        command.append("--use-proxy")
    if payload.max_movies_phase1:
        command.extend(["--max-movies-phase1", str(payload.max_movies_phase1)])
    if payload.max_movies_phase2:
        command.extend(["--max-movies-phase2", str(payload.max_movies_phase2)])
    if payload.pikpak_individual and payload.mode == "pipeline":
        command.append("--pikpak-individual")
    job = _spawn_job("daily", command, {"mode": payload.mode})
    context.audit_logger.info("task_daily username=%s job=%s", username, job["job_id"])
    return job


def trigger_adhoc_task(payload: Any, username: str) -> Dict[str, Any]:
    raw_url = (payload.url or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL for adhoc task")
    safe_url = parsed.geturl()

    command = [
        "python3",
        "-u",
        "-m",
        "apps.cli.pipeline",
        "--url",
        safe_url,
        "--start-page",
        str(payload.start_page),
        "--end-page",
        str(payload.end_page),
        "--phase",
        payload.phase,
    ]
    if payload.history_filter:
        command.append("--use-history")
    if not payload.date_filter:
        command.append("--ignore-release-date")
    if payload.use_proxy or payload.proxy_uploader or payload.proxy_pikpak:
        command.append("--use-proxy")
    if payload.dry_run:
        command.append("--dry-run")
    if payload.max_movies_phase1:
        command.extend(["--max-movies-phase1", str(payload.max_movies_phase1)])
    if payload.max_movies_phase2:
        command.extend(["--max-movies-phase2", str(payload.max_movies_phase2)])
    job = _spawn_job("adhoc", command, {"url": safe_url, "mode": "pipeline"})
    context.audit_logger.info("task_adhoc username=%s job=%s", username, job["job_id"])
    return job


def list_tasks_payload(limit: int, username: str) -> Dict[str, Any]:
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 2000")
    tasks = _list_jobs(limit=limit)
    context.audit_logger.info("task_list username=%s count=%s", username, len(tasks))
    return {
        "tasks": tasks,
        "next_schedule": _next_schedule_info(),
    }


def task_stats_payload(username: str) -> Dict[str, Any]:
    tasks = _list_jobs(limit=2000)
    daily_success = sum(
        1 for item in tasks if item.get("kind") == "daily" and item.get("status") == "success"
    )
    daily_failed = sum(
        1 for item in tasks if item.get("kind") == "daily" and item.get("status") == "failed"
    )
    daily_running = sum(
        1 for item in tasks if item.get("kind") == "daily" and item.get("status") == "running"
    )
    adhoc_running = sum(
        1 for item in tasks if item.get("kind") == "adhoc" and item.get("status") == "running"
    )
    context.audit_logger.info("task_stats username=%s total=%s", username, len(tasks))
    return {
        "daily_success": daily_success,
        "daily_failed": daily_failed,
        "daily_running": daily_running,
        "adhoc_running": adhoc_running,
    }


def get_task_payload(job_id: str, username: str) -> Dict[str, Any]:
    job = _get_job(job_id)
    context.audit_logger.info("task_read username=%s job=%s", username, job_id)
    return job


def get_task_stream_payload(job_id: str, offset: int, username: str) -> Dict[str, Any]:
    job = _get_job(job_id)
    log_path = _safe_log_path(job_id)
    chunk, next_offset = _read_log_chunk(
        log_path, offset, context.JOB_STREAM_MAX_BYTES
    )
    context.audit_logger.info(
        "task_stream username=%s job=%s offset=%s",
        username,
        job_id,
        offset,
    )
    return {
        "job_id": job_id,
        "status": job.get("status", ""),
        "offset": max(0, offset),
        "next_offset": next_offset,
        "chunk": chunk,
        "done": job.get("status") in {"success", "failed"},
    }


__all__ = [
    "get_task_payload",
    "get_task_stream_payload",
    "list_tasks_payload",
    "task_stats_payload",
    "trigger_adhoc_task",
    "trigger_daily_task",
]
