"""Generate the TS contract mirror to docs/api/contract/sql-contract.gen.ts (ADR-055).

Source of truth: javdb/storage/contract. Mirrors apps/cli/ops/dump_query_contract.py,
except the artifact is production TS the Worker imports (not a test fixture).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.storage.contract import fragments as _frag  # noqa: E402
from javdb.storage.contract.types import SqlFragment, normalize_sql  # noqa: E402

OUT = REPO_ROOT / "docs" / "api" / "contract" / "sql-contract.gen.ts"


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))


def _const_name(name: str) -> str:
    return name.upper() + "_SQL"


def _escape_template_literal_sql(sql: str) -> str:
    return sql.replace("`", r"\`").replace("${", r"\${")


def _render_fragment(f: SqlFragment) -> str:
    const = _const_name(f.name)
    sql = _escape_template_literal_sql(normalize_sql(f.sql))
    fields = "; ".join(f"{_camel(p.name)}: {p.ts_type}" for p in f.params)
    binds = ", ".join(f"p.{_camel(p.name)}" for p in f.params)
    return (
        f"export const {const} =\n  `{sql}`;\n\n"
        f"export function prepare{_pascal(f.name)}(\n"
        f"  db: D1Database,\n"
        f"  p: {{ {fields} }},\n"
        f"): D1PreparedStatement {{\n"
        f"  return db.prepare({const}).bind({binds});\n"
        f"}}\n"
    )


def render() -> str:
    body = "\n".join(_render_fragment(f) for f in _frag.FRAGMENTS)
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
