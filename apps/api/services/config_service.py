"""Configuration store and runtime config services."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from apps.api.services import context
from packages.python.javdb_core.masking import mask_full, mask_proxy_url
from packages.python.javdb_platform.config_generator import (
    get_env_bool,
    get_env_float,
    get_env_int,
    get_env_json,
)


def _build_fernet() -> Fernet | None:
    raw = os.getenv("SECRETS_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception:
        key = Fernet.generate_key()
        context.logger.warning("Invalid SECRETS_ENCRYPTION_KEY, generated runtime key.")
        return Fernet(key)


FERNET = _build_fernet()


def load_store() -> Dict[str, Any]:
    if not context.STORE_PATH.exists():
        return {}
    data = json.loads(context.STORE_PATH.read_text(encoding="utf-8"))
    values: Dict[str, Any] = {}
    for key, item in data.items():
        if isinstance(item, dict) and item.get("enc") and FERNET:
            try:
                values[key] = json.loads(
                    FERNET.decrypt(item["enc"].encode()).decode()
                )
            except InvalidToken:
                context.logger.warning("Unable to decrypt key from store: %s", key)
        else:
            values[key] = item
    return values


def save_store(values: Dict[str, Any]) -> None:
    persisted: Dict[str, Any] = {}
    for key, value in values.items():
        if key in context.SENSITIVE_KEYS and FERNET:
            plaintext = json.dumps(value, ensure_ascii=False).encode("utf-8")
            persisted[key] = {
                "enc": FERNET.encrypt(plaintext).decode("utf-8")
            }
        else:
            persisted[key] = value
    context.STORE_PATH.write_text(
        json.dumps(persisted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_runtime_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    try:
        config_module = importlib.import_module("config")
    except Exception:
        config_module = None
    for key, meta in context.CONFIG_SCHEMA.items():
        default = meta["default"]
        if config_module and hasattr(config_module, key):
            cfg[key] = getattr(config_module, key)
        else:
            cfg[key] = default
    cfg.update(load_store())
    return cfg


def mask_config(payload: Dict[str, Any]) -> Dict[str, Any]:
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
        elif key in context.SENSITIVE_KEYS:
            result[key] = "********" if value else ""
        else:
            result[key] = value
    return result


def coerce_value(key: str, value: Any) -> Any:
    if key not in context.CONFIG_SCHEMA:
        raise HTTPException(status_code=422, detail=f"Unknown config key: {key}")
    if key in context.SENSITIVE_KEYS and value == "********":
        return "__UNCHANGED__"
    if isinstance(value, str) and key == "JAVDB_SESSION_COOKIE" and len(value) > 4096:
        raise HTTPException(status_code=422, detail=f"{key} exceeds max length")
    if isinstance(value, str) and len(value) > 2048 and key != "PROXY_POOL":
        raise HTTPException(status_code=422, detail=f"{key} exceeds max length")
    meta = context.CONFIG_SCHEMA[key]
    default = meta["default"]
    type_func = meta["type_func"]
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


def run_config_generator(config_values: Dict[str, Any]) -> None:
    env = dict(os.environ)
    movie_sleep_min = config_values.get("MOVIE_SLEEP_MIN")
    movie_sleep_max = config_values.get("MOVIE_SLEEP_MAX")
    for key, meta in context.CONFIG_SCHEMA.items():
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
    cmd = [
        "python3",
        "-m",
        "apps.cli.config_generator",
        "--output",
        "config.py",
        "--quiet",
    ]
    subprocess.run(cmd, cwd=context.REPO_ROOT, env=env, check=True)
    os.chmod(context.REPO_ROOT / "config.py", 0o600)


def get_config_payload(username: str) -> Dict[str, Any]:
    payload = load_runtime_config()
    masked = mask_config(payload)
    context.audit_logger.info("config_read username=%s", username)
    return masked


def get_config_meta_payload() -> Dict[str, Any]:
    return {"fields": context.CONFIG_META_FIELDS}


def update_config_payload(config_updates: Dict[str, Any], username: str) -> Dict[str, str]:
    config_data = load_runtime_config()
    changed_keys: list[str] = []
    for key, value in config_updates.items():
        coerced = coerce_value(key, value)
        if coerced == "__UNCHANGED__":
            continue
        config_data[key] = coerced
        changed_keys.append(key)
    save_store(config_data)
    run_config_generator(config_data)
    context.audit_logger.info(
        "config_update username=%s changed=%s",
        username,
        ",".join(sorted(changed_keys)),
    )
    return {"status": "ok"}


def set_javdb_session_cookie(cookie: str, username: str) -> Dict[str, str]:
    config_data = load_runtime_config()
    config_data["JAVDB_SESSION_COOKIE"] = cookie.strip()
    save_store(config_data)
    run_config_generator(config_data)
    context.audit_logger.info("explore_sync_cookie username=%s", username)
    return {"status": "ok"}


__all__ = [
    "coerce_value",
    "get_config_meta_payload",
    "get_config_payload",
    "load_runtime_config",
    "load_store",
    "mask_config",
    "run_config_generator",
    "save_store",
    "set_javdb_session_cookie",
    "update_config_payload",
]
