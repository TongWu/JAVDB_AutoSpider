# WS4a Content-Filter Regex + Release-Date Engine (ADR-040 / ADR-054 WS4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the content-filter engine with two new capabilities on the existing `ContentFilterRule` table — **regex matching** (`regex_exclude` / `regex_include` on actor/tag dimensions) and **release-date bounding** (`release_date` dimension with `before` / `after` modes) — plus the CLI validation to author them. **NO `ContentFilterRule` schema migration**: both reuse the generic `(dimension, mode, value)` triple exactly as the Phase-2 `age` dimension did. This is the engine + CLI half of WS4a; the dual-backend web CRUD `/api/content-filter` and the SPA Settings/overlay surface are the **separate** IMP-ADR040-04.

**Architecture:** Purely additive engine branches inside `javdb/spider/services/content_filter.py` (a `_matches_regex` helper wired into `_matches_exclude_rule` and the include path; a `_release_date_drop_reasons` helper paralleling `_age_drop_reasons`), plus the CLI allow-list/validation tuples in `apps/cli/ops/content_filter.py`. Both regex compilation and date parsing **fail open** (a bad pattern or unparseable date never drops a movie and never raises into the ingestion loop), mirroring the age `int()` guard at `content_filter.py:120-123`. The release-date branch needs **no external resolver wiring** — `detail.release_date` is already parsed onto `MovieDetail` (`javdb/parsing/models.py:117`) and is passed into `evaluate()` at the existing call site (`javdb/spider/detail/runner.py:775`), so it is strictly simpler than the age dimension (which needed the `actor_age_resolver`). The task closes by backfilling the two stale `ContentFilterRule` DDL header comments (which still omit the Phase-2 `age`/`min_age`/`max_age` modes) and amending ADR-040's Status Log and roadmap, bilingually.

**Tech Stack:** Python 3 / `re` / `datetime.date` / pytest. No D1, no TypeScript, no Vue, no schema migration, no `openapi.json` change.

**Cross-repo note.** This is a **[MAIN]-only** IMP. All work lands in:
- **[MAIN]** = `/Users/tedwu/JAVDB_AutoSpider_CICD` — the engine, the CLI, the tests, the stale-comment doc-hygiene fix, and the ADR-040 amendment. Run `git` with `git -C /Users/tedwu/JAVDB_AutoSpider_CICD ...`.

There is **no [WEB] surface** in this IMP. The capability flag (`content_filter`), the dual-backend `/api/content-filter` router/route, the `src/api/content-filter.ts` client, the `SettingsFilterRulesPage.vue` CRUD table, and the read-side Browse overlay all belong to **IMP-ADR040-04** and are out of scope here.

Work the phases in order: **A (engine, TDD)** → **B (CLI, TDD)** → **C (doc-hygiene: backfill both stale DDL comments)** → **D (ADR-040 Status Log + roadmap, bilingual)**.

---

## Design decisions locked by ADR-054 WS4-D3 / the Phase-2 precedent (do not re-litigate)

- **No `ContentFilterRule` schema migration.** Regex and release-date reuse the existing generic `ContentFilterRule(id, dimension, mode, value, enabled, created_at)` triple in **REPORTS_DB** (`javdb-reports`) — exactly as the `age` dimension did in Phase 2 (zero schema change). `value` is already nullable TEXT and holds the regex pattern or the ISO date string.
- **New legal `(dimension, mode)` pairs:**

  | dimension | mode(s) | `value` holds |
  | --- | --- | --- |
  | `actor`, `tag` | `regex_exclude`, `regex_include` | a Python regex pattern (`re.search` semantics) |
  | `release_date` | `before`, `after` | an ISO date string (`YYYY-MM-DD`) |

- **Fail-open everywhere.** A `re.error` from a bad pattern, or a `ValueError`/`TypeError` from an unparseable/missing date, is swallowed and the rule is skipped — it never drops a movie and never raises into the ingestion detail loop. This mirrors the age `int()` guard (`content_filter.py:120-123`). Catastrophic-backtracking safety (compile-time validation / length cap at a CRUD boundary) is deferred to IMP-ADR040-04 where rules become web-authored; CLI-authored patterns are operator-trusted.
- **`regex_exclude` is blacklist-precedence** (drops immediately, via `_matches_exclude_rule`, like `actor`/`tag` `exclude`). `regex_include` is an AND-ed requirement (at least one tag/actor must match, paralleling `tag include`). `release_date before/after` is an attribute drop reason (paralleling age), AND-ed with the rest.
- **`before` is exclusive-of-bound-and-later, `after` is exclusive-of-bound-and-earlier**, i.e. a `before` rule drops a movie whose `release_date >= value`; an `after` rule drops a movie whose `release_date <= value`. An absent or unparseable `detail.release_date` **never** drops (parallels "unknown age never drops").
- **No resolver wiring.** Unlike age (which needed `actor_age_resolver.ages_for(detail)`), release-date reads `detail.release_date` directly — the call site at `runner.py:775` already passes `movie_detail`. No change to `evaluate()`'s signature, no change to the runner.
- **Allow-list source of truth stays in the CLI.** `apps/cli/ops/content_filter.py`'s `VALID_RULE_MODES` / `VALUE_REQUIRED` / `DIMENSIONS` / `MODES` tuples remain canonical (per WS4-D3). The future Python router (IMP-ADR040-04) will import them; the TS route will hand-mirror them, pinned by a parity test — **none of that is in this IMP**.
- **Stale-comment backfill is comment-only.** Both `ContentFilterRule` DDL header comments (`2026_05_29_add_content_filter_rule.sql:7-10` and `_db_migrations.py:236-238`) still say `dimension: actor | tag | gender`, omitting the Phase-2 `age`/`min_age`/`max_age`. This IMP touches those comments to document regex/release-date anyway, so it backfills age too. No DDL/behavior change.

