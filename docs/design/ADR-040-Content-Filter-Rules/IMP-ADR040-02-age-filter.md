# IMP-ADR040-02: Age Filter via External Actor-Age Enrichment (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-040](ADR-040-content-filter-rules.md) (umbrella) — this is **Phase 2** of the (now re-numbered) roadmap. Builds directly on [IMP-ADR040-01](IMP-ADR040-01-content-filter.md) (the deterministic content-filter engine).

**Status:** Implemented (PR #180 open).

**Goal:** Add an `age` dimension to the content-filter engine that drops a parsed movie when any actor's age (at the movie's release date) is outside an operator-configured bound — resolving each actor's birthdate **best-effort** from **minnano-av** (keyed by actor name), cached in a new `ActorMetadata` D1 table, and applied only when an `age` rule exists.

**Architecture:** A pure `ActorAgeResolver` turns a parsed `MovieDetail` into a `{actor_href: age}` map, computing age **as of the movie's `release_date`** (fallback: the run date). Per actor it is **cache-first**: read `ActorMetadata` (history D1); on a miss it queries one external source (`MinnanoAvSource`) — a pluggable adapter that searches the site by actor name, fetches the matched profile, and parses a birthdate; the result (a date or a negative cache) is written back to `ActorMetadata`. The runner builds the resolver **only when an `age` rule is active** (zero network cost otherwise), computes ages, and passes them into the existing `evaluate(detail, rules, actor_ages)`. Production fetches go through a **proxied, throttled** gateway (`create_gateway(use_cf_bypass=False)` + a politeness delay). Unknown ages never drop a movie (best-effort, fail-open — consistent with IMP-01).

**Tech Stack:** Python 3, `create_gateway` (proxy-aware fetch, CF-bypass off for external hosts), `bs4.BeautifulSoup('html.parser')` (parsing, same as the pure-Python fallback parsers), `sqlite3`/D1 via `get_db`, `dataclasses`, `pytest`, `wrangler`.

---

## Design corrections vs ADR-040 (read first)

ADR-040 §Context and §D5 assumed actor **age is obtainable from "an actor-profile lookup"**, implying javdb's own `/actors/<id>` page carries a birthdate. **That assumption is wrong and is reversed by this IMP:**

- **javdb `/actors/<id>` pages are movie *listing* pages and do NOT show a birthdate.** (Confirmed with the operator; corroborated by the codebase — javdb actor URLs are parsed as index pages, see `tests/smoke/test_spider_app_main.py`.)
- Therefore Phase-2 age filtering uses an **external data source** (minnano-av) matched **by actor name**, not a javdb lookup. (xslist was considered as a fallback but **deferred**: javdb yields Japanese actor names, which a romanized English library like xslist almost never matches exactly — so it would add a second parser for near-zero extra coverage. The resolver keeps a pluggable adapter chain so a romaji-bridged source can be added later.)
- Consequence: age filtering is **best-effort** — when minnano-av yields no birthdate for an actor, that actor has *unknown* age and **never causes a drop**. Coverage is partial (name-collision, alternate names, missing entries). This matches IMP-01's additive/fail-open philosophy.

**Task 1 amends ADR-040 (`.md` + `.zh.md`) to record this reversal** before any code lands, per the project's "Design feedback loop" rule.

**Re-numbered roadmap** (Task 1 writes this into the ADR):

| Phase | IMP | Ships |
| --- | --- | --- |
| Phase 1 — Exclude + attribute | IMP-ADR040-01 (done) | actor/tag/gender rules |
| **Phase 2 — Age filter** | **IMP-ADR040-02 (this plan)** | `age` dimension; external-source enrichment (minnano-av; xslist deferred); `ActorMetadata` cache |
| Phase 3 — Subscriptions | IMP-ADR040-03 (stub) | whitelist bypassing the rating threshold (needs an index-gate-bypass design) |
| Phase 4 — Web/MCP rule mgmt | IMP-ADR040-04 (stub) | REST CRUD over rules (web buildable now; MCP blocked on ADR-038) |
| Phase 5 — Compose (optional) | IMP-ADR040-05 (stub) | combine with the ADR-025 preference score |

---

## Storage placement

`ActorMetadata` lives in the **history** logical DB (`javdb-history`), alongside `MovieMetadata` — both are direct-UPSERT enrichment tables written outside the Pending→Commit session flow. (`ContentFilterRule` from IMP-01 stays in **reports**; only the *rules* are reports, the *actor cache* is history enrichment.)

## Age semantics (decided)

- **Reference = age at the movie's `release_date`.** The resolver parses `detail.release_date` (ISO `YYYY-MM-DD`); if it is missing or unparseable it falls back to the **run date**. This answers "was the performer of-age *in this film*" rather than "how old are they now" — correct for both daily new releases and ad-hoc back-catalog runs.
- **`min_age N`** → drop if **any** actor with a *known* age is `< N`.
- **`max_age N`** → drop if **any** actor with a *known* age is `> N`.
- Age rules are **attribute filters** (AND group, like gender) — they accumulate reasons; they do NOT have blacklist precedence.
- Unknown-age actors are skipped (never drop). Empty `actor_ages` → age rules are no-ops.

## Naming contract (verbatim — use these exact names across tasks)

- `compute_age(birthdate: str, reference: date) -> Optional[int]` — `birthdate` ISO `YYYY-MM-DD`; whole years at `reference`, or `None` if unparseable/negative.
- `ResolvedAge(birthdate: str, source: str, source_url: str)` — frozen dataclass; a successful source hit.
- `MinnanoAvSource(fetch)` — has `name: str` and `lookup(actor_name: str) -> Optional[ResolvedAge]`; `fetch: Callable[[str], Optional[str]]` (injected — the resolver wires the production fetch, tests inject fakes).
- `parse_minnano_search(html) -> list[tuple[str, str]]`, `parse_minnano_birthdate(html) -> Optional[str]` — pure.
- `ActorMetadataRepo(conn)` — `get(actor_href) -> Optional[dict]`, `upsert(actor_href, actor_name, birthdate, source, source_url) -> None` (sets `resolved=1`), `list_all() -> list[dict]`, `delete(actor_href) -> None`.
- `ActorAgeResolver(*, sources, today, cache=None)` — `ages_for(detail) -> dict[str, int]` (keys = normalized actor href; `today` is the **fallback** reference when a movie has no parseable `release_date`).
- `_ThrottledGatewayFetch(min_interval=...)` — production fetch callable: one proxied `create_gateway(use_cf_bypass=False)` + a min-interval throttle.
- `build_default_resolver(today=None) -> ActorAgeResolver` — factory wiring real cache + `[MinnanoAvSource(_ThrottledGatewayFetch())]`.
- Engine: `evaluate(detail, rules, actor_ages: Optional[Mapping[str, int]] = None) -> FilterDecision` (the only signature change; `actor_ages=None` keeps Phase-1 behavior).

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md` (+ `.zh.md`) | Modify | Reverse the age assumption; re-number roadmap; Status Log |
| `javdb/migrations/d1/2026_06_04_add_actor_metadata_table.sql` | Create | `ActorMetadata` DDL (history DB) |
| `javdb/storage/repos/actor_metadata_repo.py` | Create | `ActorMetadataRepo` (cache get/upsert/list/delete) |
| `javdb/spider/services/actor_age_sources.py` | Create | `ResolvedAge`, `MinnanoAvSource`, pure minnano-av parsers |
| `javdb/spider/services/actor_age.py` | Create | `compute_age`, `ActorAgeResolver`, `_DbActorAgeCache`, `_ThrottledGatewayFetch`, `build_default_resolver` |
| `javdb/spider/services/content_filter.py` | Modify | Add `actor_ages` param + `age` dimension to `evaluate()` |
| `javdb/spider/detail/runner.py` | Modify | Opt-in resolver build (after L471) + compute ages before `evaluate` (L722) |
| `apps/cli/ops/content_filter.py` | Modify | Add `age` dimension + `min_age`/`max_age` modes + validation |
| `apps/cli/ops/actor_age.py` | Create | CLI: inspect/refresh the `ActorMetadata` cache |
| `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Terms + CLI docs |
| `tests/unit/test_compute_age.py`, `test_actor_metadata_repo.py`, `test_actor_age_sources.py`, `test_actor_age_resolver.py`, `test_content_filter_age.py`, `test_content_filter_age_wiring.py` | Create | Unit tests |
| `tests/smoke/test_actor_age_cli.py` | Create | CLI smoke |

