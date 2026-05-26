"""Unified Cloudflare D1 access port.

This module owns transport concerns only: HTTP POSTs, retry/backoff,
schema metadata cache, metrics, and recovery hooks. Business SQL remains
in the storage DB and Repo layers.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as _datetime
import email.utils as _email_utils
import json
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Any, Callable, Iterable, Sequence

import requests

# D1Connection must import D1AccessPort lazily at runtime to avoid a circular
# import: this module intentionally reuses d1_client cursor/error helpers.
from javdb.infra.logging import get_logger

from javdb.storage.d1_client import (
    D1Cursor,
    D1PermanentError,
    D1TransientError,
    _EXPORT_LOCK_BACKOFF_FLOOR_SEC,
    _EXPORT_LOCK_KEYWORDS,
    _TRANSIENT_ERROR_KEYWORDS,
    _matches_keyword,
    _params_for_d1_json,
)
from javdb.storage.d1_recovery import RecoveryEvent, append_event


logger = get_logger(__name__)


@dataclass(frozen=True)
class D1PortConfig:
    timeout: int
    batch_limit: int
    max_retries: int
    retry_base_sec: float
    retry_max_sleep_sec: float


def d1_summary_path(reports_dir: str | None = None) -> Path:
    root = reports_dir or os.environ.get("REPORTS_DIR", "reports")
    return Path(root) / "D1" / "d1_port_summary.json"


def d1_batching_enabled() -> bool:
    raw = os.environ.get("D1_BATCHING_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def recovery_outbox_path(reports_dir: str | None = None) -> Path:
    root = reports_dir or os.environ.get("REPORTS_DIR", "reports")
    return Path(root) / "D1" / "d1_recovery_outbox.jsonl"


def recovery_outbox_enabled() -> bool:
    raw = os.environ.get("D1_RECOVERY_OUTBOX_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _numeric_summary_value(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _read_existing_summary(path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        key: _numeric_summary_value(payload.get(key))
        for key in _SUMMARY_COUNTER_KEYS
    }


def _with_derived_summary(summary: dict[str, int]) -> dict[str, int | float]:
    out: dict[str, int | float] = {
        key: _numeric_summary_value(summary.get(key))
        for key in _SUMMARY_COUNTER_KEYS
    }
    batches = int(out["batches"])
    batch_statements = int(out["batch_statements"])
    out["average_batch_size"] = (
        batch_statements / batches if batches else 0.0
    )
    out["recovery_drain_duration_sec"] = 0.0
    return out


_SUMMARY_WRITE_LOCK = threading.Lock()
_SUMMARY_COUNTER_KEYS = (
    "http_posts",
    "sql_statements",
    "batches",
    "batch_statements",
    "retries",
    "retry_successes",
    "transient_errors",
    "permanent_errors",
    "schema_cache_hits",
    "schema_cache_misses",
    "outbox_queued",
    "outbox_replayed",
    "outbox_dead_lettered",
)


class D1AccessPort:
    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        config: D1PortConfig,
        post_request: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.5),
    ):
        self._url = url
        self._headers = dict(headers)
        self._config = config
        self._session = requests.Session()
        self._post_request = post_request or self._session.post
        self._sleep = sleep
        self._jitter = jitter
        self._schema_cache: dict[tuple[str, tuple[Any, ...]], list[D1Cursor]] = {}
        self._batch_queue: dict[str, list[tuple[str, tuple[Any, ...], object]]] = {}
        self._batch_queue_since: dict[str, float] = {}
        self._summary = {key: 0 for key in _SUMMARY_COUNTER_KEYS}
        self._last_written_summary = {key: 0 for key in _SUMMARY_COUNTER_KEYS}
        self._outbox_path = recovery_outbox_path()

    def execute(
        self,
        sql: str,
        params: Iterable[Any] = (),
        *,
        policy=None,
    ) -> list[D1Cursor]:
        params_tuple = tuple(params)
        if self._should_queue(policy):
            self._queue_statement(sql, params_tuple, policy)
            return [D1Cursor({"meta": {"changes": 0}, "results": []}, queued=True)]

        key = self._schema_cache_key(sql, params_tuple)
        if key is not None and key in self._schema_cache:
            self._summary["schema_cache_hits"] += 1
            return self._clone_cursors(self._schema_cache[key])
        if key is not None:
            self._summary["schema_cache_misses"] += 1

        try:
            cursors = self._post_with_retry(
                {"sql": sql, "params": _params_for_d1_json(params_tuple)}
            )
        except D1TransientError as exc:
            self._queue_recovery_if_allowed(policy, sql, params_tuple, exc)
            raise
        self._summary["sql_statements"] += 1
        if key is not None:
            self._schema_cache[key] = self._clone_cursors(cursors)
        elif self._is_schema_mutation(sql):
            self._schema_cache.clear()
        return cursors

    def executemany(
        self,
        sql: str,
        seq_of_params: Iterable[Iterable[Any]],
        *,
        policy=None,
    ) -> list[D1Cursor]:
        statements = [
            {"sql": sql, "params": _params_for_d1_json(params)}
            for params in seq_of_params
        ]
        if not statements:
            return []

        cursors: list[D1Cursor] = []
        for chunk in self._split(statements, self._config.batch_limit):
            cursors.extend(self._post_with_retry({"batch": chunk}))
            self._record_batch_metrics(len(chunk))
        if self._is_schema_mutation(sql):
            self._schema_cache.clear()
        return cursors

    def batch_execute(
        self,
        statements: Sequence[tuple[str, Sequence[Any]]],
        *,
        policy=None,
    ) -> list[D1Cursor]:
        body_statements = [
            {"sql": sql, "params": _params_for_d1_json(params)}
            for sql, params in statements
        ]
        if not body_statements:
            return []

        cursors: list[D1Cursor] = []
        for chunk in self._split(body_statements, self._config.batch_limit):
            cursors.extend(self._post_with_retry({"batch": chunk}))
            self._record_batch_metrics(len(chunk))
        if any(self._is_schema_mutation(sql) for sql, _params in statements):
            self._schema_cache.clear()
        return cursors

    def flush(self, *, ordering_key: str | None = None) -> list[D1Cursor]:
        cursors: list[D1Cursor] = []
        keys = [ordering_key] if ordering_key is not None else list(self._batch_queue)
        for key in keys:
            queued = list(self._batch_queue.get(key) or [])
            if not queued:
                continue
            statements = [(sql, params) for sql, params, _policy in queued]
            try:
                cursors.extend(self.batch_execute(statements))
            except D1TransientError as exc:
                for sql, params, policy in queued:
                    self._queue_recovery_if_allowed(policy, sql, params, exc)
                raise
            self._batch_queue.pop(key, None)
            self._batch_queue_since.pop(key, None)
        return cursors

    def discard(self, *, ordering_key: str | None = None) -> None:
        keys = [ordering_key] if ordering_key is not None else list(self._batch_queue)
        for key in keys:
            self._batch_queue.pop(key, None)
            self._batch_queue_since.pop(key, None)

    def drain_recovery(
        self,
        *,
        ordering_key: str | None = None,
        max_batches: int | None = None,
    ) -> dict[str, int]:
        return {"replayed": 0, "dead_lettered": 0}

    def summary(self) -> dict[str, int | float]:
        return _with_derived_summary(dict(self._summary))

    def write_summary(self, path: str | os.PathLike[str] | None = None) -> None:
        target = Path(path) if path is not None else d1_summary_path()

        with _SUMMARY_WRITE_LOCK:
            current = dict(self._summary)
            delta = {
                key: current[key] - self._last_written_summary.get(key, 0)
                for key in _SUMMARY_COUNTER_KEYS
            }
            aggregate = _read_existing_summary(target)
            for key in _SUMMARY_COUNTER_KEYS:
                aggregate[key] = _numeric_summary_value(aggregate.get(key)) + delta[key]
            aggregate = _with_derived_summary(aggregate)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            self._last_written_summary = current

    def close(self) -> None:
        try:
            self.flush()
        finally:
            try:
                self._session.close()
            except Exception:
                logger.warning("Failed to close D1 port session", exc_info=True)

    def _post_with_retry(self, body: dict[str, Any]) -> list[D1Cursor]:
        last_exc: D1TransientError | None = None
        attempts = max(1, self._config.max_retries)
        for attempt in range(attempts):
            try:
                result = self._post(body)
                if attempt > 0:
                    self._summary["retry_successes"] += 1
                return result
            except D1PermanentError:
                self._summary["permanent_errors"] += 1
                raise
            except D1TransientError as exc:
                self._summary["transient_errors"] += 1
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                self._summary["retries"] += 1
                self._sleep(self._compute_backoff(attempt, exc))
        assert last_exc is not None
        raise last_exc

    def _post(self, body: dict[str, Any]) -> list[D1Cursor]:
        self._summary["http_posts"] += 1
        try:
            response = self._post_request(
                self._url,
                headers=self._headers,
                json=body,
                timeout=self._config.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise D1TransientError(f"D1 network error: {exc}") from exc
        except requests.RequestException as exc:
            raise D1TransientError(f"D1 HTTP request failed: {exc}") from exc

        status = response.status_code
        if status == 429 or 500 <= status < 600:
            err = D1TransientError(
                f"D1 API returned HTTP {status}: {response.text[:500]}"
            )
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                err.retry_after = retry_after  # type: ignore[attr-defined]
            raise err
        if 400 <= status < 500:
            errors = self._extract_errors(response)
            if errors is not None:
                err_text = str(errors)
                if _matches_keyword(err_text, _TRANSIENT_ERROR_KEYWORDS):
                    transient = D1TransientError(
                        f"D1 API returned HTTP {status} (transient): {errors}"
                    )
                    if _matches_keyword(err_text, _EXPORT_LOCK_KEYWORDS):
                        transient.is_export_lock = True  # type: ignore[attr-defined]
                    raise transient
                raise D1PermanentError(f"D1 API returned HTTP {status}: {errors}")
            raise D1PermanentError(
                f"D1 API returned HTTP {status}: {response.text[:500]}"
            )
        if status != 200:
            raise D1PermanentError(
                f"D1 API returned unexpected HTTP {status}: {response.text[:500]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise D1TransientError(
                f"D1 returned non-JSON response: {response.text[:500]}"
            ) from exc

        if not payload.get("success"):
            errors = payload.get("errors") or payload.get("messages") or []
            err_text = str(errors)
            if _matches_keyword(err_text, _TRANSIENT_ERROR_KEYWORDS):
                transient = D1TransientError(f"D1 API transient error: {errors}")
                if _matches_keyword(err_text, _EXPORT_LOCK_KEYWORDS):
                    transient.is_export_lock = True  # type: ignore[attr-defined]
                raise transient
            raise D1PermanentError(f"D1 API error: {errors}")

        return [D1Cursor(item) for item in payload.get("result") or []]

    def _compute_backoff(self, attempt: int, exc: D1TransientError) -> float:
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
            try:
                parsed = _email_utils.parsedate_to_datetime(str(retry_after))
                delay = (
                    parsed
                    - _datetime.datetime.now(tz=_datetime.timezone.utc)
                ).total_seconds()
                return max(0.0, delay)
            except Exception:
                pass

        base = self._config.retry_base_sec * (2**attempt)
        if getattr(exc, "is_export_lock", False):
            base = max(base, _EXPORT_LOCK_BACKOFF_FLOOR_SEC)
        return min(base, self._config.retry_max_sleep_sec) + self._jitter()

    @staticmethod
    def _extract_errors(response) -> list[Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("errors") or payload.get("messages") or []

    @staticmethod
    def _split(seq: Sequence[Any], size: int):
        if size <= 0:
            raise ValueError("D1PortConfig.batch_limit must be positive")
        for i in range(0, len(seq), size):
            yield seq[i : i + size]

    @staticmethod
    def _schema_cache_key(
        sql: str,
        params: Iterable[Any],
    ) -> tuple[str, tuple[Any, ...]] | None:
        normalized = " ".join(sql.strip().lower().split())
        if normalized.startswith("pragma table_info"):
            return (normalized, tuple(params))
        if normalized.startswith("select ") and re.search(
            r"\bfrom\s+sqlite_master\b", normalized
        ):
            return (normalized, tuple(params))
        return None

    @staticmethod
    def _clone_cursors(cursors: Sequence[D1Cursor]) -> list[D1Cursor]:
        return [
            D1Cursor(
                {
                    "meta": {"last_row_id": cur.lastrowid, "changes": cur.rowcount},
                    "results": [dict(row) for row in cur.fetchall()],
                }
            )
            for cur in cursors
        ]

    def _record_batch_metrics(self, statement_count: int) -> None:
        self._summary["batches"] += 1
        self._summary["batch_statements"] += statement_count
        self._summary["sql_statements"] += statement_count

    def _should_queue(self, policy) -> bool:
        return bool(
            policy is not None
            and getattr(policy, "batching_allowed", False)
            and getattr(policy, "ordering_key", None)
            and d1_batching_enabled()
        )

    def _queue_statement(
        self,
        sql: str,
        params: tuple[Any, ...],
        policy: object,
    ) -> None:
        ordering_key = str(getattr(policy, "ordering_key"))
        self._flush_on_enqueue_if_interval_elapsed(ordering_key)

        queue = self._batch_queue.setdefault(ordering_key, [])
        if not queue:
            self._batch_queue_since[ordering_key] = time.monotonic()
        queue.append((sql, params, policy))
        if len(queue) >= self._config.batch_limit:
            self.flush(ordering_key=ordering_key)

    def _queue_recovery_if_allowed(
        self,
        policy,
        sql: str,
        params: Iterable[Any],
        error: D1TransientError,
    ) -> None:
        if not recovery_outbox_enabled():
            return
        if policy is None or not getattr(policy, "recovery_allowed", False):
            return
        event = RecoveryEvent.queued(policy, sql, params, str(error))
        try:
            append_event(self._outbox_path, event)
        except OSError:
            logger.warning(
                "Failed to append D1 recovery outbox event for %s",
                getattr(policy, "idempotency_key", "<unknown>"),
                exc_info=True,
            )
            return
        self._summary["outbox_queued"] += 1

    def _flush_on_enqueue_if_interval_elapsed(self, ordering_key: str) -> None:
        queue = self._batch_queue.get(ordering_key)
        if not queue:
            return
        started_at = self._batch_queue_since.get(ordering_key)
        if started_at is None:
            return
        interval_ms = _env_int("D1_FLUSH_INTERVAL_MS", 250)
        if interval_ms <= 0:
            return
        elapsed_ms = (time.monotonic() - started_at) * 1000
        if elapsed_ms >= interval_ms:
            self.flush(ordering_key=ordering_key)

    @staticmethod
    def _is_schema_mutation(sql: str) -> bool:
        normalized = _leading_sql_token(sql)
        if normalized.startswith(
            (
                "alter ",
                "create ",
                "drop ",
                "reindex ",
                "vacuum ",
            )
        ):
            return True
        return normalized.startswith(
            (
                "update sqlite_master",
                "delete from sqlite_master",
                "insert into sqlite_master",
            )
        )


def _leading_sql_token(sql: str) -> str:
    i = 0
    length = len(sql)
    while i < length:
        while i < length and sql[i].isspace():
            i += 1
        if sql.startswith("--", i):
            newline = sql.find("\n", i + 2)
            if newline == -1:
                return ""
            i = newline + 1
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                return ""
            i = end + 2
            continue
        if i < length and sql[i] == "(":
            i += 1
            continue
        break
    return sql[i:].lstrip().lower()
