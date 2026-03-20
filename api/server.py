"""
FastAPI REST layer with auth, config management and task execution.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from passlib.context import CryptContext
from pydantic import BaseModel, Field, field_validator

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

try:
    from javdb_rust_core import (
        detect_page_type,
        parse_category_page,
        parse_detail_page,
        parse_index_page,
        parse_tag_page,
        parse_top_page,
    )

    RUST_CORE_AVAILABLE = True
except ImportError as e:
    from api.parsers import (
        detect_page_type,
        parse_category_page,
        parse_detail_page,
        parse_index_page,
        parse_tag_page,
        parse_top_page,
    )

    RUST_CORE_AVAILABLE = False
    logger.warning("Rust core unavailable, fallback python parsers: %s", e)

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
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "change-me-api-secret-key-32chars-min")
ACCESS_TOKEN_EXPIRE_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", "1800"))
REFRESH_TOKEN_EXPIRE_SECONDS = int(
    os.getenv("REFRESH_TOKEN_EXPIRE_SECONDS", str(7 * 24 * 3600))
)
MAX_SESSIONS_PER_USER = int(os.getenv("MAX_SESSIONS_PER_USER", "3"))

ACTIVE_TOKENS: Dict[str, list[str]] = {}
REVOKED_JTI: set[str] = set()
RATE_BUCKETS: Dict[str, list[float]] = {}
JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()


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
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return asdict(result)


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
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url scheme must be http/https")
        host = parsed.netloc.lower()
        if "javdb.com" not in host:
            raise ValueError("url host must include javdb.com")
        blocked = ("127.", "10.", "192.168.", "localhost")
        if any(host.startswith(prefix) for prefix in blocked):
            raise ValueError("url host is not allowed")
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


app = FastAPI(
    title="JAVDB AutoSpider API",
    version="0.2.0",
    description="Fullstack API for config, tasks and parsing.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    if payload.get("jti") in REVOKED_JTI:
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
    if len(records) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests")
    records.append(now)
    RATE_BUCKETS[key] = records


def _require_auth(request: Request) -> Dict[str, Any]:
    token = _bearer_token(request)
    payload = _jwt_decode(token)
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


def _spawn_job(job_prefix: str, command: list[str]) -> Dict[str, Any]:
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
        "pid": process.pid,
        "process": process,
        "log_path": str(log_path),
    }
    with JOB_LOCK:
        JOBS[job_id] = job
    return {"job_id": job_id, "status": "queued", "created_at": job["created_at"]}


def _get_job(job_id: str) -> Dict[str, Any]:
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", job_id):
        raise HTTPException(status_code=422, detail="Invalid job_id")
    with JOB_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    process: subprocess.Popen = job["process"]
    rc = process.poll()
    if rc is None:
        status = "running"
    else:
        status = "success" if rc == 0 else "failed"
    log_content = ""
    log_path = Path(job["log_path"])
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        log_content = "\n".join(lines[-200:])
    return {
        "job_id": job_id,
        "status": status,
        "created_at": job["created_at"],
        "command": job["command"],
        "log": log_content,
    }


@app.middleware("http")
async def auth_csrf_middleware(request: Request, call_next):
    if request.url.path in {"/api/health", "/api/auth/login"}:
        return await call_next(request)
    # CORS preflight requests omit Authorization; must pass through for CORSMiddleware
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        try:
            _verify_csrf(request)
            _require_auth(request)
        except HTTPException as exc:
            # HTTPException inside middleware can surface as 500; return JSON explicitly
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
    sessions = ACTIVE_TOKENS.setdefault(payload.username, [])
    if len(sessions) >= MAX_SESSIONS_PER_USER:
        raise HTTPException(status_code=403, detail="Too many active sessions")
    access = _jwt_encode({"sub": payload.username, "role": user["role"], "typ": "access"}, ACCESS_TOKEN_EXPIRE_SECONDS)
    refresh = _jwt_encode({"sub": payload.username, "role": user["role"], "typ": "refresh"}, REFRESH_TOKEN_EXPIRE_SECONDS)
    access_claims = jwt.decode(access, API_SECRET_KEY, algorithms=["HS256"])
    sessions.append(access_claims["jti"])
    csrf = secrets.token_urlsafe(24)
    response.set_cookie("csrf_token", csrf, httponly=False, samesite="lax", secure=False)
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


@app.post("/api/auth/logout")
async def logout(current=Depends(_require_auth)):
    jti = current.get("jti")
    if jti:
        REVOKED_JTI.add(jti)
        sessions = ACTIVE_TOKENS.get(current["sub"], [])
        ACTIVE_TOKENS[current["sub"]] = [s for s in sessions if s != jti]
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
    command = ["python3", "pipeline.py"] if payload.mode == "pipeline" else ["python3", "scripts/spider", "--from-pipeline"]
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
    job = _spawn_job("daily", command)
    audit_logger.info("task_daily username=%s job=%s", current["sub"], job["job_id"])
    return job


@app.post("/api/tasks/adhoc")
async def trigger_adhoc(payload: AdhocTaskPayload, current=Depends(require_role("admin"))):
    command = [
        "python3",
        "pipeline.py",
        "--url",
        payload.url,
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
    job = _spawn_job("adhoc", command)
    audit_logger.info("task_adhoc username=%s job=%s", current["sub"], job["job_id"])
    return job


@app.get("/api/tasks/{job_id}")
async def get_task(job_id: str, current=Depends(_require_auth)):
    job = _get_job(job_id)
    audit_logger.info("task_read username=%s job=%s", current["sub"], job_id)
    return job


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
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/detail")
async def api_parse_detail(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_detail_page(payload.html)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/category")
async def api_parse_category(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_category_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/top")
async def api_parse_top(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_top_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse/tags")
async def api_parse_tags(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        result = parse_tag_page(payload.html, payload.page_num)
        return _result_to_dict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/detect-page-type")
async def api_detect_page_type(payload: HtmlPayload, _: Dict[str, Any] = Depends(_require_auth)):
    try:
        page_type = detect_page_type(payload.html)
        return {"page_type": page_type}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
