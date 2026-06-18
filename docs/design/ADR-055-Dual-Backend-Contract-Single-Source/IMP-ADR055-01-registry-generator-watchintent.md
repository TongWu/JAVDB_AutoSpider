# ADR-055 Phase 1 — Contract registry + generator + WatchIntent migration

**Status:** Completed — implemented and verified on 2026-06-15. MAIN branch `adr-055-contract-single-source` delivered the Python registry, generator, committed artifact, freshness guard, Python WatchIntent migration, docs/status closeout, and ADR/CONTEXT vocabulary. WEB branch `adr-055-sql-contract` delivered the vendoring script, generated production module, WatchIntent consumer migration, behavioral conformance smoke, CI freshness check, and manual re-vendor workflow. Fresh closeout verification passed for the targeted MAIN pytest suite, WEB typecheck/server/unit suites, vendored artifact byte-parity (`VENDORED == MAIN`), static SQL/bind-order searches, and `git diff --check` on both reviewed ranges.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Python contract registry + TS-codegen pipeline from [ADR-055](ADR-055-dual-backend-contract-single-source.md), then migrate the `WatchIntent` UPSERT (gap B6) end-to-end so the SQL + bind order exist in exactly one hand-authored place and the TS side is generated + CI-locked.

**Architecture:** A Python registry (`javdb/storage/contract/`) declares each static cross-backend SQL fragment once (SQL text + ordered typed params). A generator (`apps/cli/ops/dump_sql_contract.py`) emits the TS mirror `docs/api/contract/sql-contract.gen.ts` (an `export const …_SQL` + a `prepare…(db, {params})` typed bind helper per fragment). The Python backend consumes the registry directly via a generic `order_params` binder; the TS Worker `import`s the generated module (vendored via `scripts/fetch-sql-contract.mjs`, exactly like `api.gen.ts` / `query-builders.golden.json`). Drift is impossible (one author) and staleness is caught by a Python freshness test + a web CI freshness step. This mirrors the ADR-018 query-contract pipeline; the one structural difference is that the artifact is **production** TS under `server/contract/`, not a test fixture.

**Tech Stack:** Python 3.11+ (dataclasses, pytest), Node 20 (ESM script), TypeScript (Hono, Cloudflare Workers, vitest `cloudflare:test`), GitHub Actions.