---

## File Structure

**[MAIN] create:**
- `tests/unit/test_content_filter_regex.py` — regex engine unit tests (drop / keep / bad-pattern fail-open).
- `tests/unit/test_content_filter_release_date.py` — release-date engine unit tests (before/after drop & keep, missing/unparseable-date no-drop).

**[MAIN] modify:**
- `javdb/spider/services/content_filter.py` — add `_matches_regex` + wire into `_matches_exclude_rule` (actor/tag exclude) and the actor/tag regex-include path; add `_release_date_drop_reasons` + call it from `evaluate()`.
- `apps/cli/ops/content_filter.py` — extend `DIMENSIONS` / `MODES` / `VALID_RULE_MODES` / `VALUE_REQUIRED`; add regex-compile + ISO-date validation to `_validate_add`.
- `tests/smoke/test_content_filter_cli.py` — add CLI validation cases for the new pairs (extends the existing parametrized table + adds regex/date acceptance cases).
- `javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql` — backfill the stale header comment (age + regex/date), comment-only.
- `javdb/storage/db/_db_migrations.py` — backfill the same comment in the `_REPORTS_DDL` mirror, comment-only.
- `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md` + `.zh.md` — Status Log entry + roadmap amendment (regex/date phase).

**[MAIN] not touched (deliberately):** `javdb/spider/detail/runner.py` (no call-site change — `evaluate()` signature is unchanged and `detail.release_date` already flows in), `docs/api/openapi.json` (no API surface), any `server/`/`src/` file (no web surface).

---

## Phase A — Engine: regex + release-date branches (TDD) [MAIN]

### Task 1: `_matches_regex` helper + wiring (TDD)

**Files:**
- Modify: `javdb/spider/services/content_filter.py`
- Test: `tests/unit/test_content_filter_regex.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_content_filter_regex.py` (mirrors `test_content_filter_age.py`: local lightweight dataclasses so the engine is exercised in isolation; `ActorCredit`/`MovieLink`-shaped objects need only `name`/`href`):

```python
# tests/unit/test_content_filter_regex.py
"""Regex content-filter engine branches (ADR-040 WS4a / IMP-ADR040-03)."""

from dataclasses import dataclass, field

from javdb.spider.services.content_filter import FilterDecision, Rule, evaluate


@dataclass
class _Link:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    release_date: str = ""


def _rule(dimension, mode, value, rid=1, enabled=True):
    return Rule(id=rid, dimension=dimension, mode=mode, value=value, enabled=enabled)


def test_regex_exclude_drops_matching_tag():
    detail = _Detail(tags=[_Link(name="VR Exclusive", href="/tags/vr")])
    dec = evaluate(detail, [_rule("tag", "regex_exclude", r"(?i)\bvr\b")])
    assert dec.keep is False
    assert dec.reasons == ["excluded by tag rule: (?i)\\bvr\\b"]


def test_regex_exclude_matches_href():
    detail = _Detail(actors=[_Link(name="Lead", href="/actors/EvkJ")])
    dec = evaluate(detail, [_rule("actor", "regex_exclude", r"/actors/Evk")])
    assert dec.keep is False


def test_regex_exclude_keeps_when_no_match():
    detail = _Detail(tags=[_Link(name="Drama", href="/tags/drama")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", r"comedy")]).keep is True


def test_regex_include_requires_a_match():
    detail = _Detail(tags=[_Link(name="Drama", href="/tags/drama")])
    dec = evaluate(detail, [_rule("tag", "regex_include", r"comedy")])
    assert dec.keep is False
    assert any("missing required tag regex" in r for r in dec.reasons)


def test_regex_include_passes_when_one_tag_matches():
    detail = _Detail(
        tags=[_Link(name="Drama", href="/tags/drama"), _Link(name="Comedy", href="/tags/comedy")]
    )
    assert evaluate(detail, [_rule("tag", "regex_include", r"(?i)comedy")]).keep is True


def test_bad_pattern_fails_open_exclude():
    # An unbalanced group is a re.error; it must be skipped, never raised, never drop.
    detail = _Detail(tags=[_Link(name="VR", href="/tags/vr")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", r"(unclosed")]).keep is True


def test_bad_pattern_fails_open_include():
    detail = _Detail(tags=[_Link(name="VR", href="/tags/vr")])
    # A broken include rule must not impose a phantom requirement -> keeps.
    assert evaluate(detail, [_rule("tag", "regex_include", r"[")]).keep is True


def test_blank_regex_value_does_not_match():
    detail = _Detail(tags=[_Link(name="Drama", href="/tags/drama")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", "  ")]).keep is True


def test_disabled_regex_rule_ignored():
    detail = _Detail(tags=[_Link(name="VR", href="/tags/vr")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", r"vr", enabled=False)]).keep is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/unit/test_content_filter_regex.py -q`
Expected: FAIL — the current engine ignores `regex_exclude`/`regex_include` (no branch handles them), so `keep` is `True` where the test expects `False` (and the include-requirement reason is never produced).

- [ ] **Step 3: Add the `_matches_regex` helper**

