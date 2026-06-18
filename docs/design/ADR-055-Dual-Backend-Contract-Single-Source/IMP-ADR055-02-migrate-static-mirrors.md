# ADR-055 Phase 2 — Migrate remaining static mirror points

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Completed — implemented and verified on 2026-06-15; reconciled with `main` 2026-06-17.

> **Reconciliation note (2026-06-17):** While this branch was open, `main` independently landed the ADR-055 `SharedConstant` primitive **and the four Phase-2 registry entries** (`ACTOR_SUBSCRIPTION_UPSERT`, `SYSTEM_STATE_UPSERT`, `VALID_RULE_MODES`, `VALUE_REQUIRED`, `REPORT_SESSION_COLUMNS`) alongside [#220](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/220) (incident alerting), with a more complete generator. On merge, this branch **adopted main's registry implementation wholesale** (its `SharedConstant(name, kind, values)` with kinds `string`/`number`/`string_set`/`string_array`; constants in `fragments.py` `CONSTANTS`) and dropped the parallel pieces it had introduced (`javdb/storage/contract/constants.py`, `str_set`/`str_list` accessors, a separate constants test). The **net delivered contribution of this IMP is therefore the consumer-side migration** — wiring both backends onto the registry, deleting the hand-CANONICAL parity tests, and adding the behavioral/conformance smokes. Part A below documents the registry build as originally planned; in execution it was satisfied by main's #220. Consumers read `frozenset(fragments.X.values)` / `", ".join(fragments.REPORT_SESSION_COLUMNS.values)` (main's API), not the `as_str_*` accessors this plan first described.

**Goal:** Fold the four remaining hand-mirrored static cross-backend constructs — the `ActorSubscription` upsert, the `system_state` upsert, the content-filter allow-lists (`VALID_RULE_MODES` / `VALUE_REQUIRED`), and the `ReportSessions` column projection — into the ADR-055 contract registry so each lives in exactly one hand-authored place, the TS side is generated + CI-locked, and the redundant hand-CANONICAL parity tests are deleted.

**Architecture:** Phase 1 ([IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md)) stood up a Python registry (`javdb/storage/contract/`) of `SqlFragment`s, a generator (`apps/cli/ops/dump_sql_contract.py`) that emits `docs/api/contract/sql-contract.gen.ts`, a Python freshness test, web vendoring (`scripts/fetch-sql-contract.mjs`), and a web CI freshness/conformance guard. Phase 2 (a) adds **two more `SqlFragment`s** (ActorSubscription, system_state), (b) introduces a **`SharedConstant`** primitive + generator support so the same artifact can also emit shared **data constants** (sets and lists), and (c) migrates both backends' consumers to the registry. Two constructs are subtle: the content-filter allow-lists move from Python `(dimension, mode)` **tuple** keys to encoded `"dim:mode"` **string** keys so Python and TS share one representation; and the `ReportSessions` column list is consumed by the ADR-018-guarded *dynamic* `buildSessionQuery` / `_build_session_query` builders, so the migration must keep the **assembled** SQL byte-identical (the ADR-018 query golden regen must be a no-op).

**Tech Stack:** Python 3.11+ (dataclasses, pytest), Node 20 (ESM script), TypeScript (Hono, Cloudflare Workers, vitest `cloudflare:test`), GitHub Actions.

**Repos & runbook:**
- MAIN monorepo (this worktree): `/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0`, branch `claude/happy-hoover-2ec5b0`. `git -C <MAIN> …`.
- WEB repo (separate git repo): `/Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web`. Create + work on branch `claude/adr055-p2-static-mirrors` (Task C0). `git -C <WEB> …`.
- **Python tests — use the WORKTREE root in PYTHONPATH** (the main checkout at `/Users/tedwu/JAVDB_AutoSpider_CICD` is on `main` and does NOT have these changes; pointing PYTHONPATH there silently imports the old code). Run from the worktree dir:
  `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <paths> -p no:cacheprovider -q`
  Add `--continue-on-collection-errors` for broad runs (baseline ≈57 pre-existing 401/Rust env failures are NOT regressions).
- Web tests: `cd <WEB> && npm run typecheck && npm run test:server && npm run test:unit`.
- Conventional Commits; identity `Ted <ted@wu.engineer>`; no `[CLAUDE]` prefix. **Never `git add -A` in MAIN** (reports/*.db get dirtied) — stage explicit paths.

**Verified construct shapes (source of truth for this plan):**

| # | Construct | Python source | TS source | Parity test to DELETE (MAIN / WEB) |
| --- | --- | --- | --- | --- |
| 1 | ActorSubscription upsert | `javdb/storage/repos/subscription_repo.py:17-49` | `server/services/subscription-service.ts:24-49` | `tests/unit/test_actor_subscription_upsert_parity.py` / `server/__tests__/actor-subscription-upsert-parity.test.ts` |
| 2 | content-filter allow-lists | `apps/cli/ops/content_filter.py:21-49` (+ router `apps/api/routers/content_filter.py:27-31,84-89`) | `server/routes/content-filter.ts:21-50` | `tests/unit/test_content_filter_modes_parity.py` / `server/__tests__/content-filter-modes-parity.test.ts` |
| 3 | system_state upsert | `javdb/storage/repos/system_state_repo.py:39-48` | `server/services/system-state-store.ts:11-25` | none (intra-backend dedup only — add smokes, delete nothing) |
| 4 | ReportSessions columns | `javdb/storage/repos/sessions_repo.py:62-66,119-121` | `server/routes/sessions.ts:34-35` (consumed at `:80,:117`; re-imported by `server/__tests__/sessions-routes.test.ts:4`) | none (guarded by ADR-018 golden — add smokes, delete nothing) |

---

## Part A — MAIN repo: registry primitive + fragments + constants + generator

### Task A1: `SharedConstant` primitive + accessors (`types.py`)

**Files:**
- Modify: `javdb/storage/contract/types.py` (append `SharedConstant` + `as_str_set` + `as_str_list`)
- Test: `tests/unit/test_contract_types.py` (append cases)

- [ ] **Step 1: Append the failing tests** to `tests/unit/test_contract_types.py`

```python
from javdb.storage.contract.types import (
    SharedConstant,
    as_str_list,
    as_str_set,
)

_SET = SharedConstant(name="demo_set", kind="str_set", value=("a:b", "c:d"))
_LIST = SharedConstant(name="demo_list", kind="str_list", value=("X", "Y", "Z"))


def test_as_str_set_returns_frozenset_of_values():
    assert as_str_set(_SET) == frozenset({"a:b", "c:d"})


def test_as_str_list_returns_value_tuple_in_order():
    assert as_str_list(_LIST) == ("X", "Y", "Z")


def test_accessors_reject_wrong_kind():
    import pytest

    with pytest.raises(ValueError):
        as_str_set(_LIST)
    with pytest.raises(ValueError):
        as_str_list(_SET)
```

- [ ] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py -p no:cacheprovider -q`
Expected: FAIL — `ImportError: cannot import name 'SharedConstant'`.

- [ ] **Step 3: Implement in `javdb/storage/contract/types.py`** — append after `SqlFragment` (keep `Param`/`SqlFragment`/`normalize_sql`/`order_params` unchanged):

```python
@dataclass(frozen=True)
class SharedConstant:
    """A shared cross-backend data constant (ADR-055 D3).

    Authored once here; the TS mirror is emitted by the generator. ``value`` is
    an ORDERED tuple (determinism for codegen); ``str_set`` constants are wrapped
    in a frozenset for Python membership use, ``str_list`` keep tuple order.
    The TS export name is ``name.upper()``.
    """

    name: str                  # snake_case id, e.g. "valid_rule_modes"
    kind: str                  # "str_set" | "str_list"
    value: Tuple[str, ...]     # ordered string values
    doc: str = ""              # optional one-line provenance note


def as_str_set(const: SharedConstant) -> frozenset:
    """Return a ``str_set`` constant's values as a frozenset (membership use)."""
    if const.kind != "str_set":
        raise ValueError(f"as_str_set({const.name}): kind is {const.kind!r}, not 'str_set'")
    return frozenset(const.value)


def as_str_list(const: SharedConstant) -> Tuple[str, ...]:
    """Return a ``str_list`` constant's values as an ordered tuple."""
    if const.kind != "str_list":
        raise ValueError(f"as_str_list({const.name}): kind is {const.kind!r}, not 'str_list'")
    return tuple(const.value)
```

- [ ] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py -p no:cacheprovider -q`
Expected: PASS (existing 3 + new 3 = 6 passed).

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add javdb/storage/contract/types.py tests/unit/test_contract_types.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "feat(contract): ADR-055 SharedConstant primitive + str_set/str_list accessors"
```

---

### Task A2: ActorSubscription + system_state `SqlFragment`s (`fragments.py`)

**Files:**
- Modify: `javdb/storage/contract/fragments.py`
- Test: `tests/unit/test_contract_fragments.py` (append cases)

- [ ] **Step 1: Append the failing tests** to `tests/unit/test_contract_fragments.py`

```python
def test_actor_subscription_upsert_shape():
    f = fragments.ACTOR_SUBSCRIPTION_UPSERT
    assert f.name == "actor_subscription_upsert"
    assert f.db == "history"
    assert [p.name for p in f.params] == ["actor_href", "actor_name", "active"]
    norm = normalize_sql(f.sql)
    assert norm.startswith("INSERT INTO ActorSubscription")
    assert "ON CONFLICT(actor_href) DO UPDATE SET" in norm
    assert "active = excluded.active" in norm


def test_system_state_upsert_shape():
    f = fragments.SYSTEM_STATE_UPSERT
    assert f.name == "system_state_upsert"
    assert f.db == "operations"
    assert [p.name for p in f.params] == ["key", "value"]
    norm = normalize_sql(f.sql)
    assert norm.startswith("INSERT INTO system_state")
    assert "ON CONFLICT(key) DO UPDATE SET" in norm
    assert "datetime('now')" in norm
```

- [ ] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_fragments.py -p no:cacheprovider -q`
Expected: FAIL — `AttributeError: ... ACTOR_SUBSCRIPTION_UPSERT`.

- [ ] **Step 3: Implement in `javdb/storage/contract/fragments.py`** — add both fragments after `WATCH_INTENT_UPSERT` (SQL copied verbatim from `subscription_repo.py` / `system_state_repo.py`), and extend the `FRAGMENTS` tuple:

```python
ACTOR_SUBSCRIPTION_UPSERT = SqlFragment(
    name="actor_subscription_upsert",
    db="history",
    sql="""
    INSERT INTO ActorSubscription
        (actor_href, actor_name, active, created_at, updated_at)
    VALUES (?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(actor_href) DO UPDATE SET
        actor_name = excluded.actor_name,
        active     = excluded.active,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
""",
    params=(
        Param("actor_href", "str", "string"),
        Param("actor_name", "str | None", "string | null"),
        Param("active", "int", "number"),
    ),
)

SYSTEM_STATE_UPSERT = SqlFragment(
    name="system_state_upsert",
    db="operations",
    sql="""
    INSERT INTO system_state (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                    updated_at = datetime('now')
""",
    params=(
        Param("key", "str", "string"),
        Param("value", "str", "string"),
    ),
)

FRAGMENTS: tuple[SqlFragment, ...] = (
    WATCH_INTENT_UPSERT,
    ACTOR_SUBSCRIPTION_UPSERT,
    SYSTEM_STATE_UPSERT,
)
```

> Replace the existing `FRAGMENTS = (WATCH_INTENT_UPSERT,)` line with the three-entry tuple above. The generic `test_param_count_matches_placeholders` (already present) will validate `?` count == params for the new entries.

- [ ] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_fragments.py -p no:cacheprovider -q`
Expected: PASS (existing 4 + new 2 = 6 passed). The `test_sql_contract_freshness.py` is now intentionally STALE until Task A5 — do not run it yet.

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add javdb/storage/contract/fragments.py tests/unit/test_contract_fragments.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "feat(contract): ADR-055 ActorSubscription + system_state fragments"
```

---

### Task A3: content-filter + columns constants (`constants.py`) + `__init__` exports

**Files:**
- Create: `javdb/storage/contract/constants.py`
- Modify: `javdb/storage/contract/__init__.py`
- Test: `tests/unit/test_contract_constants.py`

- [ ] **Step 1: Write the failing test** `tests/unit/test_contract_constants.py`

```python
"""ADR-055: the shared-constant registry is well-formed and queryable."""
from javdb.storage.contract import constants
from javdb.storage.contract.types import SharedConstant, as_str_list, as_str_set


def test_registry_is_nonempty_tuple_of_constants():
    assert constants.CONSTANTS
    assert all(isinstance(c, SharedConstant) for c in constants.CONSTANTS)


def test_constant_names_unique():
    names = [c.name for c in constants.CONSTANTS]
    assert len(names) == len(set(names))


def test_valid_rule_modes_membership():
    s = as_str_set(constants.VALID_RULE_MODES)
    assert "actor:exclude" in s
    assert "gender:exclude_all_male" in s
    assert "release_date:after" in s
    assert len(s) == 13


def test_value_required_excludes_exclude_all_male():
    s = as_str_set(constants.VALUE_REQUIRED)
    assert "gender:exclude_all_male" not in s
    assert "gender:require_lead" in s
    assert len(s) == 12


def test_report_session_columns_projection():
    cols = as_str_list(constants.REPORT_SESSION_COLUMNS)
    assert ", ".join(cols) == (
        "Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
        "ReportType, ReportDate, FailureReason"
    )
```

- [ ] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_constants.py -p no:cacheprovider -q`
Expected: FAIL — `ModuleNotFoundError: javdb.storage.contract.constants`.

- [ ] **Step 3: Implement `javdb/storage/contract/constants.py`** (ordered to match the current TS Set insertion order)

```python
"""Shared data-constant registry (ADR-055). The single hand-edited source.

Each entry is mirrored to the TS Worker by apps/cli/ops/dump_sql_contract.py
(emitted into the same sql-contract.gen.ts as the SQL fragments). Add new static
cross-backend allow-lists / column projections / enums here, never by hand in the
other repo. ``value`` order is significant only for codegen determinism.
"""
from __future__ import annotations

from javdb.storage.contract.types import SharedConstant

# content-filter (dimension:mode) allow-list (ADR-040 WS4a). Encoded "dim:mode"
# so Python and TS share one representation; Python consumers use string keys.
VALID_RULE_MODES = SharedConstant(
    name="valid_rule_modes",
    kind="str_set",
    value=(
        "actor:exclude",
        "tag:exclude",
        "tag:include",
        "gender:require_lead",
        "gender:exclude_all_male",
        "age:min_age",
        "age:max_age",
        "actor:regex_exclude",
        "actor:regex_include",
        "tag:regex_exclude",
        "tag:regex_include",
        "release_date:before",
        "release_date:after",
    ),
    doc="content-filter legal (dimension:mode) pairs",
)

# Subset of VALID_RULE_MODES whose rules require a non-empty value
# (excludes gender:exclude_all_male, which takes no value).
VALUE_REQUIRED = SharedConstant(
    name="value_required",
    kind="str_set",
    value=(
        "actor:exclude",
        "tag:exclude",
        "tag:include",
        "gender:require_lead",
        "age:min_age",
        "age:max_age",
        "actor:regex_exclude",
        "actor:regex_include",
        "tag:regex_exclude",
        "tag:regex_include",
        "release_date:before",
        "release_date:after",
    ),
    doc="content-filter (dimension:mode) pairs that require a value",
)

# ReportSessions full-row projection shared by the two backends' session list +
# detail reads. The ASSEMBLED query is pinned byte-for-byte by the ADR-018 query
# Contract Golden, so the column order/spelling here MUST NOT change.
REPORT_SESSION_COLUMNS = SharedConstant(
    name="report_session_columns",
    kind="str_list",
    value=(
        "Id",
        "Status",
        "WriteMode",
        "RunId",
        "RunAttempt",
        "DateTimeCreated",
        "ReportType",
        "ReportDate",
        "FailureReason",
    ),
    doc="ReportSessions full-row SELECT projection (ADR-018 golden-pinned)",
)

CONSTANTS: tuple[SharedConstant, ...] = (
    VALID_RULE_MODES,
    VALUE_REQUIRED,
    REPORT_SESSION_COLUMNS,
)
```

- [ ] **Step 4: Extend `javdb/storage/contract/__init__.py`** — surface the new symbols (replace its body):

```python
"""ADR-055 contract registry: the single source for static cross-backend SQL + constants."""
from javdb.storage.contract import constants, fragments
from javdb.storage.contract.types import (
    Param,
    SharedConstant,
    SqlFragment,
    as_str_list,
    as_str_set,
    normalize_sql,
    order_params,
)

__all__ = [
    "Param",
    "SqlFragment",
    "SharedConstant",
    "normalize_sql",
    "order_params",
    "as_str_set",
    "as_str_list",
    "fragments",
    "constants",
]
```

- [ ] **Step 5: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_constants.py tests/unit/test_contract_types.py tests/unit/test_contract_fragments.py -p no:cacheprovider -q`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add javdb/storage/contract/constants.py javdb/storage/contract/__init__.py tests/unit/test_contract_constants.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "feat(contract): ADR-055 shared-constant registry (allow-lists + ReportSessions columns)"
```

---

### Task A4: Extend the generator to emit constants (`dump_sql_contract.py`)

**Files:**
- Modify: `apps/cli/ops/dump_sql_contract.py`
- Test: `tests/unit/test_dump_sql_contract.py` (append cases)

- [ ] **Step 1: Append the failing tests** to `tests/unit/test_dump_sql_contract.py`

```python
def test_render_emits_new_fragment_helpers():
    out = render()
    assert "export const ACTOR_SUBSCRIPTION_UPSERT_SQL =" in out
    assert "export function prepareActorSubscriptionUpsert(" in out
    assert "p.actorHref, p.actorName, p.active" in out
    assert "export const SYSTEM_STATE_UPSERT_SQL =" in out
    assert "export function prepareSystemStateUpsert(" in out
    assert "p.key, p.value" in out


def test_render_emits_str_set_constant():
    out = render()
    assert "export const VALID_RULE_MODES: ReadonlySet<string> = new Set([" in out
    assert '"gender:exclude_all_male"' in out
    assert "export const VALUE_REQUIRED: ReadonlySet<string> = new Set([" in out


def test_render_emits_str_list_constant():
    out = render()
    assert "export const REPORT_SESSION_COLUMNS: readonly string[] = [" in out
    assert '"FailureReason"' in out
```

- [ ] **Step 2: Run it — expect failure**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_dump_sql_contract.py -p no:cacheprovider -q`
Expected: FAIL — constants not rendered (the new-fragment asserts may pass already since A2 added them; the constant asserts fail).

- [ ] **Step 3: Implement constant rendering in `apps/cli/ops/dump_sql_contract.py`**

Add the `json` import at the top (after `import hashlib`):

```python
import json
```

Add the constants import next to the fragments import:

```python
from javdb.storage.contract import constants as _const  # noqa: E402
from javdb.storage.contract import fragments as _frag  # noqa: E402
from javdb.storage.contract.types import SharedConstant, SqlFragment, normalize_sql  # noqa: E402
```

Add a renderer for constants (after `_render_fragment`):

```python
def _ts_string_array(values: tuple[str, ...]) -> str:
    """Render an ordered tuple of strings as a multi-line TS array literal."""
    inner = "".join(f"\n  {json.dumps(v)}," for v in values)
    return f"[{inner}\n]"


def _render_constant(c: SharedConstant) -> str:
    name = c.name.upper()
    if c.kind == "str_set":
        return f"export const {name}: ReadonlySet<string> = new Set({_ts_string_array(c.value)});\n"
    if c.kind == "str_list":
        return f"export const {name}: readonly string[] = {_ts_string_array(c.value)};\n"
    raise ValueError(f"_render_constant({c.name}): unknown kind {c.kind!r}")
```

Change `render()` to include constants in the body (so the version hash covers both):

```python
def render() -> str:
    blocks = [_render_fragment(f) for f in _frag.FRAGMENTS]
    blocks += [_render_constant(c) for c in _const.CONSTANTS]
    body = "\n".join(blocks)
    version = hashlib.sha256(body.encode()).hexdigest()[:16]
    header = (
        "// AUTO-GENERATED from javdb/storage/contract — DO NOT EDIT.\n"
        "// Source of truth: ADR-055. Regenerate: "
        "python3 -m apps.cli.ops.dump_sql_contract\n"
        f"// version: {version}\n"
        "/* eslint-disable */\n\n"
    )
    return header + body
```

- [ ] **Step 4: Run it — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_dump_sql_contract.py -p no:cacheprovider -q`
Expected: PASS (existing determinism/escape tests + new fragment/constant tests).

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add apps/cli/ops/dump_sql_contract.py tests/unit/test_dump_sql_contract.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "feat(contract): ADR-055 generator emits shared constants into sql-contract.gen.ts"
```

---

### Task A5: Regenerate the committed artifact + freshness green

**Files:**
- Modify (generated): `docs/api/contract/sql-contract.gen.ts`
- Test: `tests/unit/test_sql_contract_freshness.py` (no change — already compares committed == render())

- [ ] **Step 1: Regenerate the artifact**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 && PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_sql_contract`
Expected: `wrote …/docs/api/contract/sql-contract.gen.ts`.

- [ ] **Step 2: Eyeball the artifact** — confirm it now contains, in order: WatchIntent, ActorSubscription, system_state fragments (const + `prepare…` helper each), then `VALID_RULE_MODES`, `VALUE_REQUIRED` (`new Set([...])`), `REPORT_SESSION_COLUMNS` (`readonly string[]`), and a new `// version:` hash.

Run: `grep -n "export const\|export function" /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/docs/api/contract/sql-contract.gen.ts`

- [ ] **Step 3: Run the freshness test — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_sql_contract_freshness.py tests/unit/test_dump_sql_contract.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add docs/api/contract/sql-contract.gen.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "feat(contract): ADR-055 regenerate sql-contract.gen.ts with P2 fragments + constants"
```

---

## Part B — MAIN repo: migrate Python consumers; delete parity tests; add smokes

### Task B1: ActorSubscription consumer → registry; delete parity test; behavioral smoke

**Files:**
- Modify: `javdb/storage/repos/subscription_repo.py:10-49`
- Delete: `tests/unit/test_actor_subscription_upsert_parity.py`
- Test: `tests/unit/test_actor_subscription_upsert_behavior.py` (new; ADR-055 D8)

- [ ] **Step 1: Add the behavioral smoke** `tests/unit/test_actor_subscription_upsert_behavior.py`

```python
"""ADR-055 D8: behavioral smoke for the ActorSubscription upsert (column->value)."""
import pathlib
import sqlite3

import pytest

from javdb.storage import db as _db
from javdb.storage.repos.subscription_repo import ActorSubscriptionRepo

_DDL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    return ActorSubscriptionRepo(db_path=path)


def test_upsert_maps_each_column_to_the_right_value(repo):
    row = repo.upsert(actor_href="/actors/EvkJ", actor_name="Alice", active=1)
    assert row["actor_href"] == "/actors/EvkJ"
    assert row["actor_name"] == "Alice"
    assert row["active"] == 1


def test_upsert_refreshes_name_and_active_on_conflict(repo):
    repo.upsert(actor_href="/actors/EvkJ", actor_name="Alice", active=1)
    row = repo.upsert(actor_href="/actors/EvkJ", actor_name="Alicia", active=0)
    assert row["actor_name"] == "Alicia"
    assert row["active"] == 0
```

- [ ] **Step 2: Run it against CURRENT code — expect pass** (proves the smoke is valid before refactor)

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_actor_subscription_upsert_behavior.py -p no:cacheprovider -q`
Expected: PASS (2 passed).

- [ ] **Step 3: Migrate `subscription_repo.py` to the registry**

Replace the top-of-file SQL const block (the `# Byte-mirrored …` comment through the closing `"""` of `ACTOR_SUBSCRIPTION_UPSERT_SQL`, lines 10-27) with:

```python
from javdb.storage.contract import fragments, order_params

# Single source of truth: the ADR-055 contract registry. Re-exported for any
# back-compat importers; the SQL itself lives only in javdb/storage/contract.
ACTOR_SUBSCRIPTION_UPSERT_SQL = fragments.ACTOR_SUBSCRIPTION_UPSERT.sql
```

(Keep `from javdb.storage import db as _db` and `from javdb.storage.db import get_db`.)

Then in `ActorSubscriptionRepo.upsert`, replace the execute call:

```python
            conn.execute(
                ACTOR_SUBSCRIPTION_UPSERT_SQL, (actor_href, actor_name, active)
            )
```

with:

```python
            conn.execute(
                fragments.ACTOR_SUBSCRIPTION_UPSERT.sql,
                order_params(
                    fragments.ACTOR_SUBSCRIPTION_UPSERT,
                    actor_href=actor_href,
                    actor_name=actor_name,
                    active=active,
                ),
            )
```

- [ ] **Step 4: Confirm no other importer of the const, then delete the parity test**

Run: `grep -rn "ACTOR_SUBSCRIPTION_UPSERT_SQL" /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/javdb /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/apps`
Expected: only the re-export in `subscription_repo.py` (the parity test is the only other reader and is being deleted).

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 rm tests/unit/test_actor_subscription_upsert_parity.py
```

- [ ] **Step 5: Run the subscription suite — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_actor_subscription_upsert_behavior.py tests/unit/test_subscription_repo.py tests/unit/test_contract_fragments.py -p no:cacheprovider -q`
Expected: PASS (if `test_subscription_repo.py` does not exist, drop it from the list; the behavior + fragments tests must pass).

- [ ] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add javdb/storage/repos/subscription_repo.py tests/unit/test_actor_subscription_upsert_behavior.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add -u tests/unit/test_actor_subscription_upsert_parity.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "refactor(subscriptions): source ActorSubscription upsert from ADR-055 registry; drop parity test"
```

---

### Task B2: system_state consumer → registry; behavioral smoke

**Files:**
- Modify: `javdb/storage/repos/system_state_repo.py:39-48`
- Test: `tests/unit/test_system_state_upsert_behavior.py` (new; ADR-055 D8)

- [ ] **Step 1: Add the behavioral smoke** `tests/unit/test_system_state_upsert_behavior.py`

```python
"""ADR-055 D8: behavioral smoke for the system_state upsert (insert + conflict)."""
import sqlite3

import pytest

from javdb.storage.repos.system_state_repo import SystemStateRepo

_DDL = (
    "CREATE TABLE system_state ("
    "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
    "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    yield c
    c.close()


def test_put_inserts_then_updates_on_conflict(conn):
    repo = SystemStateRepo(conn)
    repo.put("onboarded", "false")
    assert repo.get("onboarded") == "false"
    repo.put("onboarded", "true")
    assert repo.get("onboarded") == "true"


def test_put_binds_key_and_value_in_order(conn):
    repo = SystemStateRepo(conn)
    repo.put("alpha", "A")
    repo.put("beta", "B")
    assert repo.get("alpha") == "A"
    assert repo.get("beta") == "B"
```

- [ ] **Step 2: Run it against CURRENT code — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_system_state_upsert_behavior.py -p no:cacheprovider -q`
Expected: PASS (2 passed).

- [ ] **Step 3: Migrate `system_state_repo.py` to the registry**

Add the import at the top of the file (after the existing `import json` / `from typing import Any`):

```python
from javdb.storage.contract import fragments, order_params
```

Replace the body of `SystemStateRepo.put`:

```python
    def put(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO system_state (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                            updated_at = datetime('now')
            """,
            (key, value),
        )
```

with:

```python
    def put(self, key: str, value: str) -> None:
        # Single source of truth: the ADR-055 contract registry (mirrored to the
        # TS Worker's prepareSystemStateUpsert in sql-contract.gen.ts).
        self._conn.execute(
            fragments.SYSTEM_STATE_UPSERT.sql,
            order_params(fragments.SYSTEM_STATE_UPSERT, key=key, value=value),
        )
```

- [ ] **Step 4: Run the system_state suite — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_system_state_upsert_behavior.py tests/unit/test_system_state_repo.py -p no:cacheprovider -q`
Expected: PASS (existing repo tests unchanged + new smoke).

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add javdb/storage/repos/system_state_repo.py tests/unit/test_system_state_upsert_behavior.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "refactor(storage): source system_state upsert from ADR-055 registry"
```

---

### Task B3: content-filter consumers → registry string keys; delete parity test; constants smoke

**Files:**
- Modify: `apps/cli/ops/content_filter.py:21-49,88-98,162` (constants + tuple→string keys)
- Modify: `apps/api/routers/content_filter.py:4-7,84` (docstring + tuple→string key)
- Delete: `tests/unit/test_content_filter_modes_parity.py`
- Test: `tests/unit/test_content_filter_constants_contract.py` (new)

- [ ] **Step 1: Write the contract smoke** `tests/unit/test_content_filter_constants_contract.py`

```python
"""ADR-055 D8: content-filter allow-lists come from the registry + wire into the router."""
from apps.api.routers import content_filter as cf_router
from apps.cli.ops.content_filter import VALID_RULE_MODES, VALUE_REQUIRED


def test_allow_lists_use_encoded_string_keys():
    assert "actor:exclude" in VALID_RULE_MODES
    assert "gender:exclude_all_male" in VALID_RULE_MODES
    assert "gender:exclude_all_male" not in VALUE_REQUIRED
    assert "release_date:after" in VALUE_REQUIRED


def test_router_shares_the_same_allow_list_objects():
    # No second copy: the router consumes the CLI module's registry-derived sets.
    assert cf_router.VALID_RULE_MODES is VALID_RULE_MODES
    assert cf_router.VALUE_REQUIRED is VALUE_REQUIRED
```

- [ ] **Step 2: Run it — expect failure** (current sets are tuples, not strings)

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_content_filter_constants_contract.py -p no:cacheprovider -q`
Expected: FAIL — `"actor:exclude" in VALID_RULE_MODES` is False (still `("actor","exclude")` tuples).

- [ ] **Step 3: Migrate `apps/cli/ops/content_filter.py`**

Replace the `VALID_RULE_MODES = { … }` and `VALUE_REQUIRED = { … }` literal blocks (lines 21-49) with registry-derived frozensets:

```python
from javdb.storage.contract import constants as _contract_constants
from javdb.storage.contract.types import as_str_set

# (dimension:mode) allow-list — single source of truth is the ADR-055 contract
# registry (javdb/storage/contract/constants.py), mirrored to the TS Worker.
# Encoded as "dim:mode" strings so both backends share one representation.
VALID_RULE_MODES = as_str_set(_contract_constants.VALID_RULE_MODES)
VALUE_REQUIRED = as_str_set(_contract_constants.VALUE_REQUIRED)
```

(Put the two `import` lines with the other top-of-file imports; keep `DIMENSIONS`, `MODES`, `GENDER_VALUES` as-is.)

In `validate_rule_value`, change the key construction + comparisons (lines 88, 91, 98):

```python
    value = (value or "").strip()
    rule_key = f"{dimension}:{mode}"
    if rule_key in VALUE_REQUIRED and not value:
        raise ValueError(f"{dimension} {mode} rules require a non-empty value")
    if rule_key == "gender:require_lead":
        normalized = value.casefold()
        if normalized not in GENDER_VALUES:
            raise ValueError(
                f"gender require_lead rules require a value of {GENDER_VALUES}"
            )
        return normalized
    if rule_key == "gender:exclude_all_male":
        if value:
            raise ValueError("gender exclude_all_male rules do not accept a value")
        return ""
```

In `_validate_add`, change the key construction (line 162):

```python
    rule_key = f"{args.dimension}:{args.mode}"
    if rule_key not in VALID_RULE_MODES:
```

- [ ] **Step 4: Migrate the router `apps/api/routers/content_filter.py`**

Change the tuple key (line 84) to a string key:

```python
    rule_key = f"{body.dimension}:{body.mode}"
```

Update the module docstring (lines 4-7) — replace the sentence referencing the deleted parity test:

```python
    mode) allow-list is imported from apps.cli.ops.content_filter (the single
    source of truth — itself sourced from the ADR-055 contract registry and
    mirrored to server/routes/content-filter.ts via sql-contract.gen.ts).
```

- [ ] **Step 5: Run the contract smoke — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_content_filter_constants_contract.py -p no:cacheprovider -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Delete the parity test + run the content-filter behavioral suites**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 rm tests/unit/test_content_filter_modes_parity.py
```

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_content_filter_router.py tests/unit/test_content_filter_constants_contract.py -p no:cacheprovider -q`
Expected: PASS — the existing router gating tests (invalid pair → 422, value-required → 422, regex/release_date accepted) still pass with string keys, confirming the migration preserved behavior. (If 401-auth env failures appear in `test_content_filter_router.py`, they are part of the ≈57 baseline; the gating assertions themselves must pass.)

- [ ] **Step 7: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add apps/cli/ops/content_filter.py apps/api/routers/content_filter.py tests/unit/test_content_filter_constants_contract.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add -u tests/unit/test_content_filter_modes_parity.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "refactor(content-filter): source allow-lists from ADR-055 registry (string keys); drop parity test"
```

---

### Task B4: ReportSessions columns → registry; ADR-018 golden no-op; columns smoke

**Files:**
- Modify: `javdb/storage/repos/sessions_repo.py:62-66,119-121` (+ module-level join constant)
- Test: `tests/unit/test_report_session_columns_contract.py` (new)
- Verify: `docs/api/contract/query-builders.golden.json` regen is a **no-op**

- [ ] **Step 1: Write the columns smoke** `tests/unit/test_report_session_columns_contract.py`

```python
"""ADR-055: the ReportSessions projection is sourced from the registry, byte-stable."""
from javdb.storage.repos import sessions_repo
from javdb.storage.repos.sessions_repo import _build_session_query

_EXPECTED = (
    "Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
    "ReportType, ReportDate, FailureReason"
)


def test_session_columns_join_matches_canonical():
    assert sessions_repo._SESSION_COLUMNS == _EXPECTED


def test_build_session_query_projection_unchanged():
    sql, _ = _build_session_query(state=None, cursor=None, limit=50)
    assert sql == f"SELECT {_EXPECTED} FROM ReportSessions ORDER BY Id DESC LIMIT ?"
```

- [ ] **Step 2: Run it — expect failure** (`_SESSION_COLUMNS` does not exist yet)

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_report_session_columns_contract.py -p no:cacheprovider -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_SESSION_COLUMNS'`.

- [ ] **Step 3: Migrate `sessions_repo.py`**

Add a module-level join constant after the imports (after `from dataclasses import dataclass`):

```python
from javdb.storage.contract import constants as _contract_constants

# ReportSessions full-row projection — single source of truth is the ADR-055
# registry. The ASSEMBLED query is pinned byte-for-byte by the ADR-018 query
# Contract Golden, so changing the registry value will (correctly) red that golden.
_SESSION_COLUMNS = ", ".join(_contract_constants.REPORT_SESSION_COLUMNS.value)
```

In `_build_session_query`, replace the inline `sql = ( … )` projection (lines 62-66) with:

```python
    sql = f"SELECT {_SESSION_COLUMNS} FROM ReportSessions"
```

In `SessionsRepo.get`, replace the inline projection (lines 119-121) with:

```python
        row = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM ReportSessions WHERE Id = ?",
            (session_id,),
        ).fetchone()
```

- [ ] **Step 4: Run the columns smoke + the ADR-018 golden conformance — expect pass**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_report_session_columns_contract.py tests/unit/test_query_contract_golden.py -p no:cacheprovider -q`
Expected: PASS — the golden conformance passes because the assembled SQL is byte-identical.

- [ ] **Step 5: Regenerate the ADR-018 query golden and confirm a NO-OP diff** (per the query-golden gotcha: editing a builder file requires a regen; here it must produce zero change)

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 && PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_query_contract && git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 diff --quiet docs/api/contract/query-builders.golden.json && echo "QUERY GOLDEN UNCHANGED ✓"`
Expected: `QUERY GOLDEN UNCHANGED ✓` (no diff). If the diff is non-empty, the projection drifted — fix the registry value before continuing.

- [ ] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add javdb/storage/repos/sessions_repo.py tests/unit/test_report_session_columns_contract.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "refactor(sessions): source ReportSessions projection from ADR-055 registry (golden no-op)"
```

---

## Part C — WEB repo: branch, vendor, migrate consumers, delete parity tests, smokes

### Task C0: WEB branch + re-vendor the generated artifact

**Files:**
- Modify (vendored): `server/contract/sql-contract.gen.ts`

- [ ] **Step 1: Branch off WEB main**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web checkout -b claude/adr055-p2-static-mirrors
```

- [ ] **Step 2: Re-vendor from the MAIN worktree's regenerated artifact**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && SQL_CONTRACT_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract`
Expected: `[fetch-sql-contract] wrote …/server/contract/sql-contract.gen.ts`.

- [ ] **Step 3: Confirm byte-parity with MAIN**

Run: `diff /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/docs/api/contract/sql-contract.gen.ts /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/server/contract/sql-contract.gen.ts && echo "VENDORED == MAIN ✓"`
Expected: `VENDORED == MAIN ✓`.

- [ ] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add server/contract/sql-contract.gen.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "chore(server): re-vendor ADR-055 sql-contract.gen.ts (P2 fragments + constants)"
```

---

### Task C1: subscription-service.ts → generated helper; delete parity test; conformance smoke

**Files:**
- Modify: `server/services/subscription-service.ts:24-49`
- Delete: `server/__tests__/actor-subscription-upsert-parity.test.ts`
- Test: `server/__tests__/actor-subscription-contract.test.ts` (new)

- [ ] **Step 1: Migrate `subscription-service.ts`**

Delete the hand-written `ACTOR_SUBSCRIPTION_UPSERT_SQL` const block (the `// Byte-mirrored …` comment through the closing `` `; `` — lines 24-36). Add an import near the top (after the leading comment, before `export interface ActorSubscriptionRow`):

```typescript
import { prepareActorSubscriptionUpsert } from "../contract/sql-contract.gen";
```

Replace the body of `upsertSubscription`:

```typescript
  await db
    .prepare(ACTOR_SUBSCRIPTION_UPSERT_SQL)
    .bind(actorHref, actorName, active)
    .run();
```

with:

```typescript
  await prepareActorSubscriptionUpsert(db, { actorHref, actorName, active }).run();
```

- [ ] **Step 2: Confirm no other importer, then delete the parity test**

Run: `grep -rn "ACTOR_SUBSCRIPTION_UPSERT_SQL" /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/server`
Expected: only `server/__tests__/actor-subscription-upsert-parity.test.ts` (being deleted).

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web rm server/__tests__/actor-subscription-upsert-parity.test.ts
```

- [ ] **Step 3: Add the conformance smoke** `server/__tests__/actor-subscription-contract.test.ts` (models the `cloudflare:test` D1 seeding in `subscription-routes.test.ts`)

```typescript
import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { prepareActorSubscriptionUpsert } from "../contract/sql-contract.gen";

async function seed() {
  await env.HISTORY_DB.prepare(
    `CREATE TABLE IF NOT EXISTS ActorSubscription (
      actor_href TEXT PRIMARY KEY,
      actor_name TEXT,
      active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
      last_seen_href TEXT,
      last_checked_at TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )`,
  ).run();
}

describe("ADR-055 generated ActorSubscription upsert", () => {
  beforeAll(seed);
  beforeEach(async () => {
    await env.HISTORY_DB.prepare("DELETE FROM ActorSubscription").run();
  });

  it("maps each column to the right value (bind order from the generator)", async () => {
    await prepareActorSubscriptionUpsert(env.HISTORY_DB, {
      actorHref: "/actors/EvkJ",
      actorName: "Alice",
      active: 1,
    }).run();
    const row = await env.HISTORY_DB.prepare(
      "SELECT * FROM ActorSubscription WHERE actor_href = ?",
    )
      .bind("/actors/EvkJ")
      .first<{ actor_href: string; actor_name: string | null; active: number }>();
    expect(row).toMatchObject({
      actor_href: "/actors/EvkJ",
      actor_name: "Alice",
      active: 1,
    });
  });

  it("refreshes name + active on conflict, preserves created_at", async () => {
    await prepareActorSubscriptionUpsert(env.HISTORY_DB, {
      actorHref: "/actors/EvkJ", actorName: "Alice", active: 1,
    }).run();
    const before = await env.HISTORY_DB.prepare(
      "SELECT created_at FROM ActorSubscription WHERE actor_href = ?",
    ).bind("/actors/EvkJ").first<{ created_at: string }>();
    await prepareActorSubscriptionUpsert(env.HISTORY_DB, {
      actorHref: "/actors/EvkJ", actorName: "Alicia", active: 0,
    }).run();
    const after = await env.HISTORY_DB.prepare(
      "SELECT actor_name, active, created_at FROM ActorSubscription WHERE actor_href = ?",
    ).bind("/actors/EvkJ").first<{ actor_name: string; active: number; created_at: string }>();
    expect(after).toMatchObject({ actor_name: "Alicia", active: 0 });
    expect(after!.created_at).toBe(before!.created_at);
  });
});
```

- [ ] **Step 4: Type-check + run the server suite**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server`
Expected: typecheck clean; `actor-subscription-contract.test.ts` (2) + existing `subscription-routes.test.ts` pass; the deleted parity spec is no longer collected.

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add server/services/subscription-service.ts server/__tests__/actor-subscription-contract.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add -u server/__tests__/actor-subscription-upsert-parity.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "refactor(server): consume ADR-055 generated ActorSubscription upsert; drop parity test"
```

---

### Task C2: system-state-store.ts → generated helper; conformance smoke

**Files:**
- Modify: `server/services/system-state-store.ts:1-25`
- Test: `server/__tests__/system-state-contract.test.ts` (new)

- [ ] **Step 1: Migrate `system-state-store.ts`** — replace the whole file body with the generated-helper delegate (keep the exported function signature so all callers — onboarding/system-state routes + `system-state-store.test.ts` — are unchanged):

```typescript
// Shared UPSERT for the `system_state` key/value table.
//
// The SQL + bind order are sourced from the ADR-055 contract registry
// (javdb/storage/contract) and emitted into server/contract/sql-contract.gen.ts;
// this wrapper preserves the (db, key, value) call sites across the onboarding
// and system-state routes.
import { prepareSystemStateUpsert } from "../contract/sql-contract.gen";

export async function upsertSystemState(
  db: D1Database,
  key: string,
  value: string,
): Promise<void> {
  await prepareSystemStateUpsert(db, { key, value }).run();
}
```

- [ ] **Step 2: Add the conformance smoke** `server/__tests__/system-state-contract.test.ts`

```typescript
import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { prepareSystemStateUpsert } from "../contract/sql-contract.gen";

async function seed() {
  await env.OPERATIONS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS system_state (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )`,
  ).run();
}

describe("ADR-055 generated system_state upsert", () => {
  beforeAll(seed);
  beforeEach(async () => {
    await env.OPERATIONS_DB.prepare("DELETE FROM system_state").run();
  });

  it("inserts then updates value on conflict (key bind order)", async () => {
    await prepareSystemStateUpsert(env.OPERATIONS_DB, { key: "onboarded", value: "false" }).run();
    await prepareSystemStateUpsert(env.OPERATIONS_DB, { key: "onboarded", value: "true" }).run();
    const row = await env.OPERATIONS_DB.prepare(
      "SELECT value FROM system_state WHERE key = ?",
    ).bind("onboarded").first<{ value: string }>();
    expect(row).toEqual({ value: "true" });
  });
});
```

- [ ] **Step 3: Type-check + run the server suite**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server`
Expected: typecheck clean; `system-state-contract.test.ts` (1) + existing `system-state-store.test.ts` + onboarding/system-state route suites pass.

- [ ] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add server/services/system-state-store.ts server/__tests__/system-state-contract.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "refactor(server): consume ADR-055 generated system_state upsert"
```

---

### Task C3: content-filter.ts route → generated Sets; delete parity test; smoke

**Files:**
- Modify: `server/routes/content-filter.ts:17-50`
- Delete: `server/__tests__/content-filter-modes-parity.test.ts`
- Test: `server/__tests__/content-filter-contract.test.ts` (new)

- [ ] **Step 1: Migrate `content-filter.ts`** — delete the hand-written `VALID_RULE_MODES` / `VALUE_REQUIRED` `new Set([...])` blocks and their `// Hand-mirrored …` comment (lines 17-50). Add an import to the existing import group at the top:

```typescript
import { VALID_RULE_MODES, VALUE_REQUIRED } from "../contract/sql-contract.gen";
```

(The `.has(key)` consumers at the former lines 118 & 121 are unchanged — the generated constants are `ReadonlySet<string>`.)

- [ ] **Step 2: Confirm no other importer, then delete the parity test**

Run: `grep -rn "from \"../routes/content-filter\"\|VALID_RULE_MODES\|VALUE_REQUIRED" /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/server | grep -v "sql-contract.gen"`
Expected: the route's own usage + the parity test (being deleted). If any OTHER module imports the Sets from `../routes/content-filter`, re-export them there: `export { VALID_RULE_MODES, VALUE_REQUIRED } from "../contract/sql-contract.gen";`.

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web rm server/__tests__/content-filter-modes-parity.test.ts
```

- [ ] **Step 3: Add the smoke** `server/__tests__/content-filter-contract.test.ts`

```typescript
import { describe, it, expect } from "vitest";
import { VALID_RULE_MODES, VALUE_REQUIRED } from "../contract/sql-contract.gen";

describe("ADR-055 generated content-filter allow-lists", () => {
  it("VALID_RULE_MODES gates representative pairs", () => {
    expect(VALID_RULE_MODES.has("actor:exclude")).toBe(true);
    expect(VALID_RULE_MODES.has("gender:exclude_all_male")).toBe(true);
    expect(VALID_RULE_MODES.has("actor:require_lead")).toBe(false);
    expect(VALID_RULE_MODES.size).toBe(13);
  });

  it("VALUE_REQUIRED excludes the no-value pair", () => {
    expect(VALUE_REQUIRED.has("gender:exclude_all_male")).toBe(false);
    expect(VALUE_REQUIRED.has("gender:require_lead")).toBe(true);
    expect(VALUE_REQUIRED.size).toBe(12);
  });
});
```

- [ ] **Step 4: Type-check + run the server suite**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server`
Expected: typecheck clean; `content-filter-contract.test.ts` (2) + existing `content-filter-routes.test.ts` (invalid pair → 422, regex/release_date accepted) pass; deleted parity spec no longer collected.

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add server/routes/content-filter.ts server/__tests__/content-filter-contract.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add -u server/__tests__/content-filter-modes-parity.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "refactor(server): consume ADR-055 generated content-filter allow-lists; drop parity test"
```

---

### Task C4: sessions.ts → generated columns array; update projection test; golden conformance

**Files:**
- Modify: `server/routes/sessions.ts:27-35,80,117`
- Modify: `server/__tests__/sessions-routes.test.ts:4,42-53` (string → `.join(", ")`)

- [ ] **Step 1: Migrate `server/routes/sessions.ts`**

Delete the local `REPORT_SESSION_COLUMNS` const + its ADR-018 comment (lines 27-35). Add, near the top imports, an import that is also re-exported (so `sessions-routes.test.ts` importing `REPORT_SESSION_COLUMNS` from `../routes/sessions` keeps working):

```typescript
import { REPORT_SESSION_COLUMNS } from "../contract/sql-contract.gen";
export { REPORT_SESSION_COLUMNS };
```

Update `buildSessionQuery` (former line 80) — join the array:

```typescript
  const sql =
    "SELECT " +
    REPORT_SESSION_COLUMNS.join(", ") +
    " FROM ReportSessions" +
    where +
    " ORDER BY Id DESC LIMIT ?";
```

Update the `GET /:session_id` handler (former line 117):

```typescript
  const session = await c.env.REPORTS_DB
    .prepare(`SELECT ${REPORT_SESSION_COLUMNS.join(", ")} FROM ReportSessions WHERE Id = ?`)
    .bind(sessionId)
    .first<SessionRow>();
```

- [ ] **Step 2: Update the projection test** `server/__tests__/sessions-routes.test.ts` (the `"REPORT_SESSION_COLUMNS shared projection"` describe block) — the constant is now an array, so assert its join:

Change the `.toBe(...)` assertion (around line 44):

```typescript
    expect(REPORT_SESSION_COLUMNS.join(", ")).toBe(
      "Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, ReportType, ReportDate, FailureReason",
    );
```

Change the interpolation assertion (around line 52) so the expected SQL is byte-identical:

```typescript
      `SELECT ${REPORT_SESSION_COLUMNS.join(", ")} FROM ReportSessions ORDER BY Id DESC LIMIT ?`,
```

> Read the exact current lines first (`sed -n '40,55p' server/__tests__/sessions-routes.test.ts`) and adjust only those two assertions; leave the `buildSessionQuery` output assertion (which compares against the assembled SQL) intact — it stays green because the assembled SQL is unchanged.

- [ ] **Step 3: Type-check + run the server suite (incl. the ADR-018 query-contract conformance)**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server`
Expected: typecheck clean; `sessions-routes.test.ts` (projection + builder) and `query-contract.test.ts` (the ADR-018 golden conformance — assembled `buildSessionQuery` SQL unchanged) both pass.

- [ ] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web add server/routes/sessions.ts server/__tests__/sessions-routes.test.ts
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web commit -m "refactor(server): source ReportSessions projection from ADR-055 generated constant"
```

---

## Part D — Docs + cross-repo verification

### Task D1: ADR-055 roadmap + Status Log + CONTEXT.md term + IMP status

**Files:**
- Modify: `docs/design/ADR-055-Dual-Backend-Contract-Single-Source/ADR-055-dual-backend-contract-single-source.md` (Roadmap Phase 2 row → delivered; Status Log)
- Modify: `docs/design/ADR-055-Dual-Backend-Contract-Single-Source/ADR-055-dual-backend-contract-single-source.zh.md` (same, translated)
- Modify: `CONTEXT.md` (add a **Shared constant** domain term)
- Modify: this file (`IMP-ADR055-02-migrate-static-mirrors.md`) — Status → Completed + Completion Evidence

- [ ] **Step 1: ADR-055 `.md`** — update the Roadmap Phase 2 row (drop the "written against real shapes when targets land" qualifier; mark the IMP link live) and append a Status Log entry:

```markdown
- 2026-06-17: Phase 2 delivered ([IMP-ADR055-02](IMP-ADR055-02-migrate-static-mirrors.md)) — wired both backends' consumers onto the ADR-055 registry for the remaining static mirror points (ActorSubscription + system_state upserts, content-filter allow-lists VALID_RULE_MODES/VALUE_REQUIRED, ReportSessions column projection), removed the hand-CANONICAL parity tests (ActorSubscription, content-filter), and added behavioral/conformance smokes each side. The SharedConstant primitive and these registry entries had independently landed on main alongside #220 (incident alerting); IMP-ADR055-02 reconciled to that implementation. ADR-018 query golden unchanged (byte-identical assembled SQL).
```

- [ ] **Step 2: ADR-055 `.zh.md`** — mirror the same Roadmap edit + Status Log entry, translated:

```markdown
- 2026-06-17：Phase 2 已交付（[IMP-ADR055-02](IMP-ADR055-02-migrate-static-mirrors.md)）—— 将两端消费者接入 ADR-055 registry，覆盖其余静态镜像点（ActorSubscription 与 system_state upsert、content-filter 允许表 VALID_RULE_MODES/VALUE_REQUIRED、ReportSessions 列投影），删除手写 CANONICAL parity 测试（ActorSubscription、content-filter），并在两端新增行为/一致性 smoke。SharedConstant 原语与这些 registry 条目已随 #220（incident alerting）独立落到 main；IMP-ADR055-02 据此对齐其实现。ADR-018 query golden 不变（组装 SQL 字节一致）。
```

- [ ] **Step 3: CONTEXT.md** — add a **Shared constant** term immediately after the **Generated contract module** definition, in the format consistent with the surrounding section:

```markdown
- **Shared constant** — a non-SQL cross-backend datum (allow-list / enum / column projection) declared once in the ADR-055 registry (`javdb/storage/contract/fragments.py` `CONSTANTS`) as a `SharedConstant` (`string` / `number` / `string_set` / `string_array`) and emitted into the generated contract module; neither backend hand-writes it.
```

CONTEXT.md is Chinese-primary, so also add the paired Chinese definition — the exact block below (not an illustrative example) — in the same place/format:

```markdown
- **共享常量 (Shared constant)** — 一个非 SQL 的跨后端数据项（允许表 / 枚举 / 列投影），在 ADR-055 registry（`javdb/storage/contract/fragments.py` 的 `CONSTANTS`）中作为 `SharedConstant`（`string` / `number` / `string_set` / `string_array`）声明一次，并发射进生成的契约模块；两个后端都不手写它。
```

- [ ] **Step 4: Verify ADR-018 already back-references ADR-055** (added in Phase 1). If missing, append to ADR-018's Status Log (both `.md` and `.zh.md`):

Run: `grep -n "ADR-055" /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/docs/design/ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md`
Expected: a D7/ADR-055 back-reference exists (Phase 1 Task 9). No edit needed if present.

- [ ] **Step 5: Commit docs**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add docs/design/ADR-055-Dual-Backend-Contract-Single-Source CONTEXT.md
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "docs(adr-055): mark Phase 2 delivered; add Shared constant CONTEXT term"
```

---

### Task D2: Full cross-repo green check + IMP closeout

- [ ] **Step 1: MAIN — full contract + migrated-consumer suite**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 && PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py tests/unit/test_contract_fragments.py tests/unit/test_contract_constants.py tests/unit/test_dump_sql_contract.py tests/unit/test_sql_contract_freshness.py tests/unit/test_actor_subscription_upsert_behavior.py tests/unit/test_system_state_upsert_behavior.py tests/unit/test_system_state_repo.py tests/unit/test_content_filter_constants_contract.py tests/unit/test_content_filter_router.py tests/unit/test_report_session_columns_contract.py tests/unit/test_query_contract_golden.py -p no:cacheprovider -q --continue-on-collection-errors`
Expected: all targeted contract/migration tests green (pre-existing 401-auth env failures in router tests are baseline, not regressions).

- [ ] **Step 2: MAIN — ADR-018 query golden is unchanged**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 && PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_query_contract && git diff --quiet docs/api/contract/query-builders.golden.json && echo "QUERY GOLDEN UNCHANGED ✓"`
Expected: `QUERY GOLDEN UNCHANGED ✓`.

- [ ] **Step 3: WEB — typecheck + server + unit**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npm run typecheck && npm run test:server && npm run test:unit`
Expected: all green; deleted parity specs absent; new contract smokes present.

- [ ] **Step 4: Vendored freshness proof (VENDORED == MAIN)**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && SQL_CONTRACT_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract && git diff --quiet server/contract/sql-contract.gen.ts && echo "VENDORED == MAIN ✓"`
Expected: `VENDORED == MAIN ✓`.

- [ ] **Step 5: Static guard searches** — confirm no hand-written copies remain:

Run (MAIN): `grep -rn "INSERT INTO ActorSubscription\|INSERT INTO system_state" /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/javdb /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/apps`
Expected: only `javdb/storage/contract/fragments.py`.

Run (WEB): `grep -rn "new Set(\[\|INSERT INTO ActorSubscription\|INSERT INTO system_state\|Id, Status, WriteMode" /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/server --include=*.ts | grep -v "contract/sql-contract.gen.ts"`
Expected: no hand-written ActorSubscription/system_state SQL, no hand-written allow-list Sets, no hand-written column string outside the generated module.

- [ ] **Step 6: Diff hygiene**

Run: `git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 diff --check main..HEAD && git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web diff --check main..HEAD`
Expected: clean.

- [ ] **Step 7: Mark this IMP Completed** — set the Status header to `Completed — implemented and verified on 2026-06-15`, check off the Done-when boxes, and fill Completion Evidence with the exact commands + pass counts. Commit:

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 add docs/design/ADR-055-Dual-Backend-Contract-Single-Source/IMP-ADR055-02-migrate-static-mirrors.md
git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 commit -m "docs(adr-055): close out IMP-ADR055-02 with verification evidence"
```

---

## Done-when (Phase 2 acceptance)

- [x] `javdb/storage/contract/` is the only hand-authored home of the ActorSubscription upsert, system_state upsert, content-filter allow-lists, and ReportSessions projection.
- [x] `docs/api/contract/sql-contract.gen.ts` is regenerated (3 fragments + 3 constants), committed, and freshness-tested in MAIN.
- [x] The web Worker imports `prepareActorSubscriptionUpsert`, `prepareSystemStateUpsert`, `VALID_RULE_MODES`/`VALUE_REQUIRED`, and `REPORT_SESSION_COLUMNS` from the vendored module; no hand-written copies remain.
- [x] Both hand-CANONICAL parity tests (ActorSubscription, content-filter — Python + TS) are deleted; behavioral/conformance smokes pass on both sides.
- [x] The ADR-018 query golden is byte-unchanged (regen no-op) in both repos.
- [x] All MAIN + WEB suites green; `VENDORED == MAIN ✓`.
- [x] ADR-055 roadmap/Status Log (`.md` + `.zh.md`) + CONTEXT.md updated; this IMP marked Completed with evidence.

## Completion Evidence

- 2026-06-15 MAIN contract + migrated-consumer suite: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_contract_types.py tests/unit/test_contract_fragments.py tests/unit/test_contract_constants.py tests/unit/test_dump_sql_contract.py tests/unit/test_sql_contract_freshness.py tests/unit/test_actor_subscription_upsert_behavior.py tests/unit/test_system_state_upsert_behavior.py tests/unit/test_system_state_repo.py tests/unit/test_content_filter_constants_contract.py tests/unit/test_content_filter_router.py tests/unit/test_report_session_columns_contract.py tests/unit/test_query_contract_golden.py -p no:cacheprovider -q --continue-on-collection-errors` — **125 passed** (no collection errors; content-filter router gating tests green, no 401 baseline failures in this targeted set).
- 2026-06-15 MAIN ADR-018 query golden no-op: `python3 -m apps.cli.ops.dump_query_contract && git diff --quiet docs/api/contract/query-builders.golden.json && echo "QUERY GOLDEN UNCHANGED ✓"` — regenerated 70 cases, `QUERY GOLDEN UNCHANGED ✓` (assembled `_build_session_query` SQL byte-identical after the registry migration).
- 2026-06-15 WEB verification: `npm run typecheck && npm run test:server && npm run test:unit` — typecheck (`vue-tsc --noEmit`) clean; test:server **45 files / 400 tests passed**; test:unit **42 files / 174 tests passed**. Deleted parity specs (`actor-subscription-upsert-parity.test.ts`, `content-filter-modes-parity.test.ts`) no longer collected; new contract smokes (`actor-subscription-contract.test.ts`, `system-state-contract.test.ts`, `content-filter-contract.test.ts`) present.
- 2026-06-15 vendored freshness proof: `SQL_CONTRACT_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0/docs/api/contract/sql-contract.gen.ts npm run gen:sql-contract && git diff --quiet server/contract/sql-contract.gen.ts && echo "VENDORED == MAIN ✓"` — `VENDORED == MAIN ✓` (re-vendor produced zero diff).
- 2026-06-15 static guard checks: MAIN `grep -rn "INSERT INTO ActorSubscription\|INSERT INTO system_state" javdb apps` returns only `javdb/storage/contract/fragments.py` (the single source). WEB production consumers import from `../contract/sql-contract.gen` only — `content-filter.ts` (`VALID_RULE_MODES`/`VALUE_REQUIRED`), `subscription-service.ts` (`prepareActorSubscriptionUpsert`), `system-state-store.ts` (`prepareSystemStateUpsert`), `sessions.ts` (`REPORT_SESSION_COLUMNS`); no hand-written copies of the four migrated constructs remain (remaining `new Set([...])` / `INSERT INTO …` / column-string grep hits are unrelated backend-local allow-lists or test fixtures/seeds, not the migrated constructs).
- 2026-06-15 diff hygiene: `git -C /Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/happy-hoover-2ec5b0 diff --check main..HEAD` passed (exit 0) in MAIN; `git -C /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web diff --check main..HEAD` passed (exit 0) in WEB.
