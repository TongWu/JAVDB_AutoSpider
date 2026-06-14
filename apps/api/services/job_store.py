"""Filesystem store for task-job artifacts: logs, metadata, and result files.

Single home for the path-traversal-safe read/write of everything a job leaves
under ``context.RESOLVED_JOB_LOG_DIR`` — the job-id validation, the log tail /
chunk readers, the ``.meta.json`` sidecar, and the ``.result.json`` summary.
Extracted from task_service so this security-sensitive I/O layer is one cohesive
unit (its own test surface) and the orchestration layer can't accidentally build
a path that escapes the job log dir.

Pure artifact I/O: knows nothing about the in-memory JOBS registry, subprocess
lifecycle, or CLI command building — those stay in task_service.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException

from apps.api.services import context

logger = logging.getLogger(__name__)

_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def validate_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Invalid job_id")
    for sep in (os.sep, os.altsep):
        if sep and sep in job_id:
            raise HTTPException(status_code=422, detail="Invalid job_id")


def safe_job_log_filename(job_id: str, extension: str) -> str:
    validate_job_id(job_id)
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


def resolved_path_under_job_log_dir(job_id: str, extension: str) -> Path:
    filename = safe_job_log_filename(job_id, extension)
    candidate = (context.RESOLVED_JOB_LOG_DIR / filename).resolve()
    try:
        candidate.relative_to(context.RESOLVED_JOB_LOG_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id") from None
    return candidate


def safe_log_path(job_id: str) -> Path:
    return resolved_path_under_job_log_dir(job_id, ".log")


def job_meta_path(job_id: str) -> Path:
    return resolved_path_under_job_log_dir(job_id, ".meta.json")


def read_job_meta(job_id: str) -> Dict[str, Any]:
    validate_job_id(job_id)
    path = job_meta_path(job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_job_meta(job_id: str, payload: Dict[str, Any]) -> None:
    path = job_meta_path(job_id)
    safe_payload = dict(payload)
    safe_payload["job_id"] = job_id
    path.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def log_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_log_tail(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def read_log_chunk(
    path: Path,
    offset: int,
    max_bytes: int = context.JOB_STREAM_MAX_BYTES,
) -> tuple[str, int]:
    if not path.exists():
        return "", 0
    size = log_offset(path)
    if offset < 0:
        offset = 0
    if offset > size:
        offset = size
    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
        fp.seek(offset)
        chunk = fp.read(max_bytes)
        next_offset = fp.tell()
    return chunk, next_offset


def resolve_job_result_file(value: str) -> Path:
    raw_path = Path(value).expanduser()
    base_path = raw_path if raw_path.is_absolute() else context.REPO_ROOT / raw_path
    return base_path.resolve()


def validate_job_result_file(value: str) -> None:
    try:
        candidate = resolve_job_result_file(value)
        candidate.relative_to(context.RESOLVED_JOB_LOG_DIR)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid task command") from None
    if candidate.suffixes[-2:] != [".result", ".json"]:
        raise HTTPException(status_code=400, detail="Invalid task command")


def load_result_summary(result_path: str | None) -> Dict[str, Any] | None:
    if not result_path:
        return None
    path = resolve_job_result_file(result_path)
    try:
        path.relative_to(context.RESOLVED_JOB_LOG_DIR)
    except ValueError:
        logger.warning("Task result JSON outside job log dir ignored: %s", path)
        return None
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Invalid task result JSON ignored: %s", path)
        return None
    except UnicodeDecodeError as exc:
        logger.warning("Unable to decode task result JSON %s: %s", path, exc)
        return None
    except OSError as exc:
        logger.warning("Unable to read task result JSON %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("Invalid task result payload ignored: %s", path)
        return None
    return {
        "kind": raw.get("kind"),
        "schema_version": raw.get("schema_version"),
        "status": raw.get("status"),
        "exit_code": raw.get("exit_code"),
        "failure_reason": raw.get("failure_reason"),
    }