In `javdb/spider/services/content_filter.py`, add `import re` to the top of the file (after `from typing import Iterable`), then add this helper immediately after `_matches_link` (it does `re.search`, not casefold equality, and fails open on `re.error` — the regex analogue of the age `int()` guard):

```python
def _matches_regex(pattern: str, item) -> bool:
    """True if ``pattern`` (a regex) matches the item's name or href.

    Fails open: a blank pattern or a ``re.error`` (bad pattern) never matches and
    never raises, mirroring the age ``int()`` guard. Operator-authored regex runs
    inside the ingestion loop, so a catastrophic pattern must not crash it.
    """
    pat = _clean_value(pattern)
    if not pat:
        return False
    try:
        compiled = re.compile(pat)
    except re.error:
        return False
    name = str(getattr(item, "name", "") or "")
    href = str(getattr(item, "href", "") or "")
    return bool(compiled.search(name) or compiled.search(href))
```

- [ ] **Step 4: Wire `regex_exclude` into `_matches_exclude_rule`**

In `_matches_exclude_rule`, the current body only handles `mode == 'exclude'`. Add a `regex_exclude` branch. Replace:

```python
def _matches_exclude_rule(detail, rule: Rule) -> bool:
    if rule.mode != 'exclude':
        return False
    if rule.dimension == 'actor':
        return any(_matches_link(rule.value, actor) for actor in detail.actors)
    if rule.dimension == 'tag':
        return any(_matches_link(rule.value, tag) for tag in detail.tags)
    return False
```

with:

```python
def _matches_exclude_rule(detail, rule: Rule) -> bool:
    if rule.mode == 'regex_exclude':
        if rule.dimension == 'actor':
            return any(_matches_regex(rule.value, actor) for actor in detail.actors)
        if rule.dimension == 'tag':
            return any(_matches_regex(rule.value, tag) for tag in detail.tags)
        return False
    if rule.mode != 'exclude':
        return False
    if rule.dimension == 'actor':
        return any(_matches_link(rule.value, actor) for actor in detail.actors)
    if rule.dimension == 'tag':
        return any(_matches_link(rule.value, tag) for tag in detail.tags)
    return False
```

> `_exclude_reason(rule)` already renders `excluded by {dimension} rule: {value}` for any matched exclude rule, so `regex_exclude` reuses it verbatim — no new reason builder needed. (That is what `test_regex_exclude_drops_matching_tag` pins.)

- [ ] **Step 5: Wire `regex_include` into the include path**

In `evaluate()`, the existing tag-include block collects `tag`/`include` rules and adds a `missing required tag include` reason. Add a parallel `regex_include` block immediately after it. Locate this block in `evaluate()`:

```python
    include_tag_rules = [
        rule
        for rule in enabled_rules
        if rule.dimension == 'tag' and rule.mode == 'include'
        and _normalized_match_value(rule.value)
    ]
    if include_tag_rules and not any(
        _matches_link(rule.value, tag)
        for rule in include_tag_rules
        for tag in detail.tags
    ):
        expected = ', '.join(_clean_value(rule.value) for rule in include_tag_rules)
        reasons.append(f'missing required tag include: {expected}')
```

Insert directly after it (still inside `evaluate()`, before the `gender` loop):

```python
    include_regex_rules = [
        rule
        for rule in enabled_rules
        if rule.dimension in ('actor', 'tag') and rule.mode == 'regex_include'
        and _clean_value(rule.value)
    ]
    if include_regex_rules and not any(
        _matches_regex(rule.value, field)
        for rule in include_regex_rules
        for field in (list(detail.tags) + [a.name for a in detail.actors])
    ):
        expected = ', '.join(_clean_value(rule.value) for rule in include_regex_rules)
        reasons.append(f'missing required regex include: {expected}')
```

> A bad-pattern `regex_include` rule passes through `_matches_regex` (returns `False`), but the `any(...)` over zero usable patterns combined with a present-but-broken rule would wrongly impose a requirement. `test_bad_pattern_fails_open_include` pins the desired behavior: a `regex_include` whose pattern is `[` must **not** drop. Guard the requirement so a rule with no *compilable* pattern does not count. Refine the block to:

```python
    include_regex_rules = [
        rule
        for rule in enabled_rules
        if rule.dimension in ('actor', 'tag') and rule.mode == 'regex_include'
        and _is_valid_regex(rule.value)
    ]
    # actor/tag parity: a regex_include rule is satisfied if it matches any tag
    # OR any actor name (the allow-list permits `actor` + `regex_include`, and
    # the requirement is "at least one tag/actor matches").
    _include_fields = list(detail.tags) + [a.name for a in detail.actors]
    if include_regex_rules and not any(
        _matches_regex(rule.value, field)
        for rule in include_regex_rules
        for field in _include_fields
    ):
        expected = ', '.join(_clean_value(rule.value) for rule in include_regex_rules)
        reasons.append(f'missing required regex include: {expected}')
```

and add the small validity helper next to `_matches_regex`:

```python
def _is_valid_regex(pattern: str) -> bool:
    pat = _clean_value(pattern)
    if not pat:
        return False
    try:
        re.compile(pat)
    except re.error:
        return False
    return True
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m pytest tests/unit/test_content_filter_regex.py -q`
Expected: PASS (9 passed).

- [ ] **Step 7: Run the existing engine suite to confirm no regression**

Run: `python3 -m pytest tests/unit/test_content_filter_engine.py tests/unit/test_content_filter_age.py -q`
Expected: PASS (no existing case regressed — the new branches are additive and gated on the new modes).

