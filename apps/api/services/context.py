"""Shared API runtime context and process-level settings."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict

from dotenv import load_dotenv

from compat import activate_repo_root
from packages.python.javdb_platform.config_generator import (
    get_config_map,
    get_env_bool,
    get_env_float,
    get_env_int,
    get_env_json,
    get_env_range_max,
    get_env_range_min,
)

REPO_ROOT = Path(activate_repo_root())
logger = logging.getLogger("apps.api")

load_dotenv(REPO_ROOT / ".env")

LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

STORE_PATH = REPO_ROOT / "reports" / "api_config_store.json"
STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

JOB_LOG_DIR = LOG_DIR / "jobs"
JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
RESOLVED_JOB_LOG_DIR = JOB_LOG_DIR.resolve()

SENSITIVE_KEYS = {
    "GIT_PASSWORD",
    "QB_PASSWORD",
    "SMTP_PASSWORD",
    "JAVDB_PASSWORD",
    "JAVDB_SESSION_COOKIE",
    "GPT_API_KEY",
    "PIKPAK_PASSWORD",
    "PROXY_POOL",
}

audit_logger = logging.getLogger("audit")
if not audit_logger.handlers:
    audit_handler = logging.FileHandler(LOG_DIR / "audit.log")
    audit_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)

CONFIG_MAP = get_config_map(github_actions_mode=False)
CONFIG_SCHEMA: Dict[str, Dict[str, Any]] = {}
for cfg_name, env_name, type_func, default, _ in CONFIG_MAP:
    CONFIG_SCHEMA[cfg_name] = {
        "env_name": env_name,
        "type_func": type_func,
        "default": default,
    }


def _config_field_wire_type(type_func: Any) -> str:
    if type_func is get_env_bool:
        return "bool"
    if type_func is get_env_int:
        return "int"
    if type_func is get_env_float:
        return "float"
    if type_func is get_env_json:
        return "json"
    if type_func in (get_env_range_min, get_env_range_max):
        return "int"
    return "string"


CONFIG_META_FIELDS: list[Dict[str, Any]] = [
    {
        "key": cfg_name,
        "section": section,
        "type": _config_field_wire_type(type_func),
        "sensitive": cfg_name in SENSITIVE_KEYS,
        "readonly": env_name is None,
    }
    for cfg_name, env_name, type_func, _default, section in CONFIG_MAP
]

DEFAULT_TASK_LIST_LIMIT = 200
JOB_STREAM_MAX_BYTES = 64 * 1024
EXPLORE_INDEX_STATUS_MAX_ITEMS = int(os.getenv("EXPLORE_INDEX_STATUS_MAX_ITEMS", "30"))
EXPLORE_INDEX_STATUS_CONCURRENCY = max(
    1, int(os.getenv("EXPLORE_INDEX_STATUS_CONCURRENCY", "8"))
)
EXPLORE_INDEX_STATUS_ITEM_TIMEOUT_SECONDS = float(
    os.getenv("EXPLORE_INDEX_STATUS_ITEM_TIMEOUT_SECONDS", "12")
)
EXPLORE_INDEX_STATUS_TOTAL_TIMEOUT_SECONDS = float(
    os.getenv("EXPLORE_INDEX_STATUS_TOTAL_TIMEOUT_SECONDS", "25")
)
EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS = int(
    os.getenv("EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS", "300")
)
EXPLORE_INDEX_STATUS_CACHE_MAX_ITEMS = int(
    os.getenv("EXPLORE_INDEX_STATUS_CACHE_MAX_ITEMS", "2000")
)

CORS_ORIGINS = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if item.strip()
]


def infer_cookie_secure(origins: list[str] | None = None) -> bool:
    explicit = os.getenv("COOKIE_SECURE", "").strip().lower()
    if explicit in {"1", "true", "yes"}:
        return True
    if explicit in {"0", "false", "no"}:
        return False
    insecure_override = os.getenv("COOKIE_ALLOW_INSECURE", "").strip().lower()
    if insecure_override in {"1", "true", "yes"}:
        return False
    if insecure_override in {"0", "false", "no"}:
        return True
    return True


COOKIE_SECURE = infer_cookie_secure()


__all__ = [
    "CONFIG_MAP",
    "CONFIG_META_FIELDS",
    "CONFIG_SCHEMA",
    "COOKIE_SECURE",
    "CORS_ORIGINS",
    "DEFAULT_TASK_LIST_LIMIT",
    "EXPLORE_INDEX_STATUS_CACHE_MAX_ITEMS",
    "EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS",
    "EXPLORE_INDEX_STATUS_CONCURRENCY",
    "EXPLORE_INDEX_STATUS_ITEM_TIMEOUT_SECONDS",
    "EXPLORE_INDEX_STATUS_MAX_ITEMS",
    "EXPLORE_INDEX_STATUS_TOTAL_TIMEOUT_SECONDS",
    "JOB_LOG_DIR",
    "JOB_STREAM_MAX_BYTES",
    "LOG_DIR",
    "REPO_ROOT",
    "RESOLVED_JOB_LOG_DIR",
    "SENSITIVE_KEYS",
    "STORE_PATH",
    "audit_logger",
    "infer_cookie_secure",
    "logger",
]