---

## Task 1: Amend ADR-040 (record the reversed assumption)

**Files:**
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md`
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md`

- [ ] **Step 1: Correct §D5 (and the §Context aside) in the `.md`**

In `ADR-040-content-filter-rules.md`, replace the `**Age is deferred (Phase 2)**` sentence inside **D5** with:

```markdown
**Age is Phase 2 (IMP-ADR040-02).** Correction to the original Context aside:
javdb's own `/actors/<id>` page is a movie *listing* page and carries **no
birthdate**, so age cannot come from a javdb lookup. Phase 2 instead resolves
birthdates **best-effort from minnano-av** (matched by actor name), cached in
`ActorMetadata`, computing age at the movie's release date. Actors with no
resolved birthdate have unknown age and never cause a drop. (xslist was weighed as
a fallback but deferred — it cannot match javdb's Japanese names.)
```

- [ ] **Step 2: Replace the Implementation Roadmap table** in the `.md` with the re-numbered five-phase table from the "Design corrections" section above (Phase 2 = age; Subscriptions → Phase 3; Web/MCP → Phase 4; Compose → Phase 5).

- [ ] **Step 3: Append a Status Log entry** in the `.md`:

```markdown
- 2026-06-04: Phase-2 scope corrected — javdb actor pages have no birthdate; age
  filtering uses minnano-av (best-effort, by name), age computed at release date.
  Roadmap re-numbered (age=Phase 2; subscriptions=Phase 3; web/MCP=Phase 4).
  Planned in [IMP-ADR040-02](IMP-ADR040-02-age-filter.md).
```

- [ ] **Step 4: Mirror all three edits into `ADR-040-content-filter-rules.zh.md`** (translate prose; keep table cell code/paths verbatim). Translation drift is a defect.

- [ ] **Step 5: Commit**

```bash
git add docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md \
        docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md
git commit -m "docs(adr-040): correct age source (external minnano-av, not javdb) + renumber roadmap"
```

---

## Task 2: Spike — confirm minnano-av URL + birthdate markup

**Goal:** Ground the parser (Task 6) in the *real* current markup of minnano-av, and confirm it is reachable through the production fetch path. No production code is written here; the output is confirmed URL templates + selectors. (No live HTML is committed — Task 6 tests use small synthetic fixtures shaped like what you confirm here.)

- [ ] **Step 1: Probe the markup** (plain `requests`, no config needed):

```bash
python3 - <<'PY'
import re, requests
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup

NAME = "<a JAV actress name you know minnano-av lists>"  # e.g. one from your MovieMetadata
UA = {"User-Agent": "Mozilla/5.0"}

def get(u):
    r = requests.get(u, headers=UA, timeout=15)
    print("GET", r.status_code, len(r.text), u)
    return r.text if r.status_code == 200 else ""

mh = get(f"https://www.minnano-av.com/search_result.php?search_scope=actress&search={quote(NAME)}")
ms = BeautifulSoup(mh, "html.parser")
mhits = [(a.get_text(strip=True), a.get("href")) for a in ms.select('a[href*="actress.php"]')][:5]
print("minnano hits:", mhits)
if mhits:
    purl = urljoin("https://www.minnano-av.com/", mhits[0][1])
    pt = get(purl)
    m = re.search(r'生年月日[^\d]*(\d{4})\D+(\d{1,2})\D+(\d{1,2})',
                  BeautifulSoup(pt, "html.parser").get_text(" ", strip=True))
    print("minnano birthdate match:", m.groups() if m else None)
PY
```