- [ ] **Step 8: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/spider/services/content_filter.py tests/unit/test_content_filter_regex.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(spider): add regex content-filter modes (ADR-040 WS4a)"
```

---

### Task 2: `_release_date_drop_reasons` helper + wiring (TDD)

**Files:**
- Modify: `javdb/spider/services/content_filter.py`
- Test: `tests/unit/test_content_filter_release_date.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_content_filter_release_date.py` (mirrors `test_content_filter_age.py`; the local `_Detail` carries a `release_date` field — note the engine reads `detail.release_date`, already present on the real `MovieDetail` at `javdb/parsing/models.py:117`):

```python
# tests/unit/test_content_filter_release_date.py
"""Release-date content-filter branch (ADR-040 WS4a / IMP-ADR040-03)."""

from dataclasses import dataclass, field

from javdb.spider.services.content_filter import Rule, evaluate


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    release_date: str = ""


def _date_rule(mode, value, rid=1, enabled=True):
    return Rule(id=rid, dimension="release_date", mode=mode, value=value, enabled=enabled)


def test_before_drops_on_or_after_bound():
    # 'before 2020-01-01' keeps only releases strictly before the bound.
    assert evaluate(_Detail(release_date="2020-01-01"), [_date_rule("before", "2020-01-01")]).keep is False
    assert evaluate(_Detail(release_date="2021-06-30"), [_date_rule("before", "2020-01-01")]).keep is False


def test_before_keeps_strictly_earlier():
    assert evaluate(_Detail(release_date="2019-12-31"), [_date_rule("before", "2020-01-01")]).keep is True


def test_after_drops_on_or_before_bound():
    assert evaluate(_Detail(release_date="2020-01-01"), [_date_rule("after", "2020-01-01")]).keep is False
    assert evaluate(_Detail(release_date="2019-01-01"), [_date_rule("after", "2020-01-01")]).keep is False


def test_after_keeps_strictly_later():
    assert evaluate(_Detail(release_date="2020-01-02"), [_date_rule("after", "2020-01-01")]).keep is True


def test_before_drop_reason_text():
    dec = evaluate(_Detail(release_date="2021-06-30"), [_date_rule("before", "2020-01-01")])
    assert any("before 2020-01-01" in r for r in dec.reasons)


def test_missing_release_date_never_drops():
    assert evaluate(_Detail(release_date=""), [_date_rule("after", "2020-01-01")]).keep is True


def test_unparseable_release_date_never_drops():
    assert evaluate(_Detail(release_date="not-a-date"), [_date_rule("before", "2020-01-01")]).keep is True


def test_unparseable_rule_value_ignored():
    assert evaluate(_Detail(release_date="2021-01-01"), [_date_rule("before", "bogus")]).keep is True


def test_release_date_with_time_suffix_is_truncated():
    # release_date may arrive as 'YYYY-MM-DD ...'; only the date part is compared.
    assert evaluate(_Detail(release_date="2019-12-31 12:00"), [_date_rule("before", "2020-01-01")]).keep is True


def test_disabled_release_date_rule_ignored():
    assert evaluate(_Detail(release_date="2021-01-01"), [_date_rule("before", "2020-01-01", enabled=False)]).keep is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/unit/test_content_filter_release_date.py -q`
Expected: FAIL — `evaluate()` has no `release_date` branch, so every drop case returns `keep=True`.

- [ ] **Step 3: Add the helper**

In `javdb/spider/services/content_filter.py`, add `from datetime import date` near the top (after `from dataclasses import dataclass`). Then add `_release_date_drop_reasons` immediately after `_age_drop_reasons` (it parallels the age helper: iterate `release_date` rules, parse the bound, `try/except` fail-open, append a reason). The `date.fromisoformat(raw[:10])` truncation matches how `actor_age.py:131-134` already parses `detail.release_date`:

```python
def _release_date_drop_reasons(rules: list[Rule], detail) -> list[str]:
    raw = _clean_value(getattr(detail, 'release_date', ''))
    try:
        actual = date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return []  # missing/unparseable release date never drops
    out: list[str] = []
    for rule in rules:
        if rule.dimension != 'release_date':
            continue
        try:
            bound = date.fromisoformat(_clean_value(rule.value)[:10])
        except (ValueError, TypeError):
            continue  # bad rule value is ignored (fail-open)
        if rule.mode == 'before' and actual >= bound:
            out.append(f'release date not before {bound.isoformat()}')
        elif rule.mode == 'after' and actual <= bound:
            out.append(f'release date not after {bound.isoformat()}')
    return out
```

- [ ] **Step 4: Wire it into `evaluate()`**

In `evaluate()`, find the line that extends `reasons` with the age drops:

```python
    reasons.extend(_age_drop_reasons(enabled_rules, actor_ages))
```

Add the release-date call immediately after it (both fold into the same `reasons` list, AND-ed with the rest — identical pattern to age):

```python
    reasons.extend(_release_date_drop_reasons(enabled_rules, detail))
```

> Note `_release_date_drop_reasons` takes `detail` (it reads `detail.release_date`), whereas `_age_drop_reasons` takes the pre-resolved `actor_ages` map — that is the entire "no resolver wiring" simplification. `evaluate()`'s signature is unchanged; `runner.py:775` already passes `movie_detail`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/unit/test_content_filter_release_date.py -q`
Expected: PASS (10 passed).

- [ ] **Step 6: Run the full engine suite**

