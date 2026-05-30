# IMP-ADR040-01: Content Filter Engine + Rules (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-040](ADR-040-content-filter-rules.md) (umbrella) — this is **Phase 1** of three.

**Goal:** A deterministic content-filter stage that drops parsed movies by identity/attribute rules (actor blacklist, tag include/exclude, gender) — read from a dynamic `ContentFilterRule` D1 table — applied after detail parse and AND-ed with the existing rating/rater filter.

**Architecture:** `javdb/spider/services/content_filter.py` (sibling to `dedup.py`) exposes a pure `evaluate(detail, rules) -> FilterDecision`. A `ContentFilterRepo` loads enabled rules from D1 (reports). The detail-success path loads rules once per run and drops any movie the engine rejects (with a logged reason) before it reaches the CSV/uploader. A CLI manages rules.

**Tech Stack:** Python 3, `sqlite3`/D1 via `get_db`, `dataclasses`, `pytest`, `wrangler`.

**Storage placement:** `ContentFilterRule` lives in the **reports** logical DB (`javdb-reports`), alongside the other control-plane tables added this session.

**Confirmed shapes:** `MovieDetail.actors: list[ActorCredit]` (`name`, `href`, `gender` ∈ `{female, male, ''}`); `MovieDetail.tags: list[MovieLink]` (`name`, `href`); `MovieDetail.video_code`, `.title`.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql` | Create | `ContentFilterRule` DDL |
| `javdb/spider/services/content_filter.py` | Create | `ContentFilterRule` dataclass, `FilterDecision`, `evaluate()` |
| `javdb/storage/repos/content_filter_repo.py` | Create | `ContentFilterRepo` (load/add/list/remove/set_enabled) |
| `javdb/spider/detail/runner.py` | Modify | Apply the filter in the detail-success path |
| `apps/cli/ops/content_filter.py` | Create | CLI: add/list/remove/enable rules |
| `config.py.example` | Modify | Note the dynamic rule table (no config keys needed) |
| `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Terms + CLI |
| `tests/unit/test_content_filter_engine.py` … | Create | Engine + repo tests |

**Naming contract (verbatim):** `Rule(id, dimension, mode, value, enabled)`;
`FilterDecision(keep: bool, reasons: list[str])`;
`evaluate(detail, rules) -> FilterDecision`;
`ContentFilterRepo` with `load_rules() -> list[Rule]`, `add_rule(dimension, mode, value) -> int`,
`list_rules() -> list[Rule]`, `remove_rule(rule_id)`, `set_enabled(rule_id, enabled)`.

> **Phase-2-gated:** age filter (needs actor-profile enrichment); subscriptions
> (whitelist bypassing the rating threshold); web/MCP rule management.

---

## Task 1: D1 migration — `ContentFilterRule`

**Files:**
- Create: `javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-05-29: Add ContentFilterRule table (ADR-040 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql
--
-- Dynamic content-filter rules applied after detail parse. Additive: no rows = no change.

CREATE TABLE IF NOT EXISTS ContentFilterRule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  dimension  TEXT NOT NULL,   -- actor | tag | gender
  mode       TEXT NOT NULL,   -- exclude | include | require_lead | exclude_all_male
  value      TEXT,            -- actor name/href | tag | gender value
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_content_filter_enabled ON ContentFilterRule(enabled, dimension);
```

- [ ] **Step 2: Apply to D1, re-align SQLite**

Run:
```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: statements execute; table rebuilt locally; exit 0.

- [ ] **Step 3: Verify**

Run:
```bash
python3 -c "import sqlite3,glob; p=glob.glob('reports/reports.db')[0]; print(sqlite3.connect(p).execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='ContentFilterRule'\").fetchone())"
```
Expected: `('ContentFilterRule',)`

- [ ] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql
git commit -m "feat(db): add ContentFilterRule table (ADR-040 Phase 1)"
```

---

## Task 2: The `evaluate` engine (pure)

**Files:**
- Create: `javdb/spider/services/content_filter.py`
- Test: `tests/unit/test_content_filter_engine.py`

