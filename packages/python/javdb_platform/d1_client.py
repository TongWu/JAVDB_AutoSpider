"""Cloudflare D1 HTTP client with a sqlite3-Connection-compatible facade.

Each ``D1Connection`` instance maps to a single D1 database. Because D1 only
exposes an HTTP REST endpoint, every ``execute`` is a synchronous round-trip
to the Cloudflare API; callers should batch via :meth:`executemany` whenever
possible.

Authentication is read from environment variables:

- ``CLOUDFLARE_ACCOUNT_ID``
- ``CLOUDFLARE_API_TOKEN``
- ``D1_HISTORY_DB_ID`` / ``D1_REPORTS_DB_ID`` / ``D1_OPERATIONS_DB_ID``

The facade implements just enough of :class:`sqlite3.Connection` and
:class:`sqlite3.Cursor` for the JAVDB codebase: ``execute``, ``executemany``,
``commit``, ``rollback``, ``close``, ``total_changes``, ``row_factory``,
``cursor.lastrowid``, ``cursor.rowcount``, ``fetchone``, ``fetchall``.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import requests

from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_API_TIMEOUT = _env_int("D1_HTTP_TIMEOUT", 30)
_BATCH_LIMIT = 50  # D1 caps statements per request
_MAX_RETRIES = _env_int("D1_MAX_RETRIES", 3)
_RETRY_BASE_SEC = _env_float("D1_RETRY_BASE_SEC", 1.0)
_RETRY_MAX_SLEEP_SEC = 8.0

# Substrings in CF "errors[].message" that signal a recoverable backend hiccup.
_TRANSIENT_ERROR_KEYWORDS = (
    "D1_RESET_DO",
    "busy",
    "timeout",
    "overloaded",
    "internal error",
    "temporarily",
)


class D1Error(RuntimeError):
    """Raised when the D1 API returns an error response."""


class D1TransientError(D1Error):
    """Recoverable: 5xx, 429, network timeout/connection error, D1_RESET_DO, etc."""


class D1PermanentError(D1Error):
    """Non-recoverable: SQL syntax error, FK/unique violation, 4xx (except 429)."""


class D1Cursor:
    """Minimal sqlite3.Cursor-compatible result wrapper."""

    __slots__ = ("_rows", "lastrowid", "rowcount")

    def __init__(self, result: dict):
        meta = result.get("meta") or {}
        self.lastrowid: Optional[int] = meta.get("last_row_id")
        self.rowcount: int = int(meta.get("changes") or 0)
        self._rows: List[dict] = list(result.get("results") or [])

    def fetchone(self) -> Optional[dict]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[dict]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _split(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class D1Connection:
    """sqlite3.Connection-compatible facade backed by Cloudflare D1 HTTP API."""

    def __init__(
        self,
        account_id: str,
        database_id: str,
        api_token: str,
        *,
        timeout: int = _API_TIMEOUT,
    ):
        if not account_id or not database_id or not api_token:
            raise ValueError("D1Connection requires account_id, database_id, api_token")
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/d1/database/{database_id}/query"
        )
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._total_changes = 0
        # Attribute compatibility with sqlite3.Connection — D1 returns dict rows
        # natively so row_factory is a no-op (callers can still set it).
        self.row_factory = None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> D1Cursor:
        # CF /query single-statement shape: {sql, params}
        cursors = self._post_with_retry({"sql": sql, "params": list(params)})
        if not cursors:
            # success=true but empty result[] — should never happen per CF docs,
            # but guard against API regressions / proxy stripping the body.
            raise D1PermanentError(
                "D1 API returned success with empty result list; "
                f"expected 1 cursor for SQL: {sql[:200]}"
            )
        cursor = cursors[0]
        self._total_changes += cursor.rowcount
        return cursor

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> None:
        statements = [{"sql": sql, "params": list(p)} for p in seq_of_params]
        if not statements:
            return
        for chunk in _split(statements, _BATCH_LIMIT):
            # CF /query batch shape: {batch: [{sql, params}, ...]}
            cursors = self._post_with_retry({"batch": chunk})
            for c in cursors:
                self._total_changes += c.rowcount

    def executescript(self, script: str) -> None:
        statements = [s.strip() for s in script.split(";") if s.strip()]
        if not statements:
            return
        body_statements = [{"sql": s, "params": []} for s in statements]
        for chunk in _split(body_statements, _BATCH_LIMIT):
            self._post_with_retry({"batch": chunk})

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        logger.warning(
            "D1Connection.rollback() called but D1 auto-commits each request; "
            "rollback is a no-op."
        )

    def close(self) -> None:
        return None

    @property
    def total_changes(self) -> int:
        return self._total_changes

    def _post_with_retry(self, body: dict) -> List[D1Cursor]:
        """Execute ``_post`` with exponential backoff on :class:`D1TransientError`.

        Honours ``Retry-After`` for 429 responses. Permanent errors are raised
        immediately without retry.
        """
        last_exc: Optional[D1TransientError] = None
        start = time.monotonic()
        for attempt in range(_MAX_RETRIES):
            try:
                result = self._post(body)
                if attempt > 0:
                    logger.info(
                        "D1 request succeeded after %d retr%s in %.2fs",
                        attempt,
                        "y" if attempt == 1 else "ies",
                        time.monotonic() - start,
                    )
                return result
            except D1PermanentError:
                raise
            except D1TransientError as exc:
                last_exc = exc
                if attempt >= _MAX_RETRIES - 1:
                    break
                sleep_for = self._compute_backoff(attempt, exc)
                logger.warning(
                    "D1 transient error (attempt %d/%d), retrying in %.2fs: %s",
                    attempt + 1, _MAX_RETRIES, sleep_for, exc,
                )
                time.sleep(sleep_for)
        logger.error(
            "D1 request failed after %d attempts in %.2fs: %s",
            _MAX_RETRIES, time.monotonic() - start, last_exc,
        )
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _compute_backoff(attempt: int, exc: "D1TransientError") -> float:
        # If a Retry-After hint is attached to the exception, honour it.
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
        base = _RETRY_BASE_SEC * (2 ** attempt)
        return min(base, _RETRY_MAX_SLEEP_SEC) + random.uniform(0, 0.5)

    def _post(self, body: dict) -> List[D1Cursor]:
        try:
            response = requests.post(
                self._url, headers=self._headers, json=body, timeout=self._timeout
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
            if any(kw.lower() in err_text.lower() for kw in _TRANSIENT_ERROR_KEYWORDS):
                raise D1TransientError(f"D1 API transient error: {errors}")
            raise D1PermanentError(f"D1 API error: {errors}")

        return [D1Cursor(item) for item in payload.get("result") or []]


# ── Resolution helpers ───────────────────────────────────────────────────


def _resolve_credential(name: str) -> Optional[str]:
    """Look up *name* in env first, then in ``config.py``."""
    val = os.environ.get(name)
    if val:
        return val
    return cfg(name, None)


def get_d1_account_id() -> str:
    val = _resolve_credential("CLOUDFLARE_ACCOUNT_ID")
    if not val:
        raise D1Error("Missing CLOUDFLARE_ACCOUNT_ID env var or config")
    return val


def get_d1_api_token() -> str:
    val = _resolve_credential("CLOUDFLARE_API_TOKEN")
    if not val:
        raise D1Error("Missing CLOUDFLARE_API_TOKEN env var or config")
    return val


def get_d1_database_id(logical_name: str) -> str:
    """Look up the D1 database id by logical name (history/reports/operations)."""
    env_key = f"D1_{logical_name.upper()}_DB_ID"
    val = _resolve_credential(env_key)
    if not val:
        raise D1Error(f"Missing {env_key} env var or config")
    return val


def make_d1_connection(logical_name: str) -> D1Connection:
    """Construct a D1Connection for the given logical database name."""
    return D1Connection(
        account_id=get_d1_account_id(),
        database_id=get_d1_database_id(logical_name),
        api_token=get_d1_api_token(),
    )