Run: `python3 -m pytest tests/unit/test_content_filter_engine.py tests/unit/test_content_filter_age.py tests/unit/test_content_filter_regex.py tests/unit/test_content_filter_release_date.py -q`
Expected: PASS (all engine cases green; no regression).

- [ ] **Step 7: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/spider/services/content_filter.py tests/unit/test_content_filter_release_date.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(spider): add release_date content-filter mode (ADR-040 WS4a)"
```

---

## Phase B — CLI: new `(dimension, mode)` pairs + validation (TDD) [MAIN]

### Task 3: Extend the CLI allow-list tuples + `_validate_add`

**Files:**
- Modify: `apps/cli/ops/content_filter.py`
- Test: `tests/smoke/test_content_filter_cli.py`

- [ ] **Step 1: Write the failing CLI test cases**

In `tests/smoke/test_content_filter_cli.py`, add acceptance cases for the new modes and rejection cases for bad regex/date input. Append these functions (they reuse the existing `cli_conn` fixture for accept-path cases and the `monkeypatch` `_Repo`/`_fake_db` idiom from `test_add_age_rule*` for the captured-call cases):

```python
def test_add_regex_exclude_rule(cli_conn, capsys):
    assert content_filter.main([
        "add", "--dimension", "tag", "--mode", "regex_exclude", "--value", r"(?i)\bvr\b",
    ]) == 0
    assert content_filter.main(["list"]) == 0
    assert "regex_exclude" in capsys.readouterr().out


def test_add_actor_regex_include_rule(cli_conn, capsys):
    assert content_filter.main([
        "add", "--dimension", "actor", "--mode", "regex_include", "--value", r"Yua",
    ]) == 0
    assert content_filter.main(["list"]) == 0
    assert "regex_include" in capsys.readouterr().out


def test_add_release_date_before_rule(cli_conn, capsys):
    assert content_filter.main([
        "add", "--dimension", "release_date", "--mode", "before", "--value", "2020-01-01",
    ]) == 0
    assert content_filter.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "release_date\tbefore\t2020-01-01" in out


def test_add_release_date_after_rule(cli_conn, capsys):
    assert content_filter.main([
        "add", "--dimension", "release_date", "--mode", "after", "--value", "2021-12-31",
    ]) == 0


def test_add_regex_rule_rejects_bad_pattern(cli_conn, capsys):
    with pytest.raises(SystemExit) as exc:
        content_filter.main(["add", "--dimension", "tag", "--mode", "regex_exclude", "--value", "(unclosed"])
    assert exc.value.code == 2
    assert "valid regular expression" in capsys.readouterr().err


def test_add_release_date_rejects_bad_iso(cli_conn, capsys):
    with pytest.raises(SystemExit) as exc:
        content_filter.main(["add", "--dimension", "release_date", "--mode", "before", "--value", "2020/01/01"])
    assert exc.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_add_release_date_requires_value(cli_conn, capsys):
    with pytest.raises(SystemExit) as exc:
        content_filter.main(["add", "--dimension", "release_date", "--mode", "before"])
    assert exc.value.code == 2
    assert "require --value" in capsys.readouterr().err
```

Also extend the existing `test_content_filter_cli_rejects_invalid_rule_shapes` parametrize table with two cross-dimension rejection rows (regex modes are only valid on actor/tag; before/after only on release_date):

```python
        (
            ["add", "--dimension", "gender", "--mode", "regex_exclude", "--value", "x"],
            "do not support",
        ),
        (
            ["add", "--dimension", "tag", "--mode", "before", "--value", "2020-01-01"],
            "do not support",
        ),
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/smoke/test_content_filter_cli.py -q`
Expected: FAIL — `regex_exclude`/`regex_include`/`before`/`after` are not in `MODES` (argparse `choices` rejects them with "invalid choice") and `release_date` is not in `DIMENSIONS`.

- [ ] **Step 3: Extend the allow-list tuples**

In `apps/cli/ops/content_filter.py`, extend the four module-level tuples/sets (keep the existing entries; add the new ones):

```python
DIMENSIONS = ("actor", "tag", "gender", "age", "release_date")
MODES = (
    "exclude", "include", "require_lead", "exclude_all_male", "min_age", "max_age",
    "regex_exclude", "regex_include", "before", "after",
)
VALID_RULE_MODES = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
    ("gender", "exclude_all_male"),
    ("age", "min_age"),
    ("age", "max_age"),
    ("actor", "regex_exclude"),
    ("actor", "regex_include"),
    ("tag", "regex_exclude"),
    ("tag", "regex_include"),
    ("release_date", "before"),
    ("release_date", "after"),
}
VALUE_REQUIRED = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
    ("age", "min_age"),
    ("age", "max_age"),
    ("actor", "regex_exclude"),
    ("actor", "regex_include"),
    ("tag", "regex_exclude"),
    ("tag", "regex_include"),
    ("release_date", "before"),
    ("release_date", "after"),
}
```

- [ ] **Step 4: Add regex-compile + ISO-date validation to `_validate_add`**

In `_validate_add`, add two new `elif` branches. The current tail is:

```python
    elif args.dimension == "age":
        if not value.isdigit():
            parser.error("age rules require --value to be a non-negative integer")
        args.value = str(int(value))
    else:
        args.value = value
