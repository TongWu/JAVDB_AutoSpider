"""
FastAPI REST layer with auth, config management and task execution.
"""

from __future__ import annotations

import importlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
import jwt
import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from passlib.context import CryptContext
from pydantic import BaseModel, Field, field_validator
from utils.request_handler import create_request_handler_from_config
from utils.proxy_pool import create_proxy_pool_from_config

from utils.config_generator import (
    get_config_map,
    get_env_bool,
    get_env_float,
    get_env_int,
    get_env_json,
    get_env_range_max,
    get_env_range_min,
)
from utils.masking import mask_full, mask_proxy_url

logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent.parent
# Load .env from project root (does not override variables already exported in the shell)
load_dotenv(ROOT_DIR / ".env")
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
STORE_PATH = ROOT_DIR / "reports" / "api_config_store.json"
STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
JOB_LOG_DIR = LOG_DIR / "jobs"
JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
_RESOLVED_JOB_LOG_DIR = JOB_LOG_DIR.resolve()

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


def _is_valid_javdb_host(url: str) -> bool:
    """Return True if *url* targets a known JavDB hostname (exact/suffix match)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in _ALLOWED_HOSTS


audit_logger = logging.getLogger("audit")
if not audit_logger.handlers:
    audit_handler = logging.FileHandler(LOG_DIR / "audit.log")
    audit_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)

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

PASSWORD_CTX = CryptContext(schemes=["bcrypt"], deprecated="auto")

_DEFAULT_SECRET = "change-me-api-secret-key-32chars-min"
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
if not API_SECRET_KEY or API_SECRET_KEY == _DEFAULT_SECRET:
    raise RuntimeError(
        "API_SECRET_KEY is not set or still uses the insecure default. "
        "Please set a strong, unique secret via the API_SECRET_KEY environment variable."
    )

ACCESS_TOKEN_EXPIRE_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", "1800"))
REFRESH_TOKEN_EXPIRE_SECONDS = int(
    os.getenv("REFRESH_TOKEN_EXPIRE_SECONDS", str(7 * 24 * 3600))
)
MAX_SESSIONS_PER_USER = int(os.getenv("MAX_SESSIONS_PER_USER", "3"))

ACTIVE_TOKENS: Dict[str, list[tuple[str, int]]] = {}
REVOKED_JTI: set[str] = set()
_AUTH_LOCK = threading.Lock()
RATE_BUCKETS: Dict[str, list[float]] = {}
JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()
DEFAULT_TASK_LIST_LIMIT = 200
JOB_STREAM_MAX_BYTES = 64 * 1024


def _prune_sessions(username: str) -> list[tuple[str, int]]:
    """Remove expired and revoked JTIs from a user's session list.

    Caller MUST hold ``_AUTH_LOCK``.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    sessions = ACTIVE_TOKENS.get(username, [])
    active = [(jti, exp) for jti, exp in sessions if exp > now and jti not in REVOKED_JTI]
    ACTIVE_TOKENS[username] = active
    return active