**Repos & runbook:**
- MAIN monorepo: `/Users/tedwu/JAVDB_AutoSpider_CICD` — registry, generator, committed artifact, Python tests. `git -C /Users/tedwu/JAVDB_AutoSpider_CICD …`.
- WEB repo: `/Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web` — vendor script, `server/contract/` import, TS tests, CI. `git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web …`.
- Python tests (worktree, .venv broken): `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <paths> -p no:cacheprovider -q`.
- Web tests: `npm run test:server` (vitest `cloudflare:test` pool) and `npm run test:unit`.
- Conventional Commits; identity `Ted <ted@wu.engineer>`; no `[CLAUDE]` prefix. Never `git add -A` in MAIN (reports/*.db dirty) — stage explicit paths.

---

## Part A — MAIN repo: registry, generator, artifact, freshness

### Task 1: Registry primitives (`types.py`) + generic binder

**Files:**
- Create: `javdb/storage/contract/__init__.py`
- Create: `javdb/storage/contract/types.py`
- Test: `tests/unit/test_contract_types.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/test_contract_types.py
"""ADR-055: contract registry primitives."""
import pytest

from javdb.storage.contract.types import Param, SqlFragment, normalize_sql, order_params

_F = SqlFragment(
    name="demo",
    db="history",
    sql="INSERT INTO T (a, b) VALUES (?, ?)",
    params=(Param("a", "str", "string"), Param("b", "str | None", "string | null")),
)


def test_normalize_collapses_whitespace():
    assert normalize_sql("  a\n   b\t c ") == "a b c"


def test_order_params_orders_by_registry_not_call_site():
    # kwargs given out of order -> tuple still follows fragment param order.
    assert order_params(_F, b="bee", a="aye") == ("aye", "bee")


def test_order_params_rejects_missing_and_extra():
    with pytest.raises(ValueError):
        order_params(_F, a="aye")  # missing b
    with pytest.raises(ValueError):
        order_params(_F, a="aye", b="bee", c="nope")  # extra c
```

- [x] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py -q`
Expected: FAIL — `ModuleNotFoundError: javdb.storage.contract`.

- [x] **Step 3: Implement `types.py`**

```python
# javdb/storage/contract/types.py
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


def normalize_sql(sql: str) -> str:
    """Collapse whitespace runs to one space and trim (cross-backend norm)."""
    return re.sub(r"\s+", " ", sql).strip()


def order_params(fragment: SqlFragment, **kwargs: Any) -> tuple:
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
```

```python
# javdb/storage/contract/__init__.py
"""ADR-055 contract registry: the single source for static cross-backend SQL."""
from javdb.storage.contract.types import (
    Param,
    SqlFragment,
    normalize_sql,
    order_params,
)

__all__ = ["Param", "SqlFragment", "normalize_sql", "order_params"]
```

> `__init__.py` is self-contained here (types only). Task 2 extends it to also surface the `fragments` submodule, once `fragments.py` exists.

- [x] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py -q`
Expected: PASS (3 passed).

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/storage/contract/__init__.py javdb/storage/contract/types.py tests/unit/test_contract_types.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(contract): ADR-055 registry primitives (SqlFragment, order_params)"
```

---

### Task 2: WatchIntent fragment registry entry (`fragments.py`)

**Files:**
- Create: `javdb/storage/contract/fragments.py`
- Test: `tests/unit/test_contract_fragments.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/test_contract_fragments.py
"""ADR-055: the fragment registry is well-formed and queryable."""
from javdb.storage.contract import fragments
from javdb.storage.contract.types import SqlFragment, normalize_sql


def test_registry_is_nonempty_tuple_of_fragments():
    assert fragments.FRAGMENTS
    assert all(isinstance(f, SqlFragment) for f in fragments.FRAGMENTS)


def test_fragment_names_unique():
    names = [f.name for f in fragments.FRAGMENTS]
    assert len(names) == len(set(names))


def test_param_count_matches_placeholders():
    for f in fragments.FRAGMENTS:
        assert normalize_sql(f.sql).count("?") == len(f.params), f.name


def test_watch_intent_upsert_shape():
    f = fragments.WATCH_INTENT_UPSERT
    assert f.name == "watch_intent_upsert"
    assert f.db == "history"
    assert [p.name for p in f.params] == ["video_code", "href", "status", "notes"]
    norm = normalize_sql(f.sql)
    assert norm.startswith("INSERT INTO WatchIntent")
    assert "ON CONFLICT(video_code) DO UPDATE SET" in norm
    assert "notes = COALESCE(excluded.notes, notes)" in norm
```

- [x] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_fragments.py -q`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError`.

- [x] **Step 3: Implement `fragments.py` (SQL copied verbatim from the current `watchlist_repo.py`)**

```python
# javdb/storage/contract/fragments.py
"""Static SQL fragment registry (ADR-055). The single hand-edited source.

Each entry is mirrored to the TS Worker by apps/cli/ops/dump_sql_contract.py.
Add new static cross-backend mutations / static selects here, never by hand in
the other repo.
"""
from __future__ import annotations

from javdb.storage.contract.types import Param, SqlFragment

WATCH_INTENT_UPSERT = SqlFragment(
    name="watch_intent_upsert",
    db="history",
    sql="""
    INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at)
    VALUES (?, ?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(video_code) DO UPDATE SET
        href       = excluded.href,
        status     = excluded.status,
        notes      = COALESCE(excluded.notes, notes),
        status_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
""",
    params=(
        Param("video_code", "str", "string"),
        Param("href", "str", "string"),
        Param("status", "str", "string"),
        Param("notes", "str | None", "string | null"),
    ),
)

FRAGMENTS: tuple[SqlFragment, ...] = (WATCH_INTENT_UPSERT,)
```

Then extend `javdb/storage/contract/__init__.py` to surface the submodule (now that it exists) — replace its body with:

```python
# javdb/storage/contract/__init__.py
"""ADR-055 contract registry: the single source for static cross-backend SQL."""
from javdb.storage.contract import fragments
from javdb.storage.contract.types import (
    Param,
    SqlFragment,
    normalize_sql,
    order_params,
)

__all__ = ["Param", "SqlFragment", "normalize_sql", "order_params", "fragments"]
```

- [x] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_fragments.py tests/unit/test_contract_types.py -q`
Expected: PASS (7 passed total).

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/storage/contract/__init__.py javdb/storage/contract/fragments.py tests/unit/test_contract_fragments.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(contract): ADR-055 fragment registry with WatchIntent upsert"
```

---

### Task 3: Generator CLI (`dump_sql_contract.py`)

**Files:**
- Create: `apps/cli/ops/dump_sql_contract.py`
- Test: `tests/unit/test_dump_sql_contract.py`

- [x] **Step 1: Write the failing test** (assert the rendered TS shape; no file I/O)

```python
# tests/unit/test_dump_sql_contract.py
"""ADR-055: the generator renders a deterministic, idiomatic TS mirror."""
from apps.cli.ops.dump_sql_contract import render


def test_render_is_deterministic():
    assert render() == render()


def test_render_emits_const_and_typed_prepare_helper():
    out = render()
    assert "// AUTO-GENERATED" in out
    assert "export const WATCH_INTENT_UPSERT_SQL =" in out
    # typed bind helper: snake_case params -> camelCase object keys, registry order
    assert "export function prepareWatchIntentUpsert(" in out
    assert "db: D1Database," in out
    assert "videoCode: string; href: string; status: string; notes: string | null" in out
    assert "): D1PreparedStatement {" in out
    assert "return db.prepare(WATCH_INTENT_UPSERT_SQL).bind(p.videoCode, p.href, p.status, p.notes);" in out
```

- [x] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_dump_sql_contract.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [x] **Step 3: Implement the generator**

```python
# apps/cli/ops/dump_sql_contract.py
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


def _render_fragment(f: SqlFragment) -> str:
    const = _const_name(f.name)
    sql = normalize_sql(f.sql)  # SQL has no backticks -> safe in a template literal
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
```

- [x] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_dump_sql_contract.py -q`
Expected: PASS (2 passed).

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/cli/ops/dump_sql_contract.py tests/unit/test_dump_sql_contract.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(contract): ADR-055 dump_sql_contract generator"
```

---

### Task 4: Emit the committed artifact + Python freshness test

**Files:**
- Create (generated): `docs/api/contract/sql-contract.gen.ts`
- Test: `tests/unit/test_sql_contract_freshness.py`

- [x] **Step 1: Generate the artifact**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_sql_contract`
Expected: `wrote …/docs/api/contract/sql-contract.gen.ts`.

- [x] **Step 2: Eyeball the artifact**

Run: `cat /Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/contract/sql-contract.gen.ts`
Expected: header + `export const WATCH_INTENT_UPSERT_SQL = \`INSERT INTO WatchIntent …\`;` + `export function prepareWatchIntentUpsert(db, p) { return db.prepare(WATCH_INTENT_UPSERT_SQL).bind(p.videoCode, p.href, p.status, p.notes); }`.

- [x] **Step 3: Write the freshness test**

```python
# tests/unit/test_sql_contract_freshness.py
"""ADR-055: the committed TS mirror must match the registry (freshness guard)."""
from pathlib import Path

from apps.cli.ops.dump_sql_contract import OUT, render


def test_committed_artifact_is_fresh():
    committed = Path(OUT).read_text(encoding="utf-8")
    assert committed == render(), (
        "sql-contract.gen.ts is stale — run: "
        "python3 -m apps.cli.ops.dump_sql_contract and commit the result"
    )
```

- [x] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_sql_contract_freshness.py -q`
Expected: PASS (1 passed).

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/api/contract/sql-contract.gen.ts tests/unit/test_sql_contract_freshness.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(contract): ADR-055 emit sql-contract.gen.ts + freshness test"
```

---

### Task 5: Migrate the Python consumer; delete the parity test

**Files:**
- Modify: `javdb/storage/repos/watchlist_repo.py:10-39`
- Delete: `tests/unit/test_watch_intent_upsert_parity.py`
- Test: `tests/unit/test_watch_intent_upsert_behavior.py` (new behavioral smoke, ADR-055 D8)

- [x] **Step 1: Add a behavioral smoke test (column→value + notes-COALESCE)**

```python
# tests/unit/test_watch_intent_upsert_behavior.py
"""ADR-055 D8: behavioral smoke for the WatchIntent upsert (column->value mapping)."""
import pathlib
import sqlite3

import pytest

from javdb.storage import db as _db
from javdb.storage.repos.watchlist_repo import WatchIntentRepo

_DDL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    return WatchIntentRepo(db_path=path)


def test_upsert_maps_each_column_to_the_right_value(repo):
    # Distinct value per column so a swapped bind order would be caught.
    row = repo.upsert(video_code="AAA-111", href="/v/aaa", status="want", notes="mine")
    assert row["video_code"] == "AAA-111"
    assert row["href"] == "/v/aaa"
    assert row["status"] == "want"
    assert row["notes"] == "mine"


def test_upsert_preserves_notes_when_omitted(repo):
    repo.upsert(video_code="AAA-111", href="/v/aaa", status="want", notes="keep me")
    # status-only update with notes=None must NOT wipe the stored note (COALESCE).
    row = repo.upsert(video_code="AAA-111", href="/v/aaa", status="viewed", notes=None)
    assert row["status"] == "viewed"
    assert row["notes"] == "keep me"
```

- [x] **Step 2: Run it — expect pass against the CURRENT code** (proves the smoke is valid before refactor)

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_watch_intent_upsert_behavior.py -q`
Expected: PASS (2 passed).

- [x] **Step 3: Migrate `watchlist_repo.py` to the registry**

Replace the top-of-file SQL constant block (lines 10-23, `# Byte-mirrored …` through the closing `"""`) with:

```python
from javdb.storage.contract import fragments, order_params

# Single source of truth: the ADR-055 contract registry. Re-exported for any
# back-compat importers; the SQL itself lives only in javdb/storage/contract.
WATCH_INTENT_UPSERT_SQL = fragments.WATCH_INTENT_UPSERT.sql
```

Then in `WatchIntentRepo.upsert`, replace the execute line:

```python
            conn.execute(WATCH_INTENT_UPSERT_SQL, (video_code, href, status, notes))
```

with:

```python
            conn.execute(
                fragments.WATCH_INTENT_UPSERT.sql,
                order_params(
                    fragments.WATCH_INTENT_UPSERT,
                    video_code=video_code,
                    href=href,
                    status=status,
                    notes=notes,
                ),
            )
```

- [x] **Step 4: Delete the now-redundant parity test**

Run: `git -C /Users/tedwu/JAVDB_AutoSpider_CICD rm tests/unit/test_watch_intent_upsert_parity.py`
Rationale: the registry is the single source; cross-repo sync is enforced by the freshness test (Task 4) + web CI (Task 8), not a hand-typed CANONICAL.

- [x] **Step 5: Run the watchlist Python suite — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_watchlist_repo.py tests/unit/test_watchlist_router.py tests/unit/test_watch_intent_upsert_behavior.py tests/unit/test_contract_fragments.py -p no:cacheprovider -q`
Expected: PASS (all green; `test_watchlist_repo.py` 6, `test_watchlist_router.py` 4, behavior 2, fragments 4).

- [x] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/storage/repos/watchlist_repo.py tests/unit/test_watch_intent_upsert_behavior.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add -u tests/unit/test_watch_intent_upsert_parity.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "refactor(watchlist): source WatchIntent upsert from ADR-055 registry; drop parity test"
```

---

## Part B — WEB repo: vendor, consume, CI

### Task 6: Vendor script + `gen:sql-contract`

**Files:**
- Create: `scripts/fetch-sql-contract.mjs`
- Modify: `package.json` (scripts)

- [x] **Step 1: Create the vendor script (mirrors `scripts/fetch-query-golden.mjs`)**

```javascript
// JAVDB_AutoSpider_Web/scripts/fetch-sql-contract.mjs
#!/usr/bin/env node
/**
 * fetch-sql-contract.mjs
 *
 * Resolves the ADR-055 generated SQL contract module from the main repo (local
 * file or remote URL) and writes the vendored copy to server/contract/. Unlike
 * the query golden (a test fixture), this is PRODUCTION code the Worker imports.
 * Mirrors fetch-openapi.mjs / fetch-query-golden.mjs (ADR-018 D4/D6).
 *
 * Dev:  SQL_CONTRACT_PATH=/path/to/docs/api/contract/sql-contract.gen.ts node scripts/fetch-sql-contract.mjs
 * CI:   node scripts/fetch-sql-contract.mjs            (fetches GitHub raw URL)
 * Auth: SQL_CONTRACT_TOKEN=ghp_xxx node scripts/fetch-sql-contract.mjs
 */
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT = path.join(ROOT, 'server', 'contract', 'sql-contract.gen.ts')

const SRC_PATH = process.env['SQL_CONTRACT_PATH'] ?? ''
const SRC_URL =
  process.env['SQL_CONTRACT_URL'] ??
  'https://raw.githubusercontent.com/TongWu/JAVDB_AutoSpider_CICD/main/docs/api/contract/sql-contract.gen.ts'
const TOKEN =
  process.env['SQL_CONTRACT_TOKEN'] ??
  process.env['OPENAPI_TOKEN'] ??
  process.env['GITHUB_TOKEN'] ??
  ''

function assertContract(text) {
  if (!text.includes('AUTO-GENERATED') || !text.includes('export const')) {
    throw new Error('sql-contract payload is not a generated TS module')
  }
}

async function resolveContract() {
  if (SRC_PATH) {
    console.log(`[fetch-sql-contract] reading local: ${SRC_PATH}`)
    const data = await readFile(SRC_PATH, 'utf-8')
    assertContract(data)
    return data
  }
  console.log(`[fetch-sql-contract] fetching: ${SRC_URL}`)
  const headers = TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}
  const res = await fetch(SRC_URL, { headers, signal: AbortSignal.timeout(15_000) })
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${SRC_URL}`)
  const text = await res.text()
  assertContract(text)
  return text
}

async function main() {
  const ts = await resolveContract()
  await mkdir(path.dirname(OUT), { recursive: true })
  await writeFile(OUT, ts, 'utf-8')
  console.log(`[fetch-sql-contract] wrote ${OUT}`)
}

main().catch((err) => {
  console.error('[fetch-sql-contract] failed:', err)
  process.exit(1)
})
```

- [x] **Step 2: Add the npm script**

In `package.json` `"scripts"`, add next to `"gen:query-golden"`:

```json
    "gen:sql-contract": "node scripts/fetch-sql-contract.mjs",
```

- [x] **Step 3: Vendor the artifact from the local main repo**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && SQL_CONTRACT_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract`
Expected: `[fetch-sql-contract] wrote …/server/contract/sql-contract.gen.ts`.

- [x] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add scripts/fetch-sql-contract.mjs package.json server/contract/sql-contract.gen.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "feat(server): vendor ADR-055 sql-contract.gen.ts + gen:sql-contract"
```

---

### Task 7: Migrate the TS consumer; delete the parity test; conformance smoke

**Files:**
- Modify: `server/services/watchlist-service.ts:13-36`
- Delete: `server/__tests__/watch-intent-upsert-parity.test.ts`
- Test: `server/__tests__/watch-intent-contract.test.ts` (new behavioral conformance)

- [x] **Step 1: Migrate `watchlist-service.ts` to import the generated module**

Delete the hand-written const block (lines 13-25, `// Byte-mirrored …` through the closing `` `; ``). Add the import at the top of the file (below the leading `// Watch-intent …` comment, before `export interface WatchIntentRow`):

```typescript
import { prepareWatchIntentUpsert } from "../contract/sql-contract.gen";
```

Replace the body of `upsertWatchIntent`:

```typescript
  await db.prepare(WATCH_INTENT_UPSERT_SQL).bind(videoCode, href, status, notes).run();
```

with:

```typescript
  await prepareWatchIntentUpsert(db, { videoCode, href, status, notes }).run();
```

(The SQL string and bind order now live only in the generated module.)

- [x] **Step 2: Delete the redundant parity test**

Run: `git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web rm server/__tests__/watch-intent-upsert-parity.test.ts`

- [x] **Step 3: Add a behavioral conformance test (models the `cloudflare:test` D1 harness in `watchlist-routes.test.ts`)**

```typescript
// JAVDB_AutoSpider_Web/server/__tests__/watch-intent-contract.test.ts
import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { prepareWatchIntentUpsert } from "../contract/sql-contract.gen";

async function seed() {
  await env.HISTORY_DB.prepare(
    `CREATE TABLE IF NOT EXISTS WatchIntent (
      video_code TEXT PRIMARY KEY, href TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('want','viewed')),
      notes TEXT, status_at TEXT,
      updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )`,
  ).run();
}

describe("ADR-055 generated WatchIntent upsert", () => {
  beforeAll(seed);
  beforeEach(async () => {
    await env.HISTORY_DB.prepare("DELETE FROM WatchIntent").run();
  });

  it("maps each column to the right value (bind order from the generator)", async () => {
    await prepareWatchIntentUpsert(env.HISTORY_DB, {
      videoCode: "AAA-111",
      href: "/v/aaa",
      status: "want",
      notes: "mine",
    }).run();
    const row = await env.HISTORY_DB.prepare(
      "SELECT * FROM WatchIntent WHERE video_code = ?",
    )
      .bind("AAA-111")
      .first<{ video_code: string; href: string; status: string; notes: string | null }>();
    expect(row).toMatchObject({
      video_code: "AAA-111",
      href: "/v/aaa",
      status: "want",
      notes: "mine",
    });
  });

  it("preserves notes when omitted on a status-only update (COALESCE)", async () => {
    await prepareWatchIntentUpsert(env.HISTORY_DB, {
      videoCode: "AAA-111", href: "/v/aaa", status: "want", notes: "keep me",
    }).run();
    await prepareWatchIntentUpsert(env.HISTORY_DB, {
      videoCode: "AAA-111", href: "/v/aaa", status: "viewed", notes: null,
    }).run();
    const row = await env.HISTORY_DB.prepare(
      "SELECT status, notes FROM WatchIntent WHERE video_code = ?",
    )
      .bind("AAA-111")
      .first<{ status: string; notes: string | null }>();
    expect(row).toEqual({ status: "viewed", notes: "keep me" });
  });
});
```

- [x] **Step 4: Type-check + run the server suite**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server`
Expected: typecheck clean; all server specs pass, including `watch-intent-contract.test.ts` (2) and the existing `watchlist-routes.test.ts`; the deleted parity spec no longer collected.

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add server/services/watchlist-service.ts server/__tests__/watch-intent-contract.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add -u server/__tests__/watch-intent-upsert-parity.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "refactor(server): consume ADR-055 generated WatchIntent upsert; drop parity test"
```

---

### Task 8: Web CI freshness step + manual re-vendor workflow

**Files:**
- Modify: `.github/workflows/ci.yml` (add a freshness step after the query-golden one)
- Create: `.github/workflows/revendor-sql-contract.yml`

- [x] **Step 1: Add the freshness step to `ci.yml`**

Immediately after the existing `- name: Refresh query Contract Golden from main repo` step, add:

```yaml
      - name: Refresh SQL contract from main repo
        # ADR-055 freshness check: re-fetch the Python-main generated SQL
        # contract and confirm the committed vendored copy matches. Catches
        # Python-side drift (stale vendor). Mirrors the query-golden step.
        env:
          SQL_CONTRACT_URL: https://raw.githubusercontent.com/TongWu/JAVDB_AutoSpider_CICD/main/docs/api/contract/sql-contract.gen.ts
          SQL_CONTRACT_TOKEN: ${{ secrets.CICD_REPO_TOKEN }}
        run: |
          npm run gen:sql-contract
          if ! git diff --quiet server/contract/sql-contract.gen.ts; then
            echo "::error::Vendored sql-contract.gen.ts is out of sync with Python main."
            echo "Run 'npm run gen:sql-contract' and commit, or trigger the 'Re-vendor SQL contract' workflow."
            git --no-pager diff server/contract/sql-contract.gen.ts | head -100
            exit 1
          fi
```

- [x] **Step 2: Create the re-vendor workflow (mirrors `revendor-query-golden.yml`)**

```yaml
# JAVDB_AutoSpider_Web/.github/workflows/revendor-sql-contract.yml
name: Re-vendor SQL contract

# ADR-055: dispatched by the Python repo when sql-contract.gen.ts changes on
# main (or run manually). Re-fetches the vendored module and opens a PR. CI on
# that PR runs the freshness check (now green) + the conformance smoke.
on:
  repository_dispatch:
    types: [sql-contract-updated]
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

env:
  NODE_VERSION: '20'

jobs:
  revendor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'
      - name: Install dependencies
        run: npm ci
      - name: Refresh vendored SQL contract
        env:
          SQL_CONTRACT_TOKEN: ${{ secrets.CICD_REPO_TOKEN }}
        run: npm run gen:sql-contract
      - name: Open re-vendor PR
        uses: peter-evans/create-pull-request@v6
        with:
          token: ${{ secrets.REVENDOR_PR_TOKEN }}
          branch: chore/revendor-sql-contract
          title: "chore: re-vendor SQL contract (ADR-055)"
          commit-message: "chore: re-vendor SQL contract (ADR-055)"
          body: |
            Upstream `sql-contract.gen.ts` changed on Python `main`.

            The vendored generated module has been refreshed. Freshness CI is
            now green; the conformance smoke (`npm run test:server`) confirms the
            generated bind helpers still execute correctly. Merge once green.
```

- [x] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add .github/workflows/ci.yml .github/workflows/revendor-sql-contract.yml
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "ci(server): ADR-055 sql-contract freshness check + re-vendor workflow"
```

> **Follow-up (CLOSED 2026-06-18):** the deferred auto-push sender now exists. MAIN-repo `.github/workflows/publish-sql-contract.yml` mirrors `publish-query-contract.yml` — it fires `repository_dispatch: sql-contract-updated` to the web repo whenever `docs/api/contract/sql-contract.gen.ts` changes on `main` (also triggers on `javdb/storage/contract/**` and `apps/cli/ops/dump_sql_contract.py`, with a regen + self-commit safety net and a push-diff gate so the dispatch fires even when the artifact arrived already-committed in the source PR). The WEB CI freshness step remains as a backstop, and `workflow_dispatch` is still available for manual runs.

---

## Part C — Docs + cross-repo verification

### Task 9: ADR/CONTEXT updates + cross-repo green check

**Files:**
- Modify: `docs/design/ADR-055-Dual-Backend-Contract-Single-Source/ADR-055-dual-backend-contract-single-source.md` + `.zh.md` (Status Log: P1 in progress→delivered)
- Modify: `docs/design/ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md` + `.zh.md` (Status Log back-ref to ADR-055 executing D7)
- Modify: `CONTEXT.md` (add the three ADR-055 domain terms)

- [x] **Step 1: ADR-055 Status Log** — append to both `.md` and `.zh.md`:
  `2026-MM-DD: Phase 1 delivered (IMP-ADR055-01) — registry + generator + freshness/conformance CI; WatchIntent upsert migrated (4 copies → 1 registry entry); hand-CANONICAL parity tests removed.`

- [x] **Step 2: ADR-018 Status Log back-ref** — append to both `.md` and `.zh.md`:
  `2026-MM-DD: D7 ("eliminate") executed for the static surface by ADR-055 (extends, does not supersede this guard for dynamic builders).`

- [x] **Step 3: CONTEXT.md** — add `Contract registry`, `SQL fragment`, `Generated contract module` (copy the wording from ADR-055 "Domain Language").

- [x] **Step 4: Full cross-repo verification**

Run (MAIN): `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py tests/unit/test_contract_fragments.py tests/unit/test_dump_sql_contract.py tests/unit/test_sql_contract_freshness.py tests/unit/test_watch_intent_upsert_behavior.py tests/unit/test_watchlist_repo.py tests/unit/test_watchlist_router.py -p no:cacheprovider -q`
Expected: all green.

Run (WEB): `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server && npm run test:unit`
Expected: all green.

Run (freshness parity proof): re-vendor and confirm no diff —
`cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && SQL_CONTRACT_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract && git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web diff --quiet server/contract/sql-contract.gen.ts && echo "VENDORED == MAIN ✓"`
Expected: `VENDORED == MAIN ✓`.

- [x] **Step 5: Commit docs**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/design/ADR-055-Dual-Backend-Contract-Single-Source CONTEXT.md docs/design/ADR-018-Dual-Backend-Query-Contract
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "docs(adr-055): mark Phase 1 delivered; ADR-018 back-ref; CONTEXT terms"
```

---

## Done-when (Phase 1 acceptance)

- [x] `javdb/storage/contract/` is the only hand-authored home of the WatchIntent upsert SQL + bind order.
- [x] `docs/api/contract/sql-contract.gen.ts` is generated, committed, and freshness-tested in MAIN.
- [x] The web Worker imports `prepareWatchIntentUpsert` from the vendored `server/contract/sql-contract.gen.ts`; no hand-written WatchIntent SQL remains in the web repo.
- [x] Both hand-CANONICAL parity tests are deleted; behavioral conformance smokes pass on both sides.
- [x] Web CI fails if the vendored module drifts from Python `main`.
- [x] All MAIN + WEB suites green; `VENDORED == MAIN ✓`.

## Completion Evidence

- 2026-06-15 MAIN verification: `PYTHONPATH=/Users/tedwu/.codex/worktrees/e11b/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py tests/unit/test_contract_fragments.py tests/unit/test_dump_sql_contract.py tests/unit/test_sql_contract_freshness.py tests/unit/test_watch_intent_upsert_behavior.py tests/unit/test_watchlist_repo.py tests/unit/test_watchlist_router.py -p no:cacheprovider -q` — 24 passed.
- 2026-06-15 WEB verification: `npm run typecheck && npm run typecheck:server && npm run test:server && npm run test:unit` — server 41 files / 384 tests passed; unit 39 files / 155 tests passed.
- 2026-06-15 vendored freshness proof: `SQL_CONTRACT_PATH=/Users/tedwu/.codex/worktrees/e11b/JAVDB_AutoSpider_CICD/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract && git diff --quiet server/contract/sql-contract.gen.ts` — `VENDORED == MAIN`.
- 2026-06-15 static guard checks: MAIN/Web searches show the remaining WatchIntent SQL string and bind helper only in the registry/generated artifacts/tests; the Web service imports `prepareWatchIntentUpsert`; both obsolete WatchIntent hand-CANONICAL parity tests are deleted.
- 2026-06-15 diff hygiene: `git diff --check main..HEAD` passed in MAIN; `git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web diff --check main..HEAD` passed in WEB.