```

Replace it with (the regex branch compiles to validate then keeps the verbatim pattern; the date branch parses ISO then re-serializes the canonical `YYYY-MM-DD`, mirroring the age `str(int(value))` normalization):

```python
    elif args.dimension == "age":
        if not value.isdigit():
            parser.error("age rules require --value to be a non-negative integer")
        args.value = str(int(value))
    elif args.mode in ("regex_exclude", "regex_include"):
        import re

        try:
            re.compile(value)
        except re.error as exc:
            parser.error(f"--value must be a valid regular expression: {exc}")
        args.value = value
    elif args.dimension == "release_date":
        from datetime import date

        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parser.error("release_date rules require --value as an ISO date (YYYY-MM-DD)")
        args.value = parsed.isoformat()
    else:
        args.value = value
```

> Order matters: the `regex_*` branch is keyed on `args.mode` (so it catches both `actor` and `tag` regex rules) and is placed **before** the `release_date` branch (keyed on `args.dimension`) and the `else`. The `("gender", "require_lead")` / `("gender", "exclude_all_male")` / `age` branches above are untouched and still take precedence for their dimensions.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/smoke/test_content_filter_cli.py -q`
Expected: PASS (all existing CLI cases + the new regex/date acceptance and rejection cases green).

- [ ] **Step 6: Manual CLI smoke (real REPORTS_DB / SQLite)**

This exercises the full add → list path against the real reports DB (no migration needed — the table already exists from Phase 1):

```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
python3 -m apps.cli.ops.content_filter add --dimension tag --mode regex_exclude --value '(?i)\bvr\b'
python3 -m apps.cli.ops.content_filter add --dimension release_date --mode before --value 2020-01-01
python3 -m apps.cli.ops.content_filter list
```

Expected: two `Added content filter rule <id>.` lines, then a `list` table containing a `tag	regex_exclude	(?i)\bvr\b	yes` row and a `release_date	before	2020-01-01	yes` row. (Clean up afterward with `remove --id <id>` for each, or leave them if intended.)

- [ ] **Step 7: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/cli/ops/content_filter.py tests/smoke/test_content_filter_cli.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(cli): add regex/release_date content-filter rule validation (ADR-040 WS4a)"
```

---

## Phase C — Doc-hygiene: backfill both stale DDL header comments [MAIN]

Both `ContentFilterRule` DDL header comments are pre-Phase-2 stale (they say `dimension: actor | tag | gender`, omitting `age`/`min_age`/`max_age` shipped in Phase 2). This IMP edits these comments to document regex/release-date anyway, so it is the natural moment to backfill age too. **Comment-only — no DDL or behavior change.**

### Task 4: Backfill the D1 migration header comment

**Files:**
- Modify: `javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql`

- [ ] **Step 1: Update the comment block (lines 7-10)**

Replace:

```sql
-- Dynamic content-filter rules applied after detail parse. Additive: no rows = no change.
-- dimension: actor | tag | gender
-- mode: exclude | include | require_lead | exclude_all_male
-- value: actor name/href | tag | gender value
```

with:

```sql
-- Dynamic content-filter rules applied after detail parse. Additive: no rows = no change.
-- The (dimension, mode, value) triple is generic; new dimensions/modes reuse it
-- without a schema change (age = ADR-040 Phase 2; regex/release_date = WS4a).
-- dimension: actor | tag | gender | age | release_date
-- mode: exclude | include | require_lead | exclude_all_male | min_age | max_age
--       | regex_exclude | regex_include | before | after
-- value: actor name/href | tag | gender | integer age | ISO date (YYYY-MM-DD)
--        | regex pattern
```

- [ ] **Step 2: Verify the Write-Class CI check still passes**

The file's `-- Write-Class:` header is unchanged, but re-run the validator to confirm the comment edit did not disturb it:

Run: `python3 scripts/ci/validate_d1_write_class.py javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql`
Expected: exits 0 / prints OK. (If the script takes no args, run it with no args and confirm it does not flag this file.)

> Note: this migration file predates the `-- Write-Class: authoritative` convention and may not carry that header. If the validator reports it as legacy/grandfathered, that is unchanged by this comment-only edit — do **not** add a Write-Class header here (that would be a separate, out-of-scope change). The check must simply not newly fail because of this edit.

### Task 5: Backfill the `_REPORTS_DDL` mirror comment

**Files:**
- Modify: `javdb/storage/db/_db_migrations.py`

- [ ] **Step 1: Update the mirror comment (around lines 233-238)**

In the `_REPORTS_DDL` triple-quoted literal, replace:

```sql
-- Dynamic content-filter rules (ADR-040 Phase 1).  Rules live in the
-- reports DB and are applied after detail parse; no rows means no
-- behavior change.
-- dimension: actor | tag | gender
-- mode: exclude | include | require_lead | exclude_all_male
-- value: actor name/href | tag | gender value
```

with:

```sql
-- Dynamic content-filter rules (ADR-040).  Rules live in the reports DB
-- and are applied after detail parse; no rows means no behavior change.
-- The (dimension, mode, value) triple is generic; new dimensions/modes
-- reuse it without a schema change (age = Phase 2; regex/release_date = WS4a).
-- dimension: actor | tag | gender | age | release_date
-- mode: exclude | include | require_lead | exclude_all_male | min_age | max_age
--       | regex_exclude | regex_include | before | after
-- value: actor name/href | tag | gender | integer age | ISO date (YYYY-MM-DD)
--        | regex pattern
```

- [ ] **Step 2: Verify `_REPORTS_DDL` still parses and creates the table**

Run: `python3 -c "import sqlite3; from javdb.storage.db import _db_migrations as m; c=sqlite3.connect(':memory:'); c.executescript(m._REPORTS_DDL); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='ContentFilterRule'\")])"`
Expected: prints `['ContentFilterRule']` (the comment edit did not break the embedded DDL — SQL `--` comments are inert).

- [ ] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql javdb/storage/db/_db_migrations.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "docs(db): backfill ContentFilterRule DDL header comments (ADR-040 WS4a)"
```