Semantics: **blacklist wins** (any matching `exclude` → drop), then `include`/gender
rules AND together. Tag-include is "≥1 match required if any include rule exists".
Lead actor = `actors[0]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_content_filter_engine.py
from dataclasses import dataclass, field

from javdb.spider.services.content_filter import Rule, evaluate


@dataclass
class _Actor:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Tag:
    name: str = ""
    href: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    video_code: str = "ABC-1"
    title: str = "t"


def _rule(dimension, mode, value, rid=1):
    return Rule(id=rid, dimension=dimension, mode=mode, value=value, enabled=True)


def test_no_rules_keeps():
    assert evaluate(_Detail(actors=[_Actor(name="X")]), []).keep is True


def test_actor_blacklist_drops_and_wins():
    d = _Detail(actors=[_Actor(name="Bad Actor", href="/actors/ev")],
                tags=[_Tag(name="wanted")])
    rules = [_rule("actor", "exclude", "Bad Actor"),
             _rule("tag", "include", "wanted", rid=2)]  # include would pass...
    dec = evaluate(d, rules)
    assert dec.keep is False  # ...but blacklist wins
    assert any("Bad Actor" in r for r in dec.reasons)


def test_actor_blacklist_matches_href():
    d = _Detail(actors=[_Actor(name="N", href="/actors/EvkJ")])
    assert evaluate(d, [_rule("actor", "exclude", "/actors/EvkJ")]).keep is False


def test_tag_exclude_drops():
    d = _Detail(tags=[_Tag(name="vr")])
    assert evaluate(d, [_rule("tag", "exclude", "vr")]).keep is False


def test_tag_include_requires_at_least_one():
    rules = [_rule("tag", "include", "subtitle")]
    assert evaluate(_Detail(tags=[_Tag(name="subtitle")]), rules).keep is True
    assert evaluate(_Detail(tags=[_Tag(name="other")]), rules).keep is False


def test_gender_require_lead_female():
    rules = [_rule("gender", "require_lead", "female")]
    assert evaluate(_Detail(actors=[_Actor(name="A", gender="female")]), rules).keep is True
    assert evaluate(_Detail(actors=[_Actor(name="A", gender="male")]), rules).keep is False


def test_gender_exclude_all_male():
    rules = [_rule("gender", "exclude_all_male", "")]
    assert evaluate(_Detail(actors=[_Actor(gender="male"), _Actor(gender="male")]), rules).keep is False
    assert evaluate(_Detail(actors=[_Actor(gender="male"), _Actor(gender="female")]), rules).keep is True


def test_disabled_rule_ignored():
    r = Rule(id=1, dimension="actor", mode="exclude", value="X", enabled=False)
    assert evaluate(_Detail(actors=[_Actor(name="X")]), [r]).keep is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_content_filter_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the engine**

```python
# javdb/spider/services/content_filter.py
"""Deterministic content-filter engine (ADR-040 Phase 1).

Pure: evaluate(detail, rules) -> FilterDecision. Blacklist wins; include/gender
rules AND together. Reads only attributes present on the parsed MovieDetail
(actors with name/href/gender, tags) — age is out of scope (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Rule:
    id: int
    dimension: str   # actor | tag | gender
    mode: str        # exclude | include | require_lead | exclude_all_male
    value: str
    enabled: bool = True


@dataclass
class FilterDecision:
    keep: bool
    reasons: list[str] = field(default_factory=list)


def _norm(s) -> str:
    return (s or "").strip().lower()


def _actor_matches(detail, value: str) -> bool:
    v = _norm(value)
    for a in getattr(detail, "actors", []) or []:
        if v and (v == _norm(getattr(a, "name", "")) or v == _norm(getattr(a, "href", ""))):
            return True
    return False


def _tag_names(detail) -> set[str]:
    return {_norm(getattr(t, "name", "")) for t in (getattr(detail, "tags", []) or [])}


def evaluate(detail, rules) -> FilterDecision:
    active = [r for r in rules if getattr(r, "enabled", True)]

    # 1) Blacklist wins.
    for r in active:
        if r.mode != "exclude":
            continue
        if r.dimension == "actor" and _actor_matches(detail, r.value):
            return FilterDecision(False, [f"excluded actor: {r.value}"])
        if r.dimension == "tag" and _norm(r.value) in _tag_names(detail):
            return FilterDecision(False, [f"excluded tag: {r.value}"])

    reasons: list[str] = []

    # 2) Tag include (AND across the include group: require >=1 match if any exist).
    include_tags = {_norm(r.value) for r in active if r.dimension == "tag" and r.mode == "include"}
    if include_tags and not (include_tags & _tag_names(detail)):
        return FilterDecision(False, [f"no included tag present (need one of {sorted(include_tags)})"])

    # 3) Gender rules.
    actors = getattr(detail, "actors", []) or []
    for r in active:
        if r.dimension != "gender":
            continue
        if r.mode == "require_lead":
            lead_gender = _norm(getattr(actors[0], "gender", "")) if actors else ""
            if lead_gender != _norm(r.value):
                return FilterDecision(False, [f"lead actor gender != {r.value}"])
        elif r.mode == "exclude_all_male":
            genders = {_norm(getattr(a, "gender", "")) for a in actors}
            if actors and genders == {"male"}:
                return FilterDecision(False, ["all actors male"])

    return FilterDecision(True, reasons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_content_filter_engine.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/services/content_filter.py tests/unit/test_content_filter_engine.py
git commit -m "feat(spider): add deterministic content-filter engine (ADR-040)"
```

---

## Task 3: `ContentFilterRepo`

**Files:**
- Create: `javdb/storage/repos/content_filter_repo.py`
- Test: `tests/unit/test_content_filter_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_content_filter_repo.py
import sqlite3

import pytest

from javdb.storage.repos.content_filter_repo import ContentFilterRepo

_DDL = """
CREATE TABLE ContentFilterRule (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT NOT NULL, mode TEXT NOT NULL,
  value TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT ''
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ContentFilterRepo(c)


def test_add_then_load_only_enabled(repo):
    rid = repo.add_rule("actor", "exclude", "Bad")
    repo.add_rule("tag", "include", "subtitle")
    repo.set_enabled(rid, False)
    loaded = repo.load_rules()
    assert {r.value for r in loaded} == {"subtitle"}  # disabled excluded


def test_list_includes_disabled(repo):
    rid = repo.add_rule("actor", "exclude", "X")
    repo.set_enabled(rid, False)
    assert len(repo.list_rules()) == 1


def test_remove(repo):
    rid = repo.add_rule("tag", "exclude", "vr")
    repo.remove_rule(rid)
    assert repo.list_rules() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_content_filter_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the repo**

```python
# javdb/storage/repos/content_filter_repo.py
"""Repository for ADR-040 ContentFilterRule rows (reports DB)."""

from __future__ import annotations

import logging
import sqlite3

from javdb.spider.services.content_filter import Rule

logger = logging.getLogger(__name__)


class ContentFilterRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def _rows(self, where: str = "", params=()) -> list[Rule]:
        rows = self._conn.execute(
            f"SELECT id, dimension, mode, value, enabled FROM ContentFilterRule {where}",
            params,
        ).fetchall()
        return [Rule(id=r["id"], dimension=r["dimension"], mode=r["mode"],
                     value=r["value"], enabled=bool(r["enabled"])) for r in rows]

    def load_rules(self) -> list[Rule]:
        return self._rows("WHERE enabled = 1")

    def list_rules(self) -> list[Rule]:
        return self._rows("ORDER BY id")

    def add_rule(self, dimension: str, mode: str, value: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO ContentFilterRule (dimension, mode, value) VALUES (?, ?, ?)",
            [dimension, mode, value],
        )
        return int(cur.lastrowid)

    def remove_rule(self, rule_id: int) -> None:
        self._conn.execute("DELETE FROM ContentFilterRule WHERE id = ?", [rule_id])

    def set_enabled(self, rule_id: int, enabled: bool) -> None:
        self._conn.execute("UPDATE ContentFilterRule SET enabled = ? WHERE id = ?",
                           [1 if enabled else 0, rule_id])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_content_filter_repo.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/content_filter_repo.py tests/unit/test_content_filter_repo.py
git commit -m "feat(db): add ContentFilterRepo (ADR-040)"
```

---

## Task 4: Wire the filter into the detail-success path

**Files:**
- Modify: `javdb/spider/detail/runner.py` (the detail-success / persist stage, ~line 239)
- Test: `tests/unit/test_content_filter_wiring.py`

After a detail page parses successfully, evaluate the rules and **drop** the movie
(skip persist/CSV) when the engine rejects it, logging the reason. Rules are loaded
**once per run** and threaded into the detail-success handler.

- [ ] **Step 1: Write the failing test** (pins the helper the wiring uses)

```python
# tests/unit/test_content_filter_wiring.py
from dataclasses import dataclass, field

from javdb.spider.services.content_filter import Rule, evaluate


@dataclass
class _Actor:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    video_code: str = "ABC-1"


def test_runner_drops_blacklisted_actor():
    """Pin the decision the runner consumes: blacklisted actor → keep False."""
    rules = [Rule(id=1, dimension="actor", mode="exclude", value="Bad")]
    decision = evaluate(_Detail(actors=[_Actor(name="Bad")]), rules)
    assert decision.keep is False  # the runner must skip persist when keep is False
```

- [ ] **Step 2: Run to verify PASS** (pins `evaluate` from Task 2)

Run: `pytest tests/unit/test_content_filter_wiring.py -v`
Expected: PASS

- [ ] **Step 3: Locate the detail-success seam**

Run: `grep -nE "def .*detail.*success|successful detail|persist|write_csv|def _persist|MovieDetail" javdb/spider/detail/runner.py | head`
Identify the function that handles a successfully-parsed detail (it has the parsed
`MovieDetail` and writes/persists it; the docstring at ~line 239 is "Best-effort
Phase-1 stage of a successful detail fetch").

- [ ] **Step 4: Load rules once per run** — at the start of the detail-run entry
  (the function near line 430, "Run the shared detail pipeline against a concrete
  fetch backend"), load and pass rules down:

```python
        from javdb.storage.db import get_db, REPORTS_DB_PATH
        from javdb.storage.repos.content_filter_repo import ContentFilterRepo
        try:
            with get_db(REPORTS_DB_PATH) as _cf_conn:
                _content_rules = ContentFilterRepo(_cf_conn).load_rules()
        except Exception:
            _content_rules = []  # additive: failure to load rules never blocks ingestion
```

> Thread `_content_rules` into the detail-success handler (function signature or a
> run-scoped attribute, matching how the runner already passes per-run state).

- [ ] **Step 5: Apply the filter in the detail-success handler** — where the parsed
  `MovieDetail` (call it `detail`) is available and about to be persisted/written:

```python
        from javdb.spider.services.content_filter import evaluate as _content_evaluate
        _decision = _content_evaluate(detail, _content_rules)
        if not _decision.keep:
            logger.info("Content filter dropped %s: %s",
                        getattr(detail, "video_code", "?"), "; ".join(_decision.reasons))
            return  # skip persist/CSV — drop this movie (ADR-040 D3)
```

> Place this **before** the persist/CSV write so dropped movies never reach the
> uploader. Match the handler's actual early-return / skip convention (it already has
> skip paths for history/contention). The reasons-logging line is the audit trail.

- [ ] **Step 6: Import-smoke + tests**

Run: `python3 -c "import javdb.spider.detail.runner; print('import ok')" && pytest tests/unit/test_content_filter_wiring.py -v`
Expected: `import ok` + PASS

- [ ] **Step 7: Commit**

```bash
git add javdb/spider/detail/runner.py tests/unit/test_content_filter_wiring.py
git commit -m "feat(spider): apply content filter after detail parse (ADR-040 D3)"
```

---

## Task 5: CLI to manage rules

**Files:**
- Create: `apps/cli/ops/content_filter.py`
- Test: `tests/smoke/test_content_filter_cli.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_content_filter_cli.py
import subprocess
import sys


def test_content_filter_cli_help():
    r = subprocess.run([sys.executable, "-m", "apps.cli.ops.content_filter", "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "rule" in r.stdout.lower()
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/smoke/test_content_filter_cli.py -v`
Expected: FAIL — no module

- [ ] **Step 3: Write the CLI**

```python
# apps/cli/ops/content_filter.py
"""Manage ADR-040 content-filter rules (add / list / remove / enable)."""

from __future__ import annotations

import argparse
import sys

from javdb.infra.logging import setup_logging
from javdb.storage.db import get_db, REPORTS_DB_PATH
from javdb.storage.repos.content_filter_repo import ContentFilterRepo


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apps.cli.ops.content_filter",
                                description="Manage content-filter rules (ADR-040).")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="Add a rule")
    a.add_argument("--dimension", required=True, choices=("actor", "tag", "gender"))
    a.add_argument("--mode", required=True,
                   choices=("exclude", "include", "require_lead", "exclude_all_male"))
    a.add_argument("--value", default="")
    sub.add_parser("list", help="List all rules")
    r = sub.add_parser("remove", help="Remove a rule")
    r.add_argument("--id", type=int, required=True)
    e = sub.add_parser("enable", help="Enable/disable a rule")
    e.add_argument("--id", type=int, required=True)
    e.add_argument("--off", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(level="INFO")
    with get_db(REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        if args.cmd == "add":
            rid = repo.add_rule(args.dimension, args.mode, args.value)
            print(f"added rule id={rid}")
        elif args.cmd == "list":
            for r in repo.list_rules():
                flag = "" if r.enabled else " (disabled)"
                print(f"[{r.id}] {r.dimension} {r.mode} {r.value!r}{flag}")
        elif args.cmd == "remove":
            repo.remove_rule(args.id)
            print(f"removed rule id={args.id}")
        elif args.cmd == "enable":
            repo.set_enabled(args.id, not args.off)
            print(f"rule id={args.id} enabled={not args.off}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/smoke/test_content_filter_cli.py -v`
Expected: PASS (1)

- [ ] **Step 5: Commit**

```bash
git add apps/cli/ops/content_filter.py tests/smoke/test_content_filter_cli.py
git commit -m "feat(cli): add content-filter rule management CLI (ADR-040)"
```

---

## Task 6: Docs + full gate

**Files:**
- Modify: `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh)

- [ ] **Step 1: Update CONTEXT.md** — add ADR-040 terms verbatim: *Content filter rule*,
  *Blacklist*, *Attribute filter*, *Filter decision*, *Subscription* (mark Subscription Phase-2).

- [ ] **Step 2: Update CLI reference** — document `python -m apps.cli.ops.content_filter`
  (`add`/`list`/`remove`/`enable` + the `--dimension`/`--mode`/`--value` options) in the
  en cli-reference; mirror to zh.

- [ ] **Step 3: Full gate**

Run:
```bash
pytest tests/unit/test_content_filter_engine.py tests/unit/test_content_filter_repo.py \
       tests/unit/test_content_filter_wiring.py tests/smoke/test_content_filter_cli.py -v
```
Expected: all PASS.

- [ ] **Step 4: Additive-invariant check** — with no rules, ingestion is unchanged:

Run: `python3 -c "from javdb.spider.services.content_filter import evaluate; print(evaluate(object(), []).keep)"`
Expected: `True` (empty rules → keep everything).

- [ ] **Step 5: Commit**

```bash
git add CONTEXT.md docs/handbook
git commit -m "docs: document ADR-040 content-filter rules + CLI"
```

---

## Plan Self-Review

**Spec coverage (ADR-040 Phase 1 row + D-decisions):**
- `ContentFilterRule` D1 table (D2) → Task 1. ✓
- Filter after detail parse, before queue (D3) → Task 4 (drops before persist/CSV). ✓
- Blacklist wins; rules AND (D4) → Task 2 engine + its tests. ✓
- Phase 1 dimensions from existing parse — actor/tag/gender (D5) → Task 2. ✓
- Deterministic, explainable; orthogonal to ADR-022/025 (D6) → engine returns reasons;
  no preference model touched. ✓
- Module `content_filter.py` + repo (D7) → Tasks 2, 3. ✓
- Deferred: age, subscriptions, web/MCP mgmt → not built; documented. ✓
- CLI rule management → Task 5. ✓
- Docs → Task 6. ✓

**Type consistency:** `Rule`, `FilterDecision`, `evaluate`, `ContentFilterRepo`
(`load_rules`/`add_rule`/`list_rules`/`remove_rule`/`set_enabled`) are used identically
across Tasks 2-6.

**Integration point (grep-located):** Task 4 wires the engine into `detail/runner.py`'s
detail-success handler (Step 3 greps the exact function; Steps 4-5 load rules once + drop
before persist). The engine + repo are independently green; the wiring is the integration.

**Additive guarantee:** no rules → `evaluate` keeps everything (Task 6 Step 4); rule-load
failure falls back to `[]` (Task 4 Step 4), so a content-filter problem never blocks
ingestion. The existing rating/rater filter is untouched (a parallel gate).
