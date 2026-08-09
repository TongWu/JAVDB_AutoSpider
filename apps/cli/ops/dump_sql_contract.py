"""Generate the TS contract mirror to docs/api/contract/sql-contract.gen.ts (ADR-055).

Source of truth: javdb/storage/contract. Mirrors apps/cli/ops/dump_query_contract.py,
except the artifact is production TS the Worker imports (not a test fixture).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.storage.contract import fragments as _frag  # noqa: E402
from javdb.storage.contract.types import SharedConstant, SqlFragment, normalize_sql  # noqa: E402

OUT = REPO_ROOT / "docs" / "api" / "contract" / "sql-contract.gen.ts"


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))


def _const_name(name: str) -> str:
    return name.upper() + "_SQL"


def _shared_const_name(name: str) -> str:
    return name.upper()


def _escape_template_literal_sql(sql: str) -> str:
    return sql.replace("`", r"\`").replace("${", r"\${")


def _render_fragment(f: SqlFragment) -> str:
    const = _const_name(f.name)
    sql = _escape_template_literal_sql(normalize_sql(f.sql))
    if f.params:
        fields = "; ".join(f"{_camel(p.name)}: {p.ts_type}" for p in f.params)
        binds = ", ".join(f"p.{_camel(p.name)}" for p in f.params)
        params_arg = f"  p: {{ {fields} }},\n"
        bind_call = f".bind({binds})"
    else:
        params_arg = "  _p?: Record<string, never>,\n"
        bind_call = ".bind()"
    return (
        f"export const {const} =\n  `{sql}`;\n\n"
        f"export function prepare{_pascal(f.name)}(\n"
        f"  db: D1Database,\n"
        f"{params_arg}"
        f"): D1PreparedStatement {{\n"
        f"  return db.prepare({const}){bind_call};\n"
        f"}}\n"
    )


def _render_constant(c: SharedConstant) -> str:
    const = _shared_const_name(c.name)
    if c.kind == "string":
        if not isinstance(c.values, str):
            raise TypeError(f"{c.name}: string constant requires a string value")
        return f"export const {const} = {json.dumps(c.values)};\n"
    if c.kind == "number":
        if isinstance(c.values, bool) or not isinstance(c.values, int):
            raise TypeError(f"{c.name}: number constant requires an int value")
        return f"export const {const} = {c.values};\n"
    if isinstance(c.values, (str, int)):
        raise TypeError(f"{c.name}: {c.kind} constant requires tuple values")
    if not all(isinstance(value, str) for value in c.values):
        raise TypeError(f"{c.name}: {c.kind} constant requires string tuple values")
    values = "\n".join(f"  {json.dumps(value)}," for value in c.values)
    if c.kind == "string_set":
        return (
            f"export const {const}: ReadonlySet<string> = new Set([\n"
            f"{values}\n"
            "]);\n"
        )
    if c.kind == "string_array":
        return (
            f"export const {const}: readonly string[] = [\n"
            f"{values}\n"
            "];\n"
        )
    raise ValueError(f"unknown shared constant kind: {c.kind}")


def render() -> str:
    parts = [_render_fragment(f) for f in _frag.FRAGMENTS]
    parts.extend(_render_constant(c) for c in _frag.CONSTANTS)
    body = "\n".join(parts)
    version = hashlib.sha256(body.encode()).hexdigest()[:16]
    header = (
        "// AUTO-GENERATED from javdb/storage/contract — DO NOT EDIT.\n"
        "// Source of truth: ADR-055. Regenerate: "
        "python3 -m apps.cli.ops.dump_sql_contract\n"
        f"// version: {version}\n"
        "/* eslint-disable */\n\n"
    )
    return header + body


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