- [ ] **Step 2: Confirm the production fetch path reaches minnano-av** (proxied gateway, CF-bypass off — needs the main-repo `config.py`, so run with `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD`):

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD python3 - <<'PY'
from javdb.spider.spider_gateway import create_gateway
gw = create_gateway(use_proxy=False, use_cf_bypass=False, use_cookie=False)
html = gw.fetch_html("https://www.minnano-av.com/")
print("gateway fetch ok:", html is not None, "len:", len(html or ""))
PY
```

- [ ] **Step 3: Record findings & decision gate.**

  **Decision gate: PASS** — minnano-av was reachable directly and through the production gateway (which follows redirects). The shipped parser (`javdb/spider/services/actor_age_sources.py`) was grounded by this spike. Corrected facts, differing from the pre-spike assumptions:

  - **Search URL param is `search_word`** (not `search`): `https://www.minnano-av.com/search_result.php?search_scope=actress&search_word={q}`.
  - **A single confident match 30x-redirects straight to the profile page** `actress<ID>.html` (the redirect-response IS the profile HTML; minnano's matching is alias-aware). So the fetched URL is the profile directly — no need to parse a results page for it.
  - **Multiple matches stay on a results list** whose profile links use `actress<ID>.html` hrefs (NOT `actress.php?…`, which is video pagination; NOT `ranking_actress.php`).
  - **Birthdate markup:** `生年月日 YYYY年MM月DD日`. `BeautifulSoup.get_text()` excludes `<meta>` attributes, so the meta `"生年月日"` label (if any) never false-matches the regex.

  The shipped implementation uses `search_word=`, an `actress\d+\.html` profile-link selector, and a redirect-first `lookup` (read birthdate directly from a single-match redirect; fall back to a strict suffix-stripped exact-name match on a multi-match results list). See Task 6 note below.

> No commit in this task (exploration only). Findings carry into Task 6.

---

## Task 3: `ActorMetadata` D1 migration

**Files:**
- Create: `javdb/migrations/d1/2026_06_04_add_actor_metadata_table.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-06-04: Add ActorMetadata table (ADR-040 Phase 2 / IMP-ADR040-02).
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_04_add_actor_metadata_table.sql
--
-- Best-effort actor-age enrichment cache. Birthdates are resolved from minnano-av
-- keyed by the normalized javdb actor href. A row with resolved=1 and birthdate
-- NULL is a NEGATIVE cache (looked up, not found) so the pipeline does not
-- re-query the external site every run. Additive: no rows = no behavior change.

CREATE TABLE IF NOT EXISTS ActorMetadata (
  actor_href  TEXT PRIMARY KEY,   -- normalized javdb /actors/<id> path
  actor_name  TEXT,               -- javdb display name at resolve time (reference)
  birthdate   TEXT,               -- ISO 'YYYY-MM-DD', or NULL when unknown
  source      TEXT,               -- 'minnano-av' | '' (which adapter resolved it)
  source_url  TEXT,               -- matched external profile URL (audit)
  resolved    INTEGER NOT NULL DEFAULT 0,  -- 1 once a lookup was attempted
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

- [ ] **Step 2: Apply to D1, re-align SQLite** (D1 is canonical; SQLite mirror is rebuilt from D1)

Run:
```bash
wrangler d1 execute javdb-history --remote \
  --file=javdb/migrations/d1/2026_06_04_add_actor_metadata_table.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: statement executes; local `reports/history.db` rebuilt to include `ActorMetadata`; exit 0.

- [ ] **Step 3: Verify the table exists locally**

Run:
```bash
python3 -c "import sqlite3,glob; p=glob.glob('reports/history.db')[0]; print(sqlite3.connect(p).execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='ActorMetadata'\").fetchone())"
```
Expected: `('ActorMetadata',)`

- [ ] **Step 4: Update any D1↔SQLite table-list guard, if one exists**

Run:
```bash
grep -rn "MovieMetadata" tests/ scripts/ | grep -iE "expected|parity|table" | head
```
If a hardcoded "expected tables" list includes `MovieMetadata`, add `ActorMetadata` next to it. If the grep returns nothing, no guard exists — skip.

- [ ] **Step 5: Commit**

```bash
git add javdb/migrations/d1/2026_06_04_add_actor_metadata_table.sql
git commit -m "feat(db): add ActorMetadata enrichment table (ADR-040 Phase 2)"
```

---

## Task 4: `ActorMetadataRepo`

**Files:**
- Create: `javdb/storage/repos/actor_metadata_repo.py`
- Test: `tests/unit/test_actor_metadata_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_actor_metadata_repo.py
import sqlite3

import pytest

from javdb.storage.repos.actor_metadata_repo import ActorMetadataRepo

_DDL = """
CREATE TABLE ActorMetadata (
  actor_href TEXT PRIMARY KEY, actor_name TEXT, birthdate TEXT, source TEXT,
  source_url TEXT, resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
"""


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return ActorMetadataRepo(conn)


def test_get_missing_returns_none(repo):
    assert repo.get("/actors/x") is None


def test_upsert_then_get(repo):
    repo.upsert("/actors/x", "Some Name", "1990-05-20", "minnano-av", "https://m/x")
    row = repo.get("/actors/x")
    assert row["birthdate"] == "1990-05-20"
    assert row["source"] == "minnano-av"
    assert int(row["resolved"]) == 1


def test_upsert_negative_cache(repo):
    repo.upsert("/actors/y", "Unknown", None, "", "")
    row = repo.get("/actors/y")
    assert row["birthdate"] is None
    assert int(row["resolved"]) == 1  # looked up, not found


def test_upsert_overwrites(repo):
    repo.upsert("/actors/x", "N", None, "", "")
    repo.upsert("/actors/x", "N", "1988-01-02", "minnano-av", "https://m/1")
    assert repo.get("/actors/x")["birthdate"] == "1988-01-02"


def test_delete(repo):
    repo.upsert("/actors/x", "N", "1990-01-01", "minnano-av", "u")
    repo.delete("/actors/x")
    assert repo.get("/actors/x") is None
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/unit/test_actor_metadata_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the repo**

```python
# javdb/storage/repos/actor_metadata_repo.py
"""Repository for ActorMetadata rows (history DB) — ADR-040 Phase 2 age cache."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO ActorMetadata
    (actor_href, actor_name, birthdate, source, source_url, resolved,
     created_at, updated_at)
VALUES
    (?, ?, ?, ?, ?, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ','now'),
     strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(actor_href) DO UPDATE SET
    actor_name = excluded.actor_name,
    birthdate  = excluded.birthdate,
    source     = excluded.source,
    source_url = excluded.source_url,
    resolved   = 1,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
"""


class ActorMetadataRepo:
    """Thin typed wrapper over ActorMetadata. Takes a live connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def get(self, actor_href: str) -> dict | None:
        row = self._conn.execute(
            "SELECT actor_href, actor_name, birthdate, source, source_url, resolved "
            "FROM ActorMetadata WHERE actor_href = ?",
            (actor_href,),
        ).fetchone()
        return dict(row) if row is not None else None

    def upsert(self, actor_href: str, actor_name: str, birthdate: str | None,
               source: str, source_url: str) -> None:
        self._conn.execute(
            _UPSERT_SQL, (actor_href, actor_name, birthdate, source, source_url)
        )

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT actor_href, actor_name, birthdate, source, source_url, resolved "
            "FROM ActorMetadata ORDER BY actor_href"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, actor_href: str) -> None:
        self._conn.execute(
            "DELETE FROM ActorMetadata WHERE actor_href = ?", (actor_href,)
        )
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/unit/test_actor_metadata_repo.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/actor_metadata_repo.py tests/unit/test_actor_metadata_repo.py
git commit -m "feat(db): add ActorMetadataRepo (ADR-040 Phase 2)"
```

---

## Task 5: `compute_age` (pure)

**Files:**
- Create: `javdb/spider/services/actor_age.py` (first slice — just `compute_age`)
- Test: `tests/unit/test_compute_age.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_compute_age.py
from datetime import date

from javdb.spider.services.actor_age import compute_age


def test_basic_age():
    assert compute_age("1990-05-20", date(2026, 6, 4)) == 36


def test_birthday_not_yet_reached_at_reference():
    assert compute_age("1990-12-31", date(2026, 6, 4)) == 35


def test_birthday_on_reference_day():
    assert compute_age("2000-06-04", date(2026, 6, 4)) == 26


def test_reference_before_birthday_this_year():
    # reference = a movie release date earlier in the year than the birthday
    assert compute_age("2000-06-04", date(2026, 6, 1)) == 25


def test_unparseable_returns_none():
    assert compute_age("not-a-date", date(2026, 6, 4)) is None
    assert compute_age("", date(2026, 6, 4)) is None


def test_reference_before_birth_returns_none():
    assert compute_age("2030-01-01", date(2026, 6, 4)) is None
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/unit/test_compute_age.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create the module with `compute_age`** (the resolver class is added in Task 7; this step creates the file with imports + `compute_age` only)

```python
# javdb/spider/services/actor_age.py
"""Best-effort actor-age enrichment + resolver (ADR-040 Phase 2 / IMP-ADR040-02).

javdb actor pages carry no birthdate, so ages are resolved from minnano-av by
actor name, cached in ActorMetadata, and computed at the movie's release date.
Unknown ages never cause a drop. The runner builds the resolver only when an
'age' rule is active."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


def compute_age(birthdate: str, reference: date) -> Optional[int]:
    """Whole years from ISO ``birthdate`` to ``reference``; None if unparseable/future."""
    try:
        born = date.fromisoformat((birthdate or "").strip())
    except (ValueError, TypeError):
        return None
    years = reference.year - born.year
    if (reference.month, reference.day) < (born.month, born.day):
        years -= 1
    return years if years >= 0 else None
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/unit/test_compute_age.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/services/actor_age.py tests/unit/test_compute_age.py
git commit -m "feat(spider): add compute_age helper (ADR-040 Phase 2)"
```

---

## Task 6: minnano-av age source + pure parser

**Files:**
- Create: `javdb/spider/services/actor_age_sources.py`
- Test: `tests/unit/test_actor_age_sources.py`

> **⚠ Re-grounded by the Task-2 spike.** The code block below reflects the original (pre-spike) assumptions and is **superseded** — the shipped implementation in `javdb/spider/services/actor_age_sources.py` uses `search_word=`, an `actress\d+\.html` profile-link selector, and a redirect-first `lookup` (read the birthdate directly from a single-match redirect; fall back to a strict suffix-stripped exact-name match on a multi-match list). See the shipped file for the authoritative parser.

> Selectors/URLs below reflect the assumptions probed in Task 2. **If Task 2 found
> different markup, adjust the parser bodies AND the synthetic fixtures in the test
> to match — keep them consistent.** The tests use small synthetic HTML, never live pages.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_actor_age_sources.py
from javdb.spider.services.actor_age_sources import (
    MinnanoAvSource,
    parse_minnano_birthdate,
    parse_minnano_search,
)

_SEARCH = """
<html><body>
  <a href="actress.php?actress_id=111">Hanako Test</a>
  <a href="actress.php?actress_id=222">Other Person</a>
</body></html>
"""
_PROFILE = """
<html><body><table>
  <tr><th>生年月日</th><td>1990年5月20日</td></tr>
  <tr><th>血液型</th><td>A型</td></tr>
</table></body></html>
"""


def test_parse_minnano_search():
    hits = parse_minnano_search(_SEARCH)
    assert ("Hanako Test", "actress.php?actress_id=111") in hits


def test_parse_minnano_birthdate():
    assert parse_minnano_birthdate(_PROFILE) == "1990-05-20"


def test_parse_minnano_birthdate_absent():
    assert parse_minnano_birthdate("<html><body>no dob</body></html>") is None


def test_source_lookup_exact_match():
    pages = {
        "https://www.minnano-av.com/search_result.php?search_scope=actress&search=Hanako%20Test": _SEARCH,
        "https://www.minnano-av.com/actress.php?actress_id=111": _PROFILE,
    }
    src = MinnanoAvSource(lambda url: pages.get(url))
    hit = src.lookup("Hanako Test")
    assert hit is not None
    assert hit.birthdate == "1990-05-20"
    assert hit.source == "minnano-av"


def test_source_no_exact_match_returns_none():
    src = MinnanoAvSource(lambda url: _SEARCH)
    assert src.lookup("Nonexistent Actor") is None


def test_source_fetch_failure_returns_none():
    assert MinnanoAvSource(lambda url: None).lookup("Anyone") is None
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/unit/test_actor_age_sources.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the sources module**

```python
# javdb/spider/services/actor_age_sources.py
"""External actor-age source + pure parser (ADR-040 Phase 2).

MinnanoAvSource searches minnano-av by actor name, fetches the matched profile,
and parses a birthdate. Parsers are pure (HTML in, ISO date / hits out). I/O is via
an injected ``fetch`` callable so the resolver/tests stay deterministic. The class
is a pluggable adapter — a second source (e.g. a romaji-bridged library) can be
added later behind the same ``lookup`` interface."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class ResolvedAge:
    birthdate: str  # ISO YYYY-MM-DD
    source: str
    source_url: str


def _norm(s: str) -> str:
    return (s or "").strip().casefold().replace(" ", "")


def parse_minnano_search(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.select('a[href*="actress.php"]'):
        name = a.get_text(strip=True)
        href = a.get("href", "")
        if name and href:
            out.append((name, href))
    return out


def parse_minnano_birthdate(html: str) -> Optional[str]:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    m = re.search(r"生年月日[^\d]*(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d).date().isoformat()
    except ValueError:
        return None


class MinnanoAvSource:
    name = "minnano-av"
    _SEARCH = "https://www.minnano-av.com/search_result.php?search_scope=actress&search={q}"
    _BASE = "https://www.minnano-av.com/"

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch

    def lookup(self, actor_name: str) -> Optional[ResolvedAge]:
        html = self._fetch(self._SEARCH.format(q=quote(actor_name)))
        if not html:
            return None
        for name, href in parse_minnano_search(html):
            if _norm(name) == _norm(actor_name):
                url = urljoin(self._BASE, href)
                profile = self._fetch(url)
                bd = parse_minnano_birthdate(profile or "")
                return ResolvedAge(bd, self.name, url) if bd else None
        return None
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/unit/test_actor_age_sources.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/services/actor_age_sources.py tests/unit/test_actor_age_sources.py
git commit -m "feat(spider): add minnano-av actor-age source + parser (ADR-040 Phase 2)"
```

---

## Task 7: `ActorAgeResolver` (cache-first; release-date reference; proxied+throttled fetch)

**Files:**
- Modify: `javdb/spider/services/actor_age.py` (add resolver, cache, throttled fetch, factory)
- Test: `tests/unit/test_actor_age_resolver.py`

Semantics: per actor — normalize href; if `cache.get` returns a `resolved` row, use its `birthdate` (may be None = negative cache, skip network); else run the source chain, write the result (hit or negative) back, and use it. Age is computed at the movie's `release_date` (fallback: `today`); include only known ages, keyed by normalized href.

- [ ] **Step 1: Write the failing test** (fakes for cache + sources — no DB, no network)

```python
# tests/unit/test_actor_age_resolver.py
from dataclasses import dataclass, field
from datetime import date

from javdb.spider.services.actor_age import ActorAgeResolver
from javdb.spider.services.actor_age_sources import ResolvedAge


@dataclass
class _Actor:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    release_date: str = "2026-06-01"


class _FakeCache:
    def __init__(self):
        self.rows = {}
        self.put_calls = 0

    def get(self, href):
        return self.rows.get(href)

    def put(self, href, name, resolved):
        self.put_calls += 1
        self.rows[href] = {
            "birthdate": resolved.birthdate if resolved else None,
            "resolved": 1,
        }


class _FakeSource:
    name = "fake"

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = 0

    def lookup(self, actor_name):
        self.calls += 1
        bd = self.mapping.get(actor_name)
        return ResolvedAge(bd, self.name, "u") if bd else None


_TODAY = date(2026, 6, 4)


def test_resolves_and_computes_age_at_release():
    cache = _FakeCache()
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 36}  # 1990-05-20 -> 2026-06-01
    assert cache.put_calls == 1


def test_cache_hit_uses_release_date_reference():
    cache = _FakeCache()
    cache.rows["/actors/h"] = {"birthdate": "2000-06-04", "resolved": 1}
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 25}  # birthday 06-04 not yet reached by 06-01
    assert src.calls == 0  # cache short-circuits the chain


def test_missing_release_date_falls_back_to_today():
    cache = _FakeCache()
    cache.rows["/actors/h"] = {"birthdate": "2000-06-04", "resolved": 1}
    r = ActorAgeResolver(sources=[], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="H", href="/actors/h")], release_date=""))
    assert ages == {"/actors/h": 26}  # fallback today 2026-06-04 -> birthday reached


def test_negative_cache_skips_sources():
    cache = _FakeCache()
    cache.rows["/actors/h"] = {"birthdate": None, "resolved": 1}
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {}
    assert src.calls == 0


def test_source_chain_second_source_wins():
    cache = _FakeCache()
    s1 = _FakeSource({})                       # miss
    s2 = _FakeSource({"Hanako": "1985-03-03"})  # hit
    r = ActorAgeResolver(sources=[s1, s2], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 41}
    assert s1.calls == 1 and s2.calls == 1


def test_unresolved_actor_excluded_and_negatively_cached():
    cache = _FakeCache()
    src = _FakeSource({})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Ghost", href="/actors/g")]))
    assert ages == {}
    assert cache.rows["/actors/g"]["birthdate"] is None  # negative cached


def test_actor_without_href_or_name_skipped():
    cache = _FakeCache()
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="", href=""), _Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 36}
    assert src.calls == 1
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/unit/test_actor_age_resolver.py -v`
Expected: FAIL — `ImportError: cannot import name 'ActorAgeResolver'`

- [ ] **Step 3a: Add the new imports at the TOP of `actor_age.py`** (with the existing imports, to avoid E402). Extend the `from typing import Optional` line to `from typing import Optional, Sequence`, then add below the stdlib imports:

```python
from javdb.parsing.common import normalize_javdb_href_path
from javdb.spider.services.actor_age_sources import MinnanoAvSource, ResolvedAge
from javdb.storage.db import HISTORY_DB_PATH, get_db
from javdb.storage.repos.actor_metadata_repo import ActorMetadataRepo
```

> `create_gateway` and `should_use_proxy_for_module` are imported **lazily inside
> `_ThrottledGatewayFetch`** (below), not at module top — the runner imports this
> module, and a top-level spider-runtime import risks a cycle.

- [ ] **Step 3b: Append the cache, throttled fetch, resolver, and factory** to the BOTTOM of `javdb/spider/services/actor_age.py` (below `compute_age`):

```python
class _DbActorAgeCache:
    """Default cache backed by ActorMetadata (history DB), one short conn per op."""

    def __init__(self, db_path: str = HISTORY_DB_PATH) -> None:
        self._db_path = db_path

    def get(self, actor_href: str) -> Optional[dict]:
        try:
            with get_db(self._db_path) as conn:
                return ActorMetadataRepo(conn).get(actor_href)
        except Exception:
            logger.debug("ActorMetadata get failed for %s", actor_href, exc_info=True)
            return None

    def put(self, actor_href: str, actor_name: str,
            resolved: Optional[ResolvedAge]) -> None:
        try:
            with get_db(self._db_path) as conn:
                ActorMetadataRepo(conn).upsert(
                    actor_href, actor_name,
                    resolved.birthdate if resolved else None,
                    resolved.source if resolved else "",
                    resolved.source_url if resolved else "",
                )
        except Exception:
            logger.debug("ActorMetadata upsert failed for %s", actor_href, exc_info=True)


class _ThrottledGatewayFetch:
    """Production fetch: one proxied gateway (CF-bypass off) + a min-interval throttle.

    Reuses the spider's request stack (proxy pool honored via PROXY_MODULES for the
    'spider' module) but disables CF-bypass for external (non-javdb) hosts. A
    politeness delay keeps the daily cron from hammering / getting banned by the
    external source."""

    def __init__(self, *, min_interval: float = 1.5) -> None:
        self._min_interval = min_interval
        self._last = 0.0
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from javdb.spider.spider_gateway import create_gateway
            try:
                from javdb.spider.runtime.state import should_use_proxy_for_module
                use_proxy = bool(should_use_proxy_for_module("spider", None))
            except Exception:
                use_proxy = False
            self._gateway = create_gateway(
                use_proxy=use_proxy, use_cf_bypass=False, use_cookie=False,
            )
        return self._gateway

    def __call__(self, url: str) -> Optional[str]:
        import time
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            return self._get_gateway().fetch_html(url)
        except Exception:
            logger.debug("gateway fetch failed: %s", url, exc_info=True)
            return None


class ActorAgeResolver:
    """Turn a parsed MovieDetail into {normalized_actor_href: age}, best-effort."""

    def __init__(self, *, sources: "Sequence", today: date, cache=None) -> None:
        self._sources = list(sources)
        self._today = today  # fallback reference when release_date is unparseable
        self._cache = cache if cache is not None else _DbActorAgeCache()

    def ages_for(self, detail) -> dict[str, int]:
        reference = self._reference_for(detail)
        ages: dict[str, int] = {}
        for actor in getattr(detail, "actors", []) or []:
            href = normalize_javdb_href_path(getattr(actor, "href", "") or "")
            name = (getattr(actor, "name", "") or "").strip()
            if not href or not name or href in ages:
                continue
            birthdate = self._birthdate_for(href, name)
            if not birthdate:
                continue
            age = compute_age(birthdate, reference)
            if age is not None:
                ages[href] = age
        return ages

    def _reference_for(self, detail) -> date:
        raw = (getattr(detail, "release_date", "") or "").strip()
        try:
            return date.fromisoformat(raw[:10])
        except (ValueError, TypeError):
            return self._today

    def _birthdate_for(self, href: str, name: str) -> Optional[str]:
        cached = self._cache.get(href)
        if cached is not None and cached.get("resolved"):
            return cached.get("birthdate")  # may be None (negative cache)
        resolved = self._lookup_chain(name)
        self._cache.put(href, name, resolved)
        return resolved.birthdate if resolved else None

    def _lookup_chain(self, name: str) -> Optional[ResolvedAge]:
        for source in self._sources:
            try:
                hit = source.lookup(name)
            except Exception:
                logger.debug("age source %s failed for %s",
                             getattr(source, "name", "?"), name, exc_info=True)
                hit = None
            if hit and hit.birthdate:
                return hit
        return None


def build_default_resolver(today: Optional[date] = None) -> ActorAgeResolver:
    """Wire the real cache + minnano-av source (proxied, throttled). Used by the runner."""
    return ActorAgeResolver(
        sources=[MinnanoAvSource(_ThrottledGatewayFetch())],
        today=today or date.today(),
        cache=_DbActorAgeCache(),
    )
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/unit/test_actor_age_resolver.py tests/unit/test_compute_age.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Import-smoke** (catches the new cross-module imports)

Run: `python3 -c "from javdb.spider.services.actor_age import build_default_resolver; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/services/actor_age.py tests/unit/test_actor_age_resolver.py
git commit -m "feat(spider): add ActorAgeResolver (release-date age, proxied+throttled fetch) (ADR-040 Phase 2)"
```

---

## Task 8: `age` dimension in the engine

**Files:**
- Modify: `javdb/spider/services/content_filter.py`
- Test: `tests/unit/test_content_filter_age.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_content_filter_age.py
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


def _age_rule(mode, value, rid=1):
    return Rule(id=rid, dimension="age", mode=mode, value=value, enabled=True)


def test_no_ages_keeps():
    d = _Detail(actors=[_Actor(href="/actors/a")])
    assert evaluate(d, [_age_rule("min_age", "18")], None).keep is True


def test_min_age_drops_when_known_actor_too_young():
    dec = evaluate(_Detail(), [_age_rule("min_age", "18")], {"/actors/a": 17})
    assert dec.keep is False
    assert any("minimum age 18" in r for r in dec.reasons)


def test_min_age_keeps_when_known_meets_bound():
    assert evaluate(_Detail(), [_age_rule("min_age", "18")], {"/actors/a": 22}).keep is True


def test_max_age_drops_when_known_actor_too_old():
    assert evaluate(_Detail(), [_age_rule("max_age", "40")], {"/x": 45}).keep is False


def test_unknown_actor_never_drops():
    # one known actor that satisfies the bound; another actor is unknown (absent)
    assert evaluate(_Detail(), [_age_rule("min_age", "18")], {"/known": 30}).keep is True


def test_invalid_age_value_ignored():
    assert evaluate(_Detail(), [_age_rule("min_age", "??")], {"/x": 5}).keep is True


def test_disabled_age_rule_ignored():
    r = Rule(id=1, dimension="age", mode="min_age", value="18", enabled=False)
    assert evaluate(_Detail(), [r], {"/x": 5}).keep is True
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/unit/test_content_filter_age.py -v`
Expected: FAIL — `evaluate() takes 2 positional arguments but 3 were given` (or assertion failures)

- [ ] **Step 3: Edit `evaluate` to accept `actor_ages` and check the `age` dimension**

In `javdb/spider/services/content_filter.py`:

(a) Change the function signature line:

```python
def evaluate(detail, rules: Iterable[Rule]) -> FilterDecision:
```
to:
```python
def evaluate(detail, rules: Iterable[Rule], actor_ages=None) -> FilterDecision:
```

(b) Immediately **before** the final `if reasons:` block, insert the age check:

```python
    reasons.extend(_age_drop_reasons(enabled_rules, actor_ages))

```

(c) Add this helper at the end of the module:

```python
def _age_drop_reasons(rules: list[Rule], actor_ages) -> list[str]:
    ages = [a for a in (actor_ages or {}).values() if isinstance(a, int)]
    if not ages:
        return []
    out: list[str] = []
    for rule in rules:
        if rule.dimension != 'age':
            continue
        try:
            bound = int(str(rule.value).strip())
        except (ValueError, TypeError):
            continue
        if rule.mode == 'min_age' and any(a < bound for a in ages):
            out.append(f'actor younger than minimum age {bound}')
        elif rule.mode == 'max_age' and any(a > bound for a in ages):
            out.append(f'actor older than maximum age {bound}')
    return out
```

> Note: `enabled_rules` is already computed at the top of `evaluate`; reuse it (do not re-filter). The age block sits in the AND/reasons group (after gender), so it composes with the existing tag-include / gender checks and never overrides blacklist precedence.

- [ ] **Step 4: Run age + existing engine tests to verify PASS (no regression)**

Run: `pytest tests/unit/test_content_filter_age.py tests/unit/test_content_filter_engine.py -v`
Expected: all PASS (Phase-1 engine tests still green — `actor_ages` defaults to None).

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/services/content_filter.py tests/unit/test_content_filter_age.py
git commit -m "feat(spider): add age dimension to content-filter engine (ADR-040 Phase 2)"
```

---

## Task 9: Wire age enrichment into the detail runner (opt-in)

**Files:**
- Modify: `javdb/spider/detail/runner.py` (`process_detail_entries`, near L471 and L722)
- Test: `tests/unit/test_content_filter_age_wiring.py`

The resolver is built **once per run, only if an `age` rule is active** (zero network when no age rule exists). Ages are computed per movie and passed to `evaluate`.

- [ ] **Step 1: Write the failing test** (pins the opt-in predicate + the evaluate contract the runner relies on)

```python
# tests/unit/test_content_filter_age_wiring.py
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


def _has_age_rule(rules):
    return any(getattr(r, "dimension", "") == "age" for r in (rules or []))


def test_opt_in_predicate():
    assert _has_age_rule([Rule(1, "age", "min_age", "18", True)]) is True
    assert _has_age_rule([Rule(1, "tag", "exclude", "vr", True)]) is False
    assert _has_age_rule([]) is False


def test_runner_drops_when_age_map_violates_rule():
    rules = [Rule(1, "age", "min_age", "18", True)]
    decision = evaluate(_Detail(actors=[_Actor(href="/actors/a")]), rules, {"/actors/a": 16})
    assert decision.keep is False  # runner skips persist when keep is False
```

- [ ] **Step 2: Run to verify PASS** (pins `evaluate` + the predicate shape; both already exist after Task 8)

Run: `pytest tests/unit/test_content_filter_age_wiring.py -v`
Expected: PASS

- [ ] **Step 3: Build the resolver after rules load.** In `javdb/spider/detail/runner.py`, locate (≈L470):

```python
    if content_filter_rules is None:
        content_filter_rules = load_content_filter_rules()
```
Insert immediately after it:

```python
    actor_age_resolver = None
    if any(getattr(r, 'dimension', '') == 'age' for r in (content_filter_rules or [])):
        try:
            from javdb.spider.services.actor_age import build_default_resolver
            actor_age_resolver = build_default_resolver()
        except Exception:
            logger.info(
                "Actor-age resolver unavailable; age rules will be skipped",
                exc_info=True,
            )
            actor_age_resolver = None
```

- [ ] **Step 4: Compute ages and pass them to `evaluate`.** In the same function, locate (≈L722):

```python
            if content_filter_rules and movie_detail is not None:
                decision = evaluate(movie_detail, content_filter_rules)
```
Replace those two lines with:

```python
            if content_filter_rules and movie_detail is not None:
                actor_ages = None
                if actor_age_resolver is not None:
                    try:
                        actor_ages = actor_age_resolver.ages_for(movie_detail)
                    except Exception:
                        logger.debug(
                            "actor-age resolution failed for %s",
                            entry.get('video_code', '?'), exc_info=True,
                        )
                        actor_ages = None
                decision = evaluate(movie_detail, content_filter_rules, actor_ages)
```

> Everything below (`if not decision.keep:` drop handling, the `content_filtered`
> acknowledge) is unchanged — age drops reuse the existing Phase-1 drop path.

- [ ] **Step 5: Import-smoke + wiring + engine tests**

Run:
```bash
python3 -c "import javdb.spider.detail.runner; print('import ok')" && \
pytest tests/unit/test_content_filter_age_wiring.py tests/unit/test_content_filter_engine.py tests/unit/test_content_filter_wiring.py -v
```
Expected: `import ok` + all PASS.

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/detail/runner.py tests/unit/test_content_filter_age_wiring.py
git commit -m "feat(spider): wire opt-in actor-age enrichment into detail runner (ADR-040 Phase 2)"
```

---

## Task 10: CLI — age rules + cache management

**Files:**
- Modify: `apps/cli/ops/content_filter.py` (add `age` dimension + `min_age`/`max_age` modes)
- Create: `apps/cli/ops/actor_age.py` (inspect/refresh the `ActorMetadata` cache)
- Test: `tests/smoke/test_actor_age_cli.py`
- Test: extend `tests/smoke/test_content_filter_cli.py` (add one age-rule case)

- [ ] **Step 1: Extend the content-filter CLI for age rules.** In `apps/cli/ops/content_filter.py`:

Change:
```python
DIMENSIONS = ("actor", "tag", "gender")
MODES = ("exclude", "include", "require_lead", "exclude_all_male")
VALID_RULE_MODES = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
    ("gender", "exclude_all_male"),
}
VALUE_REQUIRED = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
}
```
to:
```python
DIMENSIONS = ("actor", "tag", "gender", "age")
MODES = ("exclude", "include", "require_lead", "exclude_all_male", "min_age", "max_age")
VALID_RULE_MODES = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
    ("gender", "exclude_all_male"),
    ("age", "min_age"),
    ("age", "max_age"),
}
VALUE_REQUIRED = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
    ("age", "min_age"),
    ("age", "max_age"),
}
```

Then add age-value validation inside `_validate_add`, right after the `gender` branches (before the final `else:`):

```python
    elif args.dimension == "age":
        if not value.isdigit():
            parser.error("age rules require --value to be a non-negative integer")
        args.value = str(int(value))
```

- [ ] **Step 2: Add an age-rule case to the existing CLI smoke test.** Append to `tests/smoke/test_content_filter_cli.py`:

```python
def test_add_age_rule(monkeypatch):
    import contextlib

    import apps.cli.ops.content_filter as cli

    captured = {}

    class _Repo:
        def __init__(self, *a, **k):
            pass

        def add_rule(self, dimension, mode, value):
            captured.update(dimension=dimension, mode=mode, value=value)
            return 7

    @contextlib.contextmanager
    def _fake_db(_path):
        yield object()

    monkeypatch.setattr(cli, "ContentFilterRepo", _Repo)
    monkeypatch.setattr(cli, "get_db", _fake_db)
    rc = cli.main(["add", "--dimension", "age", "--mode", "min_age", "--value", "18"])
    assert rc == 0
    assert captured == {"dimension": "age", "mode": "min_age", "value": "18"}
```

- [ ] **Step 3: Write the failing actor-age CLI smoke test**

```python
# tests/smoke/test_actor_age_cli.py
import subprocess
import sys


def test_actor_age_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.actor_age", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    out = r.stdout.lower()
    assert "list" in out and "refresh" in out
```

- [ ] **Step 4: Run to verify FAIL**

Run: `pytest tests/smoke/test_actor_age_cli.py -v`
Expected: FAIL — no module

- [ ] **Step 5: Write the actor-age cache CLI**

```python
# apps/cli/ops/actor_age.py
"""Inspect / refresh the ActorMetadata age cache (ADR-040 Phase 2).

  list                          show cached actor rows (age shown as current age)
  refresh --href <p> --name <n> force a re-lookup (overwrites the negative cache)
  clear  --href <p>             delete a cached row
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from javdb.infra.logging import setup_logging
from javdb.parsing.common import normalize_javdb_href_path
from javdb.spider.services.actor_age import build_default_resolver, compute_age
from javdb.storage.db import HISTORY_DB_PATH, get_db
from javdb.storage.repos.actor_metadata_repo import ActorMetadataRepo


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.actor_age",
        description="Inspect / refresh the ActorMetadata age cache.",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List cached actor rows.")

    refresh = sub.add_parser("refresh", help="Force a re-lookup for one actor.")
    refresh.add_argument("--href", required=True, help="javdb /actors/<id> path")
    refresh.add_argument("--name", required=True, help="actor name to search by")

    clear = sub.add_parser("clear", help="Delete a cached actor row.")
    clear.add_argument("--href", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    if args.command == "list":
        with get_db(HISTORY_DB_PATH) as conn:
            rows = ActorMetadataRepo(conn).list_all()
        if not rows:
            print("No cached actor metadata.")
            return 0
        today = date.today()
        print("actor_href\tbirthdate\tage_now\tsource")
        for r in rows:
            bd = r.get("birthdate")
            age = compute_age(bd, today) if bd else None
            print(f"{r['actor_href']}\t{bd or '-'}\t{age if age is not None else '-'}\t{r.get('source') or '-'}")
        return 0

    if args.command == "clear":
        with get_db(HISTORY_DB_PATH) as conn:
            ActorMetadataRepo(conn).delete(args.href)
        print(f"Cleared cache for {args.href}.")
        return 0

    if args.command == "refresh":
        # Force a fresh lookup: clear, then resolve via the real source chain.
        with get_db(HISTORY_DB_PATH) as conn:
            ActorMetadataRepo(conn).delete(args.href)
        resolver = build_default_resolver()

        class _OneActor:
            actors = [type("A", (), {"name": args.name, "href": args.href})()]
            release_date = ""  # CLI debug → current-age reference

        ages = resolver.ages_for(_OneActor())
        key = normalize_javdb_href_path(args.href)
        if key in ages:
            print(f"Resolved {args.name}: age {ages[key]} (as of today).")
        else:
            print(f"No birthdate found for {args.name} (cached as unknown).")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run CLI tests to verify PASS**

Run: `pytest tests/smoke/test_actor_age_cli.py tests/smoke/test_content_filter_cli.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/cli/ops/content_filter.py apps/cli/ops/actor_age.py \
        tests/smoke/test_actor_age_cli.py tests/smoke/test_content_filter_cli.py
git commit -m "feat(cli): add age rules + ActorMetadata cache CLI (ADR-040 Phase 2)"
```

---

## Task 11: Docs + full gate

**Files:**
- Modify: `CONTEXT.md`
- Modify: `docs/handbook/en/developer/cli-reference.md` (+ `docs/handbook/zh/developer/cli-reference.md`)

- [ ] **Step 1: Update CONTEXT.md** — under the ADR-040 terms added by IMP-01, add:

```markdown
- **Actor age enrichment** — best-effort resolution of an actor's birthdate from
  minnano-av (keyed by name), cached in `ActorMetadata`. javdb pages carry no
  birthdate (ADR-040 Phase 2). Age is computed at the movie's release date.
- **Age filter** — an `age` content-filter rule (`min_age` / `max_age`) applied to
  the *known* ages of a movie's actors. Unknown ages never cause a drop (best-effort).
- **Negative cache** — an `ActorMetadata` row with `resolved=1` and `birthdate` NULL:
  "looked up, not found"; avoids re-querying the external site every run.
```

- [ ] **Step 2: Update the CLI reference (en).** In `docs/handbook/en/developer/cli-reference.md`, under the content-filter CLI section, document the new `age` dimension and the cache CLI:

````markdown
#### Age rules (ADR-040 Phase 2, best-effort)

```bash
# Drop any movie featuring an actor known to be under 18 at the release date
python -m apps.cli.ops.content_filter add --dimension age --mode min_age --value 18

# Drop any movie featuring an actor known to be over 40
python -m apps.cli.ops.content_filter add --dimension age --mode max_age --value 40
```

Ages are resolved best-effort from minnano-av by actor name, cached in
`ActorMetadata`, and computed at the movie's release date. Actors with no resolved
birthdate have unknown age and never cause a drop.

```bash
python -m apps.cli.ops.actor_age list                       # inspect the cache
python -m apps.cli.ops.actor_age refresh --href /actors/EvkJ --name "<name>"  # force re-lookup
python -m apps.cli.ops.actor_age clear --href /actors/EvkJ   # drop a cached row
```
````

- [ ] **Step 3: Mirror Step 2 into `docs/handbook/zh/developer/cli-reference.md`** (translate prose + comments; keep commands/paths verbatim).

- [ ] **Step 4: Full gate** — run every test this IMP touched:

Run:
```bash
pytest tests/unit/test_compute_age.py tests/unit/test_actor_metadata_repo.py \
       tests/unit/test_actor_age_sources.py tests/unit/test_actor_age_resolver.py \
       tests/unit/test_content_filter_age.py tests/unit/test_content_filter_age_wiring.py \
       tests/unit/test_content_filter_engine.py tests/unit/test_content_filter_wiring.py \
       tests/smoke/test_actor_age_cli.py tests/smoke/test_content_filter_cli.py -v
```
Expected: all PASS.

- [ ] **Step 5: Additive-invariant check** — no age rule → no enrichment, no behavior change:

Run:
```bash
python3 -c "from javdb.spider.services.content_filter import evaluate; assert evaluate(object(), []).keep is True; assert evaluate(object(), [], None).keep is True; print('additive ok')"
```
Expected: `additive ok` (empty rules and/or `actor_ages=None` keep everything; Phase-1 behavior intact).

- [ ] **Step 6: Regression-neighbor smoke** — confirm the runner edit didn't break detail processing:

Run:
```bash
pytest -q tests/smoke/test_spider_detail_runner.py tests/unit/test_detail_runner_work_distributor.py
git diff --check
```
Expected: PASS; no whitespace errors.

- [ ] **Step 7: Commit**

```bash
git add CONTEXT.md docs/handbook
git commit -m "docs: document ADR-040 Phase 2 age filter + actor-age cache CLI"
```

---

## Plan Self-Review

**Spec coverage (re-numbered ADR-040 Phase 2 = age):**
- ADR assumption reversed + roadmap re-numbered → Task 1. ✓
- External source markup grounded + reachability confirmed before coding → Task 2 spike. ✓
- `ActorMetadata` cache table (history D1) → Task 3; repo → Task 4. ✓
- Best-effort resolution (minnano-av, name match, negative cache, pluggable adapter) → Tasks 6, 7. ✓
- Age **at release date** (fallback run date) → `compute_age` (Task 5) + `_reference_for` (Task 7). ✓
- Proxied + throttled external fetch → `_ThrottledGatewayFetch` (Task 7). ✓
- `age` dimension (`min_age`/`max_age`), AND-group precedence, unknown→keep → Task 8. ✓
- Opt-in, zero-cost-when-unused wiring → Task 9. ✓
- CLI to add age rules + manage the cache → Task 10. ✓
- Docs (CONTEXT, cli-reference en/zh) + gates → Task 11. ✓

**Type/name consistency:** `compute_age(birthdate, reference)`, `ResolvedAge(birthdate, source, source_url)`, `MinnanoAvSource.lookup`, `parse_minnano_search`/`parse_minnano_birthdate`, `ActorMetadataRepo.get/upsert/list_all/delete`, `ActorAgeResolver(sources=, today=, cache=).ages_for`, `_ThrottledGatewayFetch`, `build_default_resolver`, `evaluate(detail, rules, actor_ages=None)` — used identically across Tasks 4–11. Cache protocol = `.get(href)` / `.put(href, name, resolved)` in both the fake (Task 7 test) and `_DbActorAgeCache` (Task 7 impl). ✓

**Additive / fail-open guarantees:** no `age` rule → resolver never built (Task 9) → zero network; `actor_ages=None` → engine age block is a no-op (Task 8); rule-load/resolver/source/cache/fetch failures all fall back to "no age data" → keep (Tasks 6, 7, 9). The Phase-1 rating gate and the actor/tag/gender rules are untouched. ✓

**Decisions baked in (operator-confirmed during plan review):** age = **at release date** (fallback run date); **minnano-av only** (xslist deferred — cannot match Japanese names; adapter chain stays open); external fetch = **proxy pool (via `PROXY_MODULES`/'spider') + politeness throttle**. Age rules apply to **any** actor with a known age (the under-age gate). Name match is **exact** (avoids attributing the wrong person's birthdate). Negative cache is **persistent** (cleared via `actor_age refresh`).

**Known limitations (documented, not defects):** best-effort coverage (name collisions, alternate names, missing minnano-av entries); `release_date` that is missing/non-ISO falls back to the run date; first run with many uncached actors adds ~2 throttled requests/actor (near-zero after caching). Selectors/URLs are confirmed by the Task-2 spike — if live markup differs, Task 6's parser + fixtures are adjusted together.

## Open follow-ups (out of scope for IMP-02)

- **Negative-cache TTL** — an actor added to minnano-av later stays "unknown" until a manual `actor_age refresh`. A dated re-resolve policy is a future enhancement.
- **Second age source** — a romaji-bridged source (e.g. xslist with name transliteration) can slot into the resolver's adapter chain when coverage gaps justify it.
- **Subscriptions / web-MCP management** — Phases 3–4 (separate IMPs), per the re-numbered roadmap.