def _build_fernet() -> Optional[Fernet]:
    raw = os.getenv("SECRETS_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception:
        key = Fernet.generate_key()
        logger.warning("Invalid SECRETS_ENCRYPTION_KEY, generated runtime key.")
        return Fernet(key)


FERNET = _build_fernet()

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


def _result_to_dict(result: Any) -> Dict[str, Any]:
    return result_to_dict(result)


def _hash_password(plain: str) -> str:
    return PASSWORD_CTX.hash(plain)


ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = _hash_password(os.getenv("ADMIN_PASSWORD", "admin123456"))
READONLY_USERNAME = os.getenv("READONLY_USERNAME", "readonly")
READONLY_PASSWORD_HASH = os.getenv("READONLY_PASSWORD_HASH")
if not READONLY_PASSWORD_HASH and os.getenv("READONLY_PASSWORD"):
    READONLY_PASSWORD_HASH = _hash_password(os.getenv("READONLY_PASSWORD"))

USERS = {
    ADMIN_USERNAME: {"role": "admin", "password_hash": ADMIN_PASSWORD_HASH},
}
if READONLY_PASSWORD_HASH:
    USERS[READONLY_USERNAME] = {
        "role": "readonly",
        "password_hash": READONLY_PASSWORD_HASH,
    }

METHOD_LIMITS = {
    "/api/auth/login": (5, 60, "ip"),
    "/api/tasks/daily": (10, 60, "user"),
    "/api/tasks/adhoc": (10, 60, "user"),
    "/api/config": (20, 60, "user"),
    "/api/config/meta": (60, 60, "user"),
}


class HtmlPayload(BaseModel):
    html: str = Field(..., max_length=5 * 1024 * 1024)
    page_num: int = Field(1, ge=1, le=9999)


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
    status: str = "ok"
    rust_core_available: bool = False


class LoginPayload(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class DailyTaskPayload(BaseModel):
    start_page: int = Field(1, ge=1, le=200)
    end_page: int = Field(10, ge=1, le=200)
    all: bool = False
    ignore_history: bool = False
    phase: str = Field("all")
    output_file: Optional[str] = None
    dry_run: bool = False
    ignore_release_date: bool = False
    use_proxy: bool = False
    max_movies_phase1: Optional[int] = Field(None, ge=1, le=10000)
    max_movies_phase2: Optional[int] = Field(None, ge=1, le=10000)
    pikpak_individual: bool = False
    mode: str = Field("pipeline")

    @field_validator("phase")
    @classmethod
    def valid_phase(cls, value: str) -> str:
        if value not in {"1", "2", "all"}:
            raise ValueError("phase must be one of 1, 2, all")
        return value

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"pipeline", "spider"}:
            raise ValueError("mode must be one of pipeline, spider")
        return value

    @field_validator("end_page")
    @classmethod
    def valid_page_range(cls, value: int, info) -> int:
        start_page = info.data.get("start_page", 1)
        if value < start_page:
            raise ValueError("end_page must be >= start_page")
        return value


class AdhocTaskPayload(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    start_page: int = Field(1, ge=1, le=200)
    end_page: int = Field(1, ge=1, le=200)
    history_filter: bool = False
    date_filter: bool = False
    phase: str = Field("all")
    use_proxy: bool = True
    proxy_uploader: bool = False
    proxy_pikpak: bool = False
    qb_category: Optional[str] = Field(None, max_length=255)
    dry_run: bool = False
    ignore_release_date: bool = True
    max_movies_phase1: Optional[int] = Field(None, ge=1, le=10000)
    max_movies_phase2: Optional[int] = Field(None, ge=1, le=10000)

    @field_validator("phase")
    @classmethod
    def valid_phase(cls, value: str) -> str:
        if value not in {"1", "2", "all"}:
            raise ValueError("phase must be one of 1, 2, all")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not _is_valid_javdb_host(value):
            raise ValueError("url must target a valid javdb.com host")
        return value

    @field_validator("end_page")
    @classmethod
    def valid_page_range(cls, value: int, info) -> int:
        start_page = info.data.get("start_page", 1)
        if value < start_page:
            raise ValueError("end_page must be >= start_page")
        return value


class HealthCheckPayload(BaseModel):
    check_smtp: bool = True
    use_proxy: bool = False


class ExploreResolvePayload(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    page_num: int = Field(1, ge=1, le=9999)
    use_proxy: bool = True
    use_cookie: bool = True

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not _is_valid_javdb_host(value):
            raise ValueError("url must target a valid javdb.com host")
        return value


class ExploreCookiePayload(BaseModel):
    cookie: str = Field(..., min_length=1, max_length=4096)


class ExploreMagnetPayload(BaseModel):
    magnet: str = Field(..., min_length=1, max_length=4096)
    title: str = Field("", max_length=255)
    category: Optional[str] = Field(None, max_length=255)

    @field_validator("magnet")
    @classmethod
    def valid_magnet(cls, value: str) -> str:
        if not value.startswith("magnet:?"):
            raise ValueError("magnet must start with magnet:?")
        return value


class ExploreOneClickPayload(BaseModel):
    detail_url: str = Field(..., min_length=1, max_length=2048)
    use_proxy: bool = True
    use_cookie: bool = True
    category: Optional[str] = Field(None, max_length=255)

    @field_validator("detail_url")
    @classmethod
    def valid_detail_url(cls, value: str) -> str:
        if not _is_valid_javdb_host(value):
            raise ValueError("detail_url must target a valid javdb.com host")
        return value


class ExploreIndexStatusPayload(BaseModel):
    movies: list[dict[str, str]] = Field(default_factory=list)
    use_proxy: bool = True
    use_cookie: bool = True


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


app = FastAPI(
    title="JAVDB AutoSpider API",
    version="0.2.0",
    description="Fullstack API for config, tasks and parsing.",
)

_CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if o.strip()
]

def _infer_cookie_secure() -> bool:
    explicit = os.getenv("COOKIE_SECURE", "").strip().lower()
    if explicit in {"1", "true", "yes"}:
        return True
    if explicit in {"0", "false", "no"}:
        return False
    return any(o.startswith("https://") for o in _CORS_ORIGINS)

COOKIE_SECURE = _infer_cookie_secure()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(_: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def _jwt_encode(payload: Dict[str, Any], expires_in: int) -> str:
    now = datetime.now(timezone.utc)
    data = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(data, API_SECRET_KEY, algorithm="HS256")


def _jwt_decode(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, API_SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    with _AUTH_LOCK:
        revoked = payload.get("jti") in REVOKED_JTI
    if revoked:
        raise HTTPException(status_code=401, detail="Token revoked")
    return payload


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return auth_header.replace("Bearer ", "", 1).strip()


def _rate_limit(scope: str, request: Request, user: Optional[Dict[str, Any]] = None) -> None:
    path = request.url.path
    if path in METHOD_LIMITS:
        limit, window, strategy = METHOD_LIMITS[path]
    else:
        limit, window, strategy = 120, 60, "ip"
    if strategy == "user" and user:
        key = f"{scope}:{path}:user:{user['sub']}"
    else:
        key = f"{scope}:{path}:ip:{request.client.host if request.client else 'unknown'}"
    now = time.time()
    records = RATE_BUCKETS.get(key, [])
    records = [ts for ts in records if now - ts < window]
    if not records:
        RATE_BUCKETS.pop(key, None)
    if len(records) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests")
    records.append(now)
    RATE_BUCKETS[key] = records


def _require_auth(request: Request) -> Dict[str, Any]:
    token = _bearer_token(request)
    payload = _jwt_decode(token)
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Access token required")
    _rate_limit("auth", request, payload)
    return payload


def _require_auth_or_token(request: Request, token: str = "") -> Dict[str, Any]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return _require_auth(request)
    token = (token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = _jwt_decode(token)
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Access token required")
    _rate_limit("auth", request, payload)
    return payload


def require_role(role: str):
    def _dep(request: Request) -> Dict[str, Any]:
        payload = _require_auth(request)
        if role == "admin" and payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return payload

    return _dep


def _verify_csrf(request: Request) -> None:
    if request.method in {"POST", "PUT", "DELETE"} and request.url.path != "/api/auth/login":
        header_token = request.headers.get("X-CSRF-Token", "")
        cookie_token = request.cookies.get("csrf_token", "")
        if not header_token or not cookie_token or header_token != cookie_token:
            raise HTTPException(status_code=403, detail="CSRF token invalid")


def _load_store() -> Dict[str, Any]:
    if not STORE_PATH.exists():
        return {}
    data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    values: Dict[str, Any] = {}
    for key, item in data.items():
        if isinstance(item, dict) and item.get("enc") and FERNET:
            try:
                values[key] = json.loads(FERNET.decrypt(item["enc"].encode()).decode())
            except InvalidToken:
                logger.warning("Unable to decrypt key from store: %s", key)
        else:
            values[key] = item
    return values


def _save_store(values: Dict[str, Any]) -> None:
    persisted: Dict[str, Any] = {}
    for key, value in values.items():
        if key in SENSITIVE_KEYS and FERNET:
            plaintext = json.dumps(value, ensure_ascii=False).encode("utf-8")
            persisted[key] = {"enc": FERNET.encrypt(plaintext).decode("utf-8")}
        else:
            persisted[key] = value
    STORE_PATH.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_runtime_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    try:
        config_module = importlib.import_module("config")
    except Exception:
        config_module = None
    for key, meta in CONFIG_SCHEMA.items():
        default = meta["default"]
        if config_module and hasattr(config_module, key):
            cfg[key] = getattr(config_module, key)
        else:
            cfg[key] = default
    cfg.update(_load_store())
    return cfg


def _mask_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        if key == "PROXY_POOL":
            if isinstance(value, list):
                masked_pool = []
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    masked_item = dict(item)
                    if "http" in masked_item and masked_item["http"]:
                        masked_item["http"] = mask_proxy_url(str(masked_item["http"]))
                    if "https" in masked_item and masked_item["https"]:
                        masked_item["https"] = mask_proxy_url(str(masked_item["https"]))
                    masked_pool.append(masked_item)
                result[key] = masked_pool
            else:
                result[key] = mask_full(str(value))
        elif key in SENSITIVE_KEYS:
            result[key] = "********" if value else ""
        else:
            result[key] = value
    return result


def _coerce_value(key: str, value: Any) -> Any:
    if key not in CONFIG_SCHEMA:
        raise HTTPException(status_code=422, detail=f"Unknown config key: {key}")
    if key in SENSITIVE_KEYS and value == "********":
        return "__UNCHANGED__"
    if isinstance(value, str) and key == "JAVDB_SESSION_COOKIE" and len(value) > 4096:
        raise HTTPException(status_code=422, detail=f"{key} exceeds max length")
    if isinstance(value, str) and len(value) > 2048 and key != "PROXY_POOL":
        raise HTTPException(status_code=422, detail=f"{key} exceeds max length")
    meta = CONFIG_SCHEMA[key]
    default = meta["default"]
    type_func: Callable[[str, Any], Any] = meta["type_func"]
    if type_func is get_env_bool:
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"{key} must be boolean")
    elif type_func is get_env_int:
        if not isinstance(value, int):
            raise HTTPException(status_code=422, detail=f"{key} must be integer")
    elif type_func is get_env_float:
        if not isinstance(value, (int, float)):
            raise HTTPException(status_code=422, detail=f"{key} must be number")
        value = float(value)
    elif type_func is get_env_json:
        if isinstance(value, str):
            if len(value) > 65536:
                raise HTTPException(status_code=422, detail=f"{key} exceeds max length")
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"{key} invalid JSON") from exc
        if not isinstance(value, type(default)):
            if key == "PROXY_POOL" and isinstance(value, list):
                return value
            raise HTTPException(status_code=422, detail=f"{key} type mismatch")
    return value


def _run_config_generator(config_values: Dict[str, Any]) -> None:
    env = dict(os.environ)
    movie_sleep_min = config_values.get("MOVIE_SLEEP_MIN")
    movie_sleep_max = config_values.get("MOVIE_SLEEP_MAX")
    for key, meta in CONFIG_SCHEMA.items():
        env_name = meta["env_name"]
        if not env_name:
            continue
        value = config_values.get(key, meta["default"])
        if env_name == "MOVIE_SLEEP":
            continue
        if isinstance(value, (list, dict)):
            env[env_name] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            env[env_name] = "true" if value else "false"
        elif value is None:
            env[env_name] = ""
        else:
            env[env_name] = str(value)
    if movie_sleep_min is not None and movie_sleep_max is not None:
        env["MOVIE_SLEEP"] = f"{movie_sleep_min},{movie_sleep_max}"
    cmd = ["python3", "utils/config_generator.py", "--output", "config.py", "--quiet"]
    subprocess.run(cmd, cwd=ROOT_DIR, env=env, check=True)
    os.chmod(ROOT_DIR / "config.py", 0o600)


def _job_meta_path(job_id: str) -> Path:
    _validate_job_id(job_id)
    candidate = (JOB_LOG_DIR / f"{job_id}.meta.json").resolve()
    try:
        candidate.relative_to(_RESOLVED_JOB_LOG_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    return candidate


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
    path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_url_from_command(command: list[str]) -> str:
    for idx, token in enumerate(command):
        if token == "--url" and idx + 1 < len(command):
            return str(command[idx + 1]).strip()
    return ""


def _extract_task_mode(kind: str, command: list[str]) -> str:
    if kind == "adhoc":
        return "pipeline"
    command_text = " ".join(command)
    if "scripts/spider" in command_text:
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


def _read_log_chunk(path: Path, offset: int, max_bytes: int = JOB_STREAM_MAX_BYTES) -> tuple[str, int]:
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
    m = re.match(r"^[a-zA-Z]+-(\d{8})-(\d{6})-[a-zA-Z0-9]+$", job_id)
    if not m:
        return ""
    date_part = m.group(1)
    time_part = m.group(2)
    try:
        dt = datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return ""


_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_job_id(job_id: str) -> None:
    """Reject job IDs that contain path-traversal characters or unexpected patterns."""
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=422, detail="Invalid job_id")


def _safe_log_path(job_id: str) -> Path:
    """Build and anchor-check a log file path for *job_id* under JOB_LOG_DIR."""
    _validate_job_id(job_id)
    candidate = (JOB_LOG_DIR / f"{job_id}.log").resolve()
    try:
        candidate.relative_to(_RESOLVED_JOB_LOG_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    return candidate


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
        log_path = _safe_log_path(job_id)
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
                completed_at = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc).isoformat()
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
        "log_path": str(log_path),
    }


def _list_jobs(limit: int = DEFAULT_TASK_LIST_LIMIT) -> list[Dict[str, Any]]:
    items: Dict[str, Dict[str, Any]] = {}
    default_daily_url = ""
    try:
        default_daily_url = str(_load_runtime_config().get("BASE_URL", "") or "")
    except Exception:
        default_daily_url = ""
    with JOB_LOCK:
        runtime_jobs = dict(JOBS)
    for job_id, job in runtime_jobs.items():
        items[job_id] = _job_summary(job_id, job)
        if items[job_id]["kind"] == "daily" and not str(items[job_id].get("url", "")):
            items[job_id]["url"] = default_daily_url
    for log_path in JOB_LOG_DIR.glob("*.log"):
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
    jobs.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
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


def _spawn_job(job_prefix: str, command: list[str], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    job_id = f"{job_prefix}-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    log_path = JOB_LOG_DIR / f"{job_id}.log"
    with open(log_path, "w", encoding="utf-8") as fp:
        process = subprocess.Popen(
            command,
            cwd=ROOT_DIR,
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
                "command_text": shlex.join(summary["command"]) if isinstance(summary["command"], list) else "",
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


def _runtime_proxy_pool(config_data: Dict[str, Any]):
    proxy_pool_raw = config_data.get("PROXY_POOL", [])
    if not isinstance(proxy_pool_raw, list) or not proxy_pool_raw:
        return None
    try:
        return create_proxy_pool_from_config(
            proxy_pool_raw,
            cooldown_seconds=int(config_data.get("PROXY_POOL_COOLDOWN_SECONDS", 691200) or 691200),
            max_failures=int(config_data.get("PROXY_POOL_MAX_FAILURES", 3) or 3),
            ban_log_file=str(ROOT_DIR / "reports" / "proxy_bans.csv"),
        )
    except Exception:
        return None


def _new_request_handler(config_data: Dict[str, Any]):
    proxy_pool = _runtime_proxy_pool(config_data)
    return create_request_handler_from_config(
        proxy_pool=proxy_pool,
        base_url=str(config_data.get("BASE_URL", "https://javdb.com")),
        cf_bypass_service_port=int(config_data.get("CF_BYPASS_SERVICE_PORT", 8000) or 8000),
        cf_bypass_port_map=config_data.get("CF_BYPASS_PORT_MAP", {}) or {},
        cf_bypass_enabled=bool(config_data.get("CF_BYPASS_ENABLED", True)),
        cf_turnstile_cooldown=int(config_data.get("CF_TURNSTILE_COOLDOWN", 30) or 30),
        fallback_cooldown=int(config_data.get("FALLBACK_COOLDOWN", 30) or 30),
        javdb_session_cookie=str(config_data.get("JAVDB_SESSION_COOKIE", "") or ""),
        proxy_http=config_data.get("PROXY_HTTP"),
        proxy_https=config_data.get("PROXY_HTTPS"),
        proxy_modules=config_data.get("PROXY_MODULES", ["spider"]) or ["spider"],
        proxy_mode=str(config_data.get("PROXY_MODE", "pool")),
    )


def _simple_fetch_javdb_html(cfg: Dict[str, Any], url: str, use_cookie: bool = True) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://javdb.com/",
    }
    cookie = str(cfg.get("JAVDB_SESSION_COOKIE", "") or "").strip()
    if use_cookie and cookie:
        headers["Cookie"] = f"_jdb_session={cookie}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text or ""
    if not html.strip():
        raise ValueError("empty html")
    return html


def _validate_javdb_url_or_422(url: str) -> None:
    """
    Ensure the given URL targets an allowed javdb host and scheme,
    and that the host does not resolve to a private/loopback IP (SSRF guard).
    Raises HTTPException(422) if invalid.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="url must use http or https scheme")
    if not parsed.netloc:
        raise HTTPException(status_code=422, detail="url must include a host")
    if not _is_valid_javdb_host(url):
        raise HTTPException(status_code=422, detail="url must target a valid javdb.com host")
    hostname = parsed.hostname or ""
    try:
        for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            hostname, None, proto=socket.IPPROTO_TCP
        ):
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise HTTPException(
                    status_code=422,
                    detail="url must not resolve to a private or reserved IP address",
                )
    except socket.gaierror:
        pass


def _fetch_javdb_html(url: str, use_proxy: bool = True, use_cookie: bool = True) -> str:
    _validate_javdb_url_or_422(url)
    cfg = _load_runtime_config()
    errors: list[str] = []
    try:
        handler = _new_request_handler(cfg)
        html = handler.get_page(
            url=url,
            use_proxy=use_proxy,
            use_cookie=use_cookie,
            module_name="spider",
            max_retries=3,
            # Explore builtin browser should not depend on external CF bypass service.
            use_cf_bypass=False,
        )
        if html:
            return html
        errors.append("request_handler returned empty")
    except Exception as exc:
        errors.append(f"request_handler: {type(exc).__name__}")

    try:
        return _simple_fetch_javdb_html(cfg, url, use_cookie=use_cookie)
    except Exception as exc:
        errors.append(f"simple_fetch: {type(exc).__name__}")

    raise HTTPException(status_code=502, detail=f"Failed to fetch target page ({'; '.join(errors)})")


def _pick_best_magnet(magnets: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not magnets:
        return None
    preferred_tokens = ("中字", "字幕", "破解", "uncensored", "無碼", "无码")

    def _score(item: dict[str, Any]) -> tuple[int, int, int]:
        tags = [str(x) for x in item.get("tags", [])]
        name = str(item.get("name", ""))
        score = 0
        for token in preferred_tokens:
            if token in name or any(token in tag for tag in tags):
                score += 3
        if "高清" in name or "1080" in name:
            score += 2
        size_hint = str(item.get("size", "")).upper()
        if "GB" in size_hint:
            score += 1
        return (score, int(item.get("file_count", 0) or 0), len(name))

    return sorted(magnets, key=_score, reverse=True)[0]


def _qb_login_session(cfg: Dict[str, Any]) -> requests.Session:
    host = str(cfg.get("QB_HOST", "")).strip()
    port = str(cfg.get("QB_PORT", "")).strip()
    username = str(cfg.get("QB_USERNAME", "")).strip()
    password = str(cfg.get("QB_PASSWORD", "")).strip()
    if not host or not port or not username or not password:
        raise HTTPException(status_code=422, detail="qBittorrent config is incomplete")
    base_url = f"http://{host}:{port}"
    session = requests.Session()
    resp = session.post(
        f"{base_url}/api/v2/auth/login",
        data={"username": username, "password": password},
        timeout=int(cfg.get("REQUEST_TIMEOUT", 30) or 30),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to login qBittorrent")
    return session


def _qb_add_magnet(cfg: Dict[str, Any], magnet: str, title: str, category: Optional[str] = None) -> None:
    host = str(cfg.get("QB_HOST", "")).strip()
    port = str(cfg.get("QB_PORT", "")).strip()
    base_url = f"http://{host}:{port}"
    session = _qb_login_session(cfg)
    effective_category = category or str(cfg.get("TORRENT_CATEGORY_ADHOC", "") or cfg.get("TORRENT_CATEGORY", ""))
    payload = {
        "urls": magnet,
        "name": title,
        "category": effective_category,
        "autoTMM": "true",
        "savepath": str(cfg.get("TORRENT_SAVE_PATH", "") or ""),
        "skip_checking": str(bool(cfg.get("SKIP_CHECKING", False))).lower(),
        "addPaused": str(not bool(cfg.get("AUTO_START", True))).lower(),
    }
    resp = session.post(
        f"{base_url}/api/v2/torrents/add",
        data=payload,
        timeout=int(cfg.get("REQUEST_TIMEOUT", 30) or 30),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to add magnet to qBittorrent")


def _resolved_history_csv_path(cfg: Dict[str, Any]) -> Path:
    reports_dir = str(cfg.get("REPORTS_DIR", "reports") or "reports")
    history_raw = str(cfg.get("PARSED_MOVIES_CSV", "parsed_movies_history.csv") or "parsed_movies_history.csv")
    history_path = Path(history_raw)
    if history_path.is_absolute():
        return history_path
    if "/" in history_raw or "\\" in history_raw:
        return ROOT_DIR / history_path
    return ROOT_DIR / reports_dir / history_path


def _downloaded_map_by_href(cfg: Dict[str, Any]) -> Dict[str, bool]:
    history_path = _resolved_history_csv_path(cfg)
    downloaded: Dict[str, bool] = {}
    if not history_path.exists():
        return downloaded
    try:
        with open(history_path, "r", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                href = str(row.get("href", "")).strip()
                if not href:
                    continue
                downloaded[href] = True
    except Exception:
        return {}
    return downloaded


def _inject_explore_enhancer(html: str, source_url: str) -> str:
    escaped_url = json.dumps(source_url)
    enhancer = f"""
<script>
(function() {{
  const SOURCE_URL = {escaped_url};
  const frameUrl = new URL(window.location.href);
  const token = frameUrl.searchParams.get("token") || "";
  const authHeaders = token ? {{ "Authorization": "Bearer " + token, "Content-Type": "application/json" }} : {{ "Content-Type": "application/json" }};

  function abs(href) {{
    try {{ return new URL(href, SOURCE_URL).toString(); }} catch (e) {{ return href; }}
  }}
  function isDetail(url) {{
    try {{ return /^\\/v\\//.test(new URL(url).pathname); }} catch (e) {{ return false; }}
  }}
  function isJavdb(url) {{
    try {{ return /(^|\\.)javdb\\.com$/i.test(new URL(url).hostname); }} catch (e) {{ return false; }}
  }}
  function postToParent(type, payload) {{
    window.parent && window.parent.postMessage(Object.assign({{ type }}, payload || {{}}), "*");
  }}
  function notifyUrl() {{
    postToParent("explore:url", {{ url: SOURCE_URL }});
  }}
  function linkToProxy(url) {{
    const u = new URL("/api/explore/proxy-page", window.location.origin);
    u.searchParams.set("url", url);
    if (token) u.searchParams.set("token", token);
    return u.toString();
  }}
  function patchLinks() {{
    document.querySelectorAll("a[href]").forEach((a) => {{
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      const target = abs(href);
      if (!isJavdb(target)) return;
      a.setAttribute("href", linkToProxy(target));
    }});
  }}
  async function apiPost(path, payload) {{
    const res = await fetch(path, {{
      method: "POST",
      headers: authHeaders,
      credentials: "include",
      body: JSON.stringify(payload || {{}})
    }});
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json().catch(() => ({{}}));
  }}
  function mkBtn(text) {{
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.style.marginLeft = "8px";
    b.style.padding = "3px 8px";
    b.style.fontSize = "12px";
    b.style.border = "1px solid #ccc";
    b.style.borderRadius = "4px";
    b.style.cursor = "pointer";
    b.style.background = "#fff";
    return b;
  }}
  function addAdhocJump() {{
    if (!isJavdb(SOURCE_URL) || isDetail(SOURCE_URL)) return;
    const bar = document.querySelector(".tabs") || document.body;
    const btn = mkBtn("跳转至adhoc任务创建");
    btn.addEventListener("click", () => postToParent("explore:jump-adhoc", {{ url: SOURCE_URL }}));
    bar.appendChild(btn);
  }}
  function addDetailButtons() {{
    if (!isDetail(SOURCE_URL)) return;
    const rows = document.querySelectorAll("#magnets-content .item.columns.is-desktop");
    rows.forEach((row) => {{
      const anchor = row.querySelector(".magnet-name a[href^='magnet:']");
      if (!anchor || row.querySelector(".explore-qb-btn")) return;
      const btn = mkBtn("使用qBittorrent下载");
      btn.className = "explore-qb-btn";
      btn.addEventListener("click", async () => {{
        try {{
          await apiPost("/api/explore/download-magnet", {{
            magnet: anchor.getAttribute("href") || "",
            title: (anchor.querySelector(".name") && anchor.querySelector(".name").textContent || "").trim()
          }});
          btn.textContent = "已提交";
        }} catch (e) {{
          btn.textContent = "失败";
        }}
      }});
      anchor.parentElement && anchor.parentElement.appendChild(btn);
    }});
    const host = document.querySelector("#magnets-content");
    if (host && !document.querySelector(".explore-one-click-btn")) {{
      const one = mkBtn("一键下载");
      one.className = "explore-one-click-btn";
      one.style.margin = "8px 0";
      one.addEventListener("click", async () => {{
        try {{
          await apiPost("/api/explore/one-click", {{ detail_url: SOURCE_URL, use_proxy: false, use_cookie: true }});
          one.textContent = "已提交";
        }} catch (e) {{
          one.textContent = "失败";
        }}
      }});
      host.parentElement && host.parentElement.insertBefore(one, host);
    }}
  }}
  function addIndexButtons() {{
    const cards = document.querySelectorAll(".movie-list .item a.box[href]");
    if (!cards.length) return;
    cards.forEach((a) => {{
      if (a.querySelector(".explore-card-oneclick")) return;
      const btn = mkBtn("一键下载");
      btn.className = "explore-card-oneclick";
      btn.addEventListener("click", async (e) => {{
        e.preventDefault();
        e.stopPropagation();
        try {{
          await apiPost("/api/explore/one-click", {{ detail_url: abs(a.getAttribute("href") || ""), use_proxy: false, use_cookie: true }});
          btn.textContent = "已提交";
        }} catch (err) {{
          btn.textContent = "失败";
        }}
      }});
      const title = a.querySelector(".video-title");
      if (title && title.parentElement) title.parentElement.appendChild(btn);
    }});
    if (!document.querySelector(".explore-page-oneclick")) {{
      const pageBtn = mkBtn("整页一键下载");
      pageBtn.className = "explore-page-oneclick";
      pageBtn.style.margin = "8px 0";
      pageBtn.addEventListener("click", async () => {{
        for (const a of cards) {{
          try {{
            await apiPost("/api/explore/one-click", {{ detail_url: abs(a.getAttribute("href") || ""), use_proxy: false, use_cookie: true }});
          }} catch (e) {{}}
        }}
        pageBtn.textContent = "已提交";
      }});
      const mount = document.querySelector(".movie-list") || document.body;
      mount.parentElement && mount.parentElement.insertBefore(pageBtn, mount);
    }}
  }}
  async function addIndexStatusTags() {{
    const cards = Array.from(document.querySelectorAll(".movie-list .item a.box[href]"));
    if (!cards.length) return;
    const movies = cards.map((a) => {{
      const href = abs(a.getAttribute("href") || "");
      const codeNode = a.querySelector(".uid");
      return {{ href, video_code: (codeNode && codeNode.textContent || "").trim() }};
    }});
    try {{
      const data = await apiPost("/api/explore/index-status", {{ movies, use_proxy: false, use_cookie: true }});
      const items = data.items || {{}};
      cards.forEach((a) => {{
        const href = abs(a.getAttribute("href") || "");
        const st = items[href];
        if (!st) return;
        if (a.querySelector(".explore-status-row")) return;
        const row = document.createElement("div");
        row.className = "explore-status-row";
        row.style.marginTop = "4px";
        row.style.fontSize = "11px";
        row.style.color = "#666";
        row.textContent = (st.has_uncensored ? "有无码" : "无码未知/无") + " · " + (st.downloaded ? "已下载" : "未下载");
        const title = a.querySelector(".video-title");
        if (title && title.parentElement) title.parentElement.appendChild(row);
      }});
    }} catch (e) {{}}
  }}
  function init() {{
    notifyUrl();
    patchLinks();
    addAdhocJump();
    addDetailButtons();
    addIndexButtons();
    addIndexStatusTags();
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", init);
  }} else {{
    init();
  }}
}})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", enhancer + "</body>")
    return html + enhancer


@app.middleware("http")
async def auth_csrf_middleware(request: Request, call_next):
    if request.url.path in {"/api/health", "/api/auth/login", "/api/auth/refresh", "/api/explore/proxy-page"}:
        return await call_next(request)
    # CORS preflight requests omit Authorization; must pass through for CORSMiddleware
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        try:
            if not request.url.path.startswith("/api/explore/"):
                _verify_csrf(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
    return await call_next(request)


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(rust_core_available=RUST_CORE_AVAILABLE)


@app.post("/api/auth/login")
async def login(payload: LoginPayload, request: Request, response: Response):
    _rate_limit("preauth", request)
    user = USERS.get(payload.username)
    if not user or not PASSWORD_CTX.verify(payload.password, user["password_hash"]):
        audit_logger.warning(
            "login_failed username=%s ip=%s",
            payload.username,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid username/password")
    access = _jwt_encode({"sub": payload.username, "role": user["role"], "typ": "access"}, ACCESS_TOKEN_EXPIRE_SECONDS)
    refresh = _jwt_encode({"sub": payload.username, "role": user["role"], "typ": "refresh"}, REFRESH_TOKEN_EXPIRE_SECONDS)
    access_claims = jwt.decode(access, API_SECRET_KEY, algorithms=["HS256"])
    with _AUTH_LOCK:
        sessions = _prune_sessions(payload.username)
        if len(sessions) >= MAX_SESSIONS_PER_USER:
            raise HTTPException(status_code=403, detail="Too many active sessions")
        sessions.append((access_claims["jti"], access_claims["exp"]))
        ACTIVE_TOKENS[payload.username] = sessions
    csrf = secrets.token_urlsafe(24)
    response.set_cookie("csrf_token", csrf, httponly=False, samesite="lax", secure=COOKIE_SECURE)
    audit_logger.info(
        "login_success username=%s ip=%s role=%s",
        payload.username,
        request.client.host if request.client else "unknown",
        user["role"],
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_SECONDS,
        "csrf_token": csrf,
        "role": user["role"],
    }


@app.post("/api/auth/refresh")
async def refresh_token(request: Request):
    """Exchange a valid refresh token for a new access token."""
    token = _bearer_token(request)
    payload = _jwt_decode(token)
    if payload.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="Refresh token required")
    _rate_limit("preauth", request)
    username = payload.get("sub", "")
    user = USERS.get(username)
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user")
    access = _jwt_encode(
        {"sub": username, "role": user["role"], "typ": "access"},
        ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    access_claims = jwt.decode(access, API_SECRET_KEY, algorithms=["HS256"])
    with _AUTH_LOCK:
        sessions = _prune_sessions(username)
        if len(sessions) >= MAX_SESSIONS_PER_USER:
            sessions.pop(0)
        sessions.append((access_claims["jti"], access_claims["exp"]))
        ACTIVE_TOKENS[username] = sessions
    audit_logger.info("token_refresh username=%s", username)
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_SECONDS,
    }


@app.post("/api/auth/logout")
async def logout(current=Depends(_require_auth)):
    jti = current.get("jti")
    if jti:
        with _AUTH_LOCK:
            REVOKED_JTI.add(jti)
            sessions = ACTIVE_TOKENS.get(current["sub"], [])
            ACTIVE_TOKENS[current["sub"]] = [(s, exp) for s, exp in sessions if s != jti]
    audit_logger.info("logout username=%s", current["sub"])
    return {"status": "ok"}


@app.get("/api/config")
async def get_config(current=Depends(_require_auth)):
    payload = _load_runtime_config()
    masked = _mask_config(payload)
    audit_logger.info("config_read username=%s", current["sub"])
    return masked


@app.get("/api/config/meta")
async def get_config_meta(_: Dict[str, Any] = Depends(_require_auth)):
    """Field groups and types for the config form UI."""
    return {"fields": CONFIG_META_FIELDS}


@app.put("/api/config")
async def update_config(config_updates: Dict[str, Any], current=Depends(require_role("admin"))):
    config_data = _load_runtime_config()
    changed_keys = []
    for key, value in config_updates.items():
        coerced = _coerce_value(key, value)
        if coerced == "__UNCHANGED__":
            continue
        config_data[key] = coerced
        changed_keys.append(key)
    _save_store(config_data)
    _run_config_generator(config_data)
    audit_logger.info(
        "config_update username=%s changed=%s",
        current["sub"],
        ",".join(sorted(changed_keys)),
    )
    return {"status": "ok"}


@app.post("/api/tasks/daily")
async def trigger_daily(payload: DailyTaskPayload, current=Depends(require_role("admin"))):
    command = ["python3", "-u", "pipeline.py"] if payload.mode == "pipeline" else ["python3", "-u", "scripts/spider", "--from-pipeline"]
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
        command.extend(["--output-file", payload.output_file])
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
    audit_logger.info("task_daily username=%s job=%s", current["sub"], job["job_id"])
    return job


@app.post("/api/tasks/adhoc")
async def trigger_adhoc(payload: AdhocTaskPayload, current=Depends(require_role("admin"))):
    # Validate and normalize the URL before using it in a subprocess command.
    raw_url = (payload.url or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL for adhoc task")
    safe_url = parsed.geturl()

    command = [
        "python3",
        "-u",
        "pipeline.py",
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
    audit_logger.info("task_adhoc username=%s job=%s", current["sub"], job["job_id"])
    return job


@app.get("/api/tasks")
async def list_tasks(limit: int = 200, current=Depends(_require_auth)):
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 2000")
    tasks = _list_jobs(limit=limit)
    audit_logger.info("task_list username=%s count=%s", current["sub"], len(tasks))
    return {
        "tasks": tasks,
        "next_schedule": _next_schedule_info(),
    }


@app.get("/api/tasks/stats")
async def task_stats(current=Depends(_require_auth)):
    tasks = _list_jobs(limit=2000)
    daily_success = sum(1 for item in tasks if item.get("kind") == "daily" and item.get("status") == "success")
    daily_failed = sum(1 for item in tasks if item.get("kind") == "daily" and item.get("status") == "failed")
    daily_running = sum(1 for item in tasks if item.get("kind") == "daily" and item.get("status") == "running")
    adhoc_running = sum(1 for item in tasks if item.get("kind") == "adhoc" and item.get("status") == "running")
    audit_logger.info("task_stats username=%s total=%s", current["sub"], len(tasks))
    return {
        "daily_success": daily_success,
        "daily_failed": daily_failed,
        "daily_running": daily_running,
        "adhoc_running": adhoc_running,
    }


@app.get("/api/tasks/{job_id}")
async def get_task(job_id: str, current=Depends(_require_auth)):
    job = _get_job(job_id)
    audit_logger.info("task_read username=%s job=%s", current["sub"], job_id)
    return job


@app.get("/api/tasks/{job_id}/stream")
async def get_task_stream(job_id: str, offset: int = 0, current=Depends(_require_auth)):
    job = _get_job(job_id)
    log_path = _safe_log_path(job_id)
    chunk, next_offset = _read_log_chunk(log_path, offset, JOB_STREAM_MAX_BYTES)
    audit_logger.info("task_stream username=%s job=%s offset=%s", current["sub"], job_id, offset)
    return {
        "job_id": job_id,
        "status": job.get("status", ""),
        "offset": max(0, offset),
        "next_offset": next_offset,
        "chunk": chunk,
        "done": job.get("status") in {"success", "failed"},
    }


@app.post("/api/explore/sync-cookie")
async def explore_sync_cookie(payload: ExploreCookiePayload, current=Depends(require_role("admin"))):
    config_data = _load_runtime_config()
    config_data["JAVDB_SESSION_COOKIE"] = payload.cookie.strip()
    _save_store(config_data)
    _run_config_generator(config_data)
    audit_logger.info("explore_sync_cookie username=%s", current["sub"])
    return {"status": "ok"}


@app.get("/api/explore/proxy-page", response_class=HTMLResponse)
async def explore_proxy_page(url: str, token: str = "", current=Depends(_require_auth_or_token)):
    _validate_javdb_url_or_422(url)
    html = _fetch_javdb_html(url, use_proxy=False, use_cookie=True)
    injected = _inject_explore_enhancer(html, url)
    audit_logger.info("explore_proxy_page username=%s", current["sub"])
    return HTMLResponse(content=injected)


@app.post("/api/explore/resolve")
async def explore_resolve(payload: ExploreResolvePayload, current=Depends(_require_auth)):
    _validate_javdb_url_or_422(payload.url)
    html = _fetch_javdb_html(payload.url, use_proxy=payload.use_proxy, use_cookie=payload.use_cookie)
    page_type = detect_page_type(html)
    body: Dict[str, Any] = {
        "url": payload.url,
        "page_type": page_type,
    }
    if page_type == "detail":
        body["detail"] = _result_to_dict(parse_detail_page(html))
    else:
        body["index"] = _result_to_dict(parse_index_page(html, payload.page_num))
    audit_logger.info("explore_resolve username=%s page_type=%s", current["sub"], page_type)
    return body


@app.post("/api/explore/download-magnet")
async def explore_download_magnet(payload: ExploreMagnetPayload, current=Depends(require_role("admin"))):
    cfg = _load_runtime_config()
    _qb_add_magnet(cfg, payload.magnet, payload.title or "JavDB", payload.category)
    audit_logger.info("explore_download_magnet username=%s", current["sub"])
    return {"status": "ok"}


@app.post("/api/explore/one-click")
async def explore_one_click(payload: ExploreOneClickPayload, current=Depends(require_role("admin"))):
    _validate_javdb_url_or_422(payload.detail_url)
    html = _fetch_javdb_html(payload.detail_url, use_proxy=payload.use_proxy, use_cookie=payload.use_cookie)
    detail = _result_to_dict(parse_detail_page(html))
    magnets = detail.get("magnets", [])
    best = _pick_best_magnet(magnets if isinstance(magnets, list) else [])
    if not best:
        raise HTTPException(status_code=404, detail="No magnet found in detail page")
    cfg = _load_runtime_config()
    title = str(best.get("name") or detail.get("video_code") or "JavDB")
    _qb_add_magnet(cfg, str(best.get("href", "")), title, payload.category)
    audit_logger.info("explore_one_click username=%s", current["sub"])
    return {
        "status": "ok",
        "selected": best,
        "video_code": detail.get("video_code", ""),
    }


@app.post("/api/explore/index-status")
async def explore_index_status(payload: ExploreIndexStatusPayload, current=Depends(_require_auth)):
    cfg = _load_runtime_config()
    downloaded_map = _downloaded_map_by_href(cfg)
    statuses: Dict[str, Dict[str, Any]] = {}
    for item in payload.movies[:50]:
        href = str(item.get("href", "")).strip()
        if not href:
            continue
        absolute_url = href if href.startswith("http") else f"https://javdb.com{href}"
        try:
            _validate_javdb_url_or_422(absolute_url)
        except HTTPException:
            continue
        is_downloaded = bool(downloaded_map.get(href) or downloaded_map.get(absolute_url))
        has_uncensored = False
        try:
            html = _fetch_javdb_html(absolute_url, use_proxy=payload.use_proxy, use_cookie=payload.use_cookie)
            detail = _result_to_dict(parse_detail_page(html))
            magnets = detail.get("magnets", [])
            if isinstance(magnets, list):
                for magnet in magnets:
                    tags = [str(x) for x in magnet.get("tags", [])] if isinstance(magnet, dict) else []
                    name = str(magnet.get("name", "")) if isinstance(magnet, dict) else ""
                    if any(token in name for token in ("無碼", "无码", "uncensored")) or any(
                        any(token in tag for token in ("無碼", "无码", "uncensored")) for tag in tags
                    ):
                        has_uncensored = True
                        break
        except Exception:
            has_uncensored = False
        statuses[href] = {
            "downloaded": is_downloaded,
            "has_uncensored": has_uncensored,
        }
    audit_logger.info("explore_index_status username=%s count=%s", current["sub"], len(statuses))
    return {"items": statuses}


@app.post("/api/health-check")
async def run_health_check(payload: HealthCheckPayload, current=Depends(require_role("admin"))):
    command = ["python3", "scripts/health_check.py"]
    if payload.check_smtp:
        command.append("--check-smtp")
    if payload.use_proxy:
        command.append("--use-proxy")
    proc = subprocess.run(command, cwd=ROOT_DIR, capture_output=True, text=True, timeout=180)
    audit_logger.info("health_check username=%s code=%s", current["sub"], proc.returncode)
    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "exit_code": proc.returncode,
        "output": (proc.stdout or "")[-4000:],
    }


@app.post("/api/login/refresh")
async def refresh_javdb_session(current=Depends(require_role("admin"))):
    proc = subprocess.run(
        ["python3", "scripts/login.py"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    audit_logger.info("javdb_refresh username=%s code=%s", current["sub"], proc.returncode)
    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "output": ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:],
    }


@app.post("/api/parse/index")
async def api_parse_index(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_index_page(payload.html, payload.page_num)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/detail")
async def api_parse_detail(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_detail_page(payload.html)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/category")
async def api_parse_category(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_category_page(payload.html, payload.page_num)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/top")
async def api_parse_top(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_top_page(payload.html, payload.page_num)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/tags")
async def api_parse_tags(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_tag_page(payload.html, payload.page_num)
        return result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/detect-page-type")
async def api_detect_page_type(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        page_type = detect_page_type(payload.html)
        return {"page_type": page_type}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/api/parse/url')
async def api_parse_url(payload: UrlPayload, _: Dict[str, Any] = Depends(_require_auth)):
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
async def api_crawl_index(payload: CrawlIndexPayload, _: Dict[str, Any] = Depends(_require_auth)):
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
# Spider Job API (async subprocess execution) -- from main branch
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_SPIDER_JOBS = 2
_spider_job_semaphore = threading.Semaphore(_MAX_CONCURRENT_SPIDER_JOBS)
_MAX_OUTPUT_LINES = 5000
_SPIDER_JOB_TTL_SECONDS = 24 * 3600

_spider_jobs: Dict[str, dict] = {}
_spider_jobs_lock = threading.Lock()


def _cleanup_expired_spider_jobs() -> None:
    """Remove finished jobs older than TTL (caller must hold _spider_jobs_lock)."""
    now = datetime.now(timezone.utc)
    expired = [
        jid for jid, job in _spider_jobs.items()
        if job.get('finished_at') and
        (now - datetime.fromisoformat(job['finished_at'])).total_seconds() > _SPIDER_JOB_TTL_SECONDS
    ]
    for jid in expired:
        del _spider_jobs[jid]


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

        with _spider_jobs_lock:
            _spider_jobs[job_id]['pid'] = process.pid

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

        with _spider_jobs_lock:
            job = _spider_jobs[job_id]
            job['status'] = 'completed' if return_code == 0 else 'failed'
            job['return_code'] = return_code
            job['finished_at'] = datetime.now(timezone.utc).isoformat()
            job['output'] = output_lines[-_MAX_OUTPUT_LINES:]
            if csv_path:
                job['csv_path'] = csv_path
            if session_id:
                job['session_id'] = session_id

    except Exception as exc:
        with _spider_jobs_lock:
            job = _spider_jobs[job_id]
            job['status'] = 'failed'
            job['error'] = str(exc)
            job['finished_at'] = datetime.now(timezone.utc).isoformat()
    finally:
        _spider_job_semaphore.release()


@app.post('/api/jobs/spider')
async def api_submit_spider_job(payload: SpiderJobPayload, _: Dict[str, Any] = Depends(require_role("admin"))):
    """Submit a full spider run as an async background job."""
    if payload.url:
        _validate_target_url(payload.url)

    if not _spider_job_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f'Maximum concurrent spider jobs ({_MAX_CONCURRENT_SPIDER_JOBS}) reached, try again later',
        )

    job_id = uuid.uuid4().hex[:12]
    cli_args = _payload_to_cli_args(payload)

    try:
        with _spider_jobs_lock:
            _cleanup_expired_spider_jobs()
            _spider_jobs[job_id] = {
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
        with _spider_jobs_lock:
            _spider_jobs.pop(job_id, None)
        _spider_job_semaphore.release()
        raise HTTPException(
            status_code=503,
            detail='Failed to start spider job, please try again later',
        )

    return {'job_id': job_id, 'status': 'running', 'cli_args': cli_args}


@app.get('/api/jobs/{job_id}/status')
async def api_get_spider_job_status(job_id: str, _: Dict[str, Any] = Depends(_require_auth)):
    """Query the status and output of a spider job."""
    with _spider_jobs_lock:
        job = _spider_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f'Job {job_id} not found')
    return job