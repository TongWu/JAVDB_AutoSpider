"""Contract registry primitives (ADR-055).

Single source of truth for static cross-backend SQL fragments. The Python
backend consumes these directly; the TS Worker consumes the generated mirror
(docs/api/contract/sql-contract.gen.ts -> vendored server/contract/sql-contract.gen.ts).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Tuple


@dataclass(frozen=True)
class Param:
    name: str       # snake_case (Python/SQL); the generator camelCases it for TS
    py_type: str    # e.g. "str", "str | None", "int"
    ts_type: str    # e.g. "string", "string | null", "number"


@dataclass(frozen=True)
class SqlFragment:
    name: str                  # snake_case id, e.g. "watch_intent_upsert"
    db: str                    # 'history' | 'reports' | 'operations' (context)
    sql: str                   # SQLite, ? placeholders
    params: Tuple[Param, ...]  # ordered; count MUST equal the number of ? in sql


@dataclass(frozen=True)
class SharedConstant:
    name: str                  # snake_case id; the generator uppercases it for TS
    kind: str                  # 'string' | 'number' | 'string_set' | 'string_array'
    values: str | int | Tuple[str, ...]


def normalize_sql(sql: str) -> str:
    """Collapse whitespace runs to one space and trim (cross-backend norm)."""
    return re.sub(r"\s+", " ", sql).strip()


def order_params(fragment: SqlFragment, /, **kwargs: Any) -> tuple:
    """Return the bind tuple in the fragment's declared param order.

    Callers pass params by keyword; order comes from the registry, so a caller
    can never get bind-order wrong (ADR-055 D5).
    """
    expected = [p.name for p in fragment.params]
    missing = [n for n in expected if n not in kwargs]
    extra = [k for k in kwargs if k not in expected]
    if missing or extra:
        raise ValueError(
            f"order_params({fragment.name}): missing={missing} extra={extra}"
        )
    return tuple(kwargs[n] for n in expected)
