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
# D1 doesn't actually advertise a fixed "statements per /query" cap. The real
# limits per Cloudflare's D1 Platform Limits page (last reviewed 2026-04) are:
#   • per-statement bound parameters: 100
#   • per-statement SQL length: 100 KB
#   • per-request execution time: 30 s (matches our default _API_TIMEOUT)
#   • response payload size: ~10 MB
# Empirically, batches above ~50 row-INSERT statements start nudging the 30 s
# wall on cold DOs; 50 has been a comfortable, conservative chunk size in
# production. Override via ``D1_BATCH_LIMIT`` if you have a workload with very
# small / very large rows that warrants a different trade-off.
_BATCH_LIMIT = _env_int("D1_BATCH_LIMIT", 50)
_MAX_RETRIES = _env_int("D1_MAX_RETRIES", 5)
_RETRY_BASE_SEC = _env_float("D1_RETRY_BASE_SEC", 1.0)
_RETRY_MAX_SLEEP_SEC = _env_float("D1_RETRY_MAX_SLEEP_SEC", 30.0)

# Substrings in CF "errors[].message" / errors[].code that signal a recoverable
# backend hiccup. Compared case-insensitively against the stringified errors.
_TRANSIENT_ERROR_KEYWORDS = (
    "D1_RESET_DO",
    "busy",
    "timeout",
    "overloaded",
    "internal error",
    "temporarily",
    # CF D1 returns this 400/code-7500 when a manual or scheduled D1 export is
    # holding a database-wide read/write lock. Treat as transient so callers
    # back off and retry instead of dropping the write.
    "long-running export",
    "7500",
)

# Substrings indicating the export-lock case specifically. Backoff for these is
# overridden to a longer fixed floor since exports typically last 10-60s and
# short retries waste round-trips on the lock window.
_EXPORT_LOCK_KEYWORDS = (
    "long-running export",
    "7500",
)
_EXPORT_LOCK_BACKOFF_FLOOR_SEC = _env_float("D1_EXPORT_LOCK_FLOOR_SEC", 15.0)


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


def _matches_keyword(text: str, keywords: Sequence[str]) -> bool:
    haystack = text.lower()
    return any(kw.lower() in haystack for kw in keywords)


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
        # A single Session reuses the underlying urllib3 connection pool, so we
        # avoid repeating the ~70-80ms TCP+TLS handshake on every D1 request
        # (the reconciler in particular issues thousands of SELECTs per run).
        # ``_post_request`` is captured as an instance attribute so unit tests
        # can monkey-patch a single instance without touching the global
        # ``requests`` module.
        self._session = requests.Session()
        self._post_request = self._session.post
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
        """Execute a sequence of DDL statements separated by ``;``.

        Intentionally limited: ``script`` is split on the literal ``;``
        character and is therefore only safe for **schema DDL with no
        semicolons inside string literals, triggers, or CHECK
        expressions**. The codebase only ever feeds simple
        ``CREATE TABLE`` / ``CREATE INDEX`` / ``ALTER TABLE`` / ``DROP``
        statements through this path (see ``packages/python/javdb_platform/db.py``),
        so a full SQL parser would be overkill — but if you ever extend
        this to user-supplied scripts or trigger bodies, swap the naive
        split for a real parser (``sqlparse.split`` is the obvious
        choice) before doing so.
        """
        statements = [s.strip() for s in script.split(";") if s.strip()]
        if not statements:
            return
        body_statements = [{"sql": s, "params": []} for s in statements]
        for chunk in _split(body_statements, _BATCH_LIMIT):
            self._post_with_retry({"batch": chunk})

    def batch_execute(
        self,
        statements: Sequence[Tuple[str, Sequence[Any]]],
    ) -> List[D1Cursor]:
        """Execute a list of ``(sql, params)`` tuples with CF batch ``/query``.

        Unlike :meth:`executemany`, statements may differ in SQL and the result
        cursors are returned in submission order so the caller can read SELECT
        rows back. Splits at :data:`_BATCH_LIMIT` (CF caps batches at 50).

        Note: each batch is atomic on D1's side — if any statement in a chunk
        fails the whole chunk rolls back. Callers that need per-row error
        attribution should fall back to per-statement :meth:`execute` on
        :class:`D1Error`.
        """
        cursors: List[D1Cursor] = []
        if not statements:
            return cursors
        body_stmts = [{"sql": s, "params": list(p)} for s, p in statements]
        for chunk in _split(body_stmts, _BATCH_LIMIT):
            chunk_cursors = self._post_with_retry({"batch": chunk})
            for c in chunk_cursors:
                self._total_changes += c.rowcount
            cursors.extend(chunk_cursors)
        return cursors

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        logger.warning(
            "D1Connection.rollback() called but D1 auto-commits each request; "
            "rollback is a no-op."
        )

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 — closing must not raise
            pass

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
        # Export-lock errors require a longer floor: short retries reliably
        # land back inside the same lock window.
        if getattr(exc, "is_export_lock", False):
            base = max(base, _EXPORT_LOCK_BACKOFF_FLOOR_SEC)
        return min(base, _RETRY_MAX_SLEEP_SEC) + random.uniform(0, 0.5)

    def _post(self, body: dict) -> List[D1Cursor]:
        try:
            response = self._post_request(
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
            # CF returns HTTP 400 for application-level errors too, including the
            # "long-running export" lock (code 7500). Inspect the JSON body so we
            # can promote those to D1TransientError and let _post_with_retry back
            # off — otherwise a single CF export would silently drop every write.
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
                raise D1PermanentError(
                    f"D1 API returned HTTP {status}: {errors}"
                )
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

    @staticmethod
    def _extract_errors(response) -> Optional[list]:
        """Best-effort extraction of CF ``errors`` array from a 4xx response.

        Returns ``None`` when the body is not JSON or has no ``errors`` /
        ``messages`` array — caller should then fall back to raw-text error.
        """
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("errors") or payload.get("messages") or []


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