---

## Phase D — ADR-040 Status Log + roadmap amendment (bilingual) [MAIN]

ADR-040's Status Log and roadmap describe Phases 1-5 but predate the regex/release-date extension. Record it as a documented amendment, in both `.md` and `.zh.md` (per the bilingual-ADR rule).

### Task 6: Amend `ADR-040-content-filter-rules.md`

**Files:**
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md`

- [ ] **Step 1: Append a Status Log entry**

After the last Status Log bullet (`- 2026-06-07: Phase 2 implemented ...`), append:

```markdown
- 2026-06-14: Content-filter engine extended with **regex** (`regex_exclude` /
  `regex_include` on actor/tag) and **release-date** (`release_date` dimension,
  `before` / `after`) modes via [IMP-ADR040-03](IMP-ADR040-03-content-filter-regex-date.md).
  Both reuse the generic `(dimension, mode, value)` triple with **no schema
  migration** — the Phase-2 (age) no-migration template. Engine + CLI only
  ([MAIN]); the dual-backend web CRUD `/api/content-filter` + SPA Settings/overlay
  surface remain [IMP-ADR040-04](IMP-ADR040-04-content-filter-web-crud.md). ADR stays
  active for Phases 4-5.
```

- [ ] **Step 2: Amend the roadmap table**

In the `## Implementation Roadmap` table, insert a row immediately after the Phase 2 row (the regex/release-date extension is a sub-phase of the existing engine, not a renumber of Phases 3-5):

```markdown
| Phase 2b — Regex + release-date | IMP-ADR040-03 (done) | `regex_exclude`/`regex_include` (actor/tag); `release_date` `before`/`after`; no schema migration (reuses the generic triple) |
```

- [ ] **Step 3: Add the new modes to Domain Language**

In `## Domain Language (additions for CONTEXT.md)`, extend the **Content filter rule** bullet so the dimension/mode list is current:

Replace:

```markdown
- **Content filter rule** — a row in `ContentFilterRule`: a dimension (actor/tag/
  gender), a mode (exclude/include/…), and a value.
```

with:

```markdown
- **Content filter rule** — a row in `ContentFilterRule`: a dimension (actor/tag/
  gender/age/release_date), a mode (exclude/include/regex_exclude/regex_include/
  require_lead/exclude_all_male/min_age/max_age/before/after), and a value.
- **Regex rule** — a `regex_exclude`/`regex_include` rule whose value is a Python
  `re.search` pattern; a bad pattern fails open (never drops, never raises).
- **Release-date rule** — a `release_date` `before`/`after` rule comparing the
  movie's parsed `release_date` to an ISO bound; an absent/unparseable date never
  drops.
```

### Task 7: Mirror the amendment into `ADR-040-content-filter-rules.zh.md`

**Files:**
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md`

- [ ] **Step 1: Append the Status Log entry (Chinese)**

After the last `状态日志 (Status Log)` bullet (`- 2026-06-07：Phase 2 经 ...`), append:

```markdown
- 2026-06-14：内容过滤引擎新增 **正则**（actor/tag 上的 `regex_exclude` /
  `regex_include`）与 **上映日期**（`release_date` 维度，`before` / `after`）模式，
  见 [IMP-ADR040-03](IMP-ADR040-03-content-filter-regex-date.md)。两者复用通用的
  `(dimension, mode, value)` 三元组，**无需 schema 迁移**——沿用 Phase 2（age）的
  免迁移模板。仅引擎 + CLI（[MAIN]）；双后端 web CRUD `/api/content-filter` 与
  SPA 设置/叠加界面仍归 [IMP-ADR040-04](IMP-ADR040-04-content-filter-web-crud.md)。
  ADR 对 Phase 4-5 仍然有效。
```

- [ ] **Step 2: Amend the roadmap table (Chinese)**

Insert after the Phase 2 row in the 路线图 table:

```markdown
| Phase 2b — 正则 + 上映日期 | IMP-ADR040-03 (done) | `regex_exclude`/`regex_include`（actor/tag）；`release_date` 的 `before`/`after`；无 schema 迁移（复用通用三元组） |
```

- [ ] **Step 3: Update Domain Language (Chinese)**

Replace the `Content filter rule（内容过滤规则）` bullet under `## 领域语言 (CONTEXT.md 待补充项)`:

```markdown
- **Content filter rule（内容过滤规则）**——`ContentFilterRule` 中一行:一个维度
  （actor/tag/gender/age/release_date）、一个 mode（exclude/include/regex_exclude/
  regex_include/require_lead/exclude_all_male/min_age/max_age/before/after）、一个 value。
- **Regex rule（正则规则）**——value 为 Python `re.search` 模式的 `regex_exclude`/
  `regex_include` 规则；坏模式 fail-open（不丢弃、不抛错）。
- **Release-date rule（上映日期规则）**——`release_date` 的 `before`/`after` 规则,
  将影片已解析的 `release_date` 与一个 ISO 边界比较;日期缺失/不可解析时永不丢弃。
```

- [ ] **Step 4: Commit (both ADR files together)**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "docs(adr): record regex/release-date content-filter phase (ADR-040 WS4a)"
```

> The IMP file itself (`IMP-ADR040-03-content-filter-regex-date.md`) lives in the same folder; commit it alongside Phase D (or as a leading `docs(adr): add IMP-ADR040-03` commit). It links the parent ADR by filename per the same-folder cross-link convention.

---

## Final verification gate

- [ ] **[MAIN] full engine + CLI suite**

Run:
```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
python3 -m pytest \
  tests/unit/test_content_filter_engine.py \
  tests/unit/test_content_filter_age.py \
  tests/unit/test_content_filter_regex.py \
  tests/unit/test_content_filter_release_date.py \
  tests/smoke/test_content_filter_cli.py -q
```
Expected: all pass (new regex + release-date engine cases, all existing engine/age cases, all CLI cases including the new accept/reject pairs).

- [ ] **[MAIN] DDL still parses (comment-only edits inert)**

Run: `python3 -c "import sqlite3; from javdb.storage.db import _db_migrations as m; c=sqlite3.connect(':memory:'); c.executescript(m._REPORTS_DDL); print('REPORTS_DDL ok')"`
Expected: `REPORTS_DDL ok`.

- [ ] **[MAIN] no accidental web/API/migration touch**

Run: `git -C /Users/tedwu/JAVDB_AutoSpider_CICD diff --name-only main...HEAD`
Expected: only the files in the File Structure list — no `server/`, no `src/`, no `docs/api/openapi.json`, no new `javdb/migrations/d1/*.sql` (the SQL file appears only as a comment edit). If any of those show up, a step over-reached.

- [ ] **Manual ingestion-path smoke (optional, requires a real run):** add a `tag regex_exclude '(?i)\bvr\b'` rule via the CLI, run a dry-run spider against a page known to contain a VR title, confirm the title is dropped with reason `excluded by tag rule: (?i)\bvr\b` in the filter stats (`runner.py` emits a `content_filtered` event). Remove the rule afterward.

> **No migration to apply.** `ContentFilterRule` already exists in remote D1 (`javdb-reports`) from Phase 1; regex/release-date add no columns. There is **no** `wrangler d1 execute` / `sync_d1_to_sqlite` deploy step in this IMP.

---

## Coverage check (self-review)

- **Regex modes (`regex_exclude` / `regex_include`)** on actor/tag → `_matches_regex` + `_is_valid_regex` (Task 1), wired into `_matches_exclude_rule` and the include path; CLI allow-list + compile validation (Task 3). Drop / keep / **bad-pattern fail-open** (both exclude and include) pinned in `test_content_filter_regex.py`.
- **Release-date mode (`release_date` `before` / `after`)** → `_release_date_drop_reasons` paralleling `_age_drop_reasons` (Task 2), folded into `evaluate()`'s `reasons` with **no resolver wiring** (reads `detail.release_date` directly); CLI allow-list + ISO-date validation (Task 3). Drop / keep / **missing-date no-drop** / **unparseable no-drop** / value-truncation pinned in `test_content_filter_release_date.py`.
- **No schema migration** → Phases A-B add only engine branches + CLI tuples; the only SQL-file touch is the comment backfill (Phase C). Verified by the `git diff --name-only` gate.
- **Stale-comment backfill** (age + regex/date) in **both** DDL mirrors → Tasks 4-5, comment-only, DDL-parse-verified.
- **ADR-040 Status Log + roadmap + Domain Language**, bilingual `.md` + `.zh.md` in the same commit → Tasks 6-7.
- **Out of scope held:** no `evaluate()` signature change, no `runner.py` change, no capability flag, no `/api/content-filter` router/route, no `openapi.json`, no `server/`/`src/` files, no catastrophic-backtracking guard (deferred to the web-authored boundary in IMP-ADR040-04) — none introduced.

---

The complete IMP markdown is above, ready to save as `/Users/tedwu/JAVDB_AutoSpider_CICD/docs/design/ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md`.

Key grounding notes for the caller:
- **Engine** (`javdb/spider/services/content_filter.py`): `_matches_regex`/`_is_valid_regex` use `re.search` with `re.error` fail-open (analogue of the age `int()` guard at `:120-123`); `_release_date_drop_reasons` mirrors `_age_drop_reasons` but takes `detail` (reads `detail.release_date`) instead of `actor_ages` — `evaluate()`'s signature is unchanged and the `runner.py:775` call site (`evaluate(movie_detail, content_filter_rules, actor_ages)`) needs no edit.
- **Release-date parsing** reuses the `date.fromisoformat(raw[:10])` truncation idiom already in `actor_age.py:131-134`.
- **CLI** (`apps/cli/ops/content_filter.py:14-32`): extends `DIMENSIONS`/`MODES`/`VALID_RULE_MODES`/`VALUE_REQUIRED`; `_validate_add` gets a `regex_*` (keyed on `args.mode`, before the `release_date` branch) and a `release_date` ISO branch, both mirroring the age `isdigit`/`str(int())` normalize-and-reject shape.
- **Tests** follow real precedents: engine tests model `tests/unit/test_content_filter_age.py` (local dataclasses, but the release-date `_Detail` adds the `release_date` field the age version lacked); CLI tests extend the existing `tests/smoke/test_content_filter_cli.py` (the `cli_conn` fixture + the `test_add_age_rule*` `_Repo`/`_fake_db` pattern + the parametrized rejection table).
- **MAIN-only**: no web surface, no `ContentFilterRule` migration (the table predates this work in `javdb-reports`); both stale DDL header comments (`.sql:7-10` and `_db_migrations.py:236-238`) get a comment-only age+regex/date backfill.