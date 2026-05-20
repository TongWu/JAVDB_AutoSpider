# JavDB Parsing Module Design

Date: 2026-05-19

## Context

JavDB HTML parsing currently lives under `apps.api.parsers` and uses
dataclasses from `apps.api.models`. That location is backwards for the current
architecture: Spider runtime, Storage, Migration tools, API services, and ops
profiling all depend on parsing behavior, but parsing is not an API-layer
concern.

ADR-005 already identified a smaller version of this problem: three helpers in
`apps.api.parsers.common` are imported by Storage and should be moved down. This
design supersedes that narrow D4/PR-6 item by moving the whole JavDB Parsing
Interface into a deep domain module.

## Non-Negotiable Invariant

The parsing restructure is behavior-preserving. Parsing has been running in
production for months, so the migration must not change parser output, fallback
behavior, Rust-first dispatch semantics, legacy adapter return shapes, URL
normalization, sentinel values, tag interpretation, or any edge-case detail.

Every implementation phase must prove parity against the current behavior before
it is considered complete. Structural cleanup is not allowed to "improve" parser
behavior as a side effect. If a parser behavior change is ever needed, it must be
a separate PR with its own fixtures and parity explanation.

## Goals

- Make `javdb.parsing` the single production Interface for JavDB HTML parsing.
- Move parser dataclasses into `javdb.parsing.models`.
- Move common parser helpers into `javdb.parsing.common`.
- Keep Rust-first production dispatch, with BeautifulSoup fallbacks clearly
  marked as frozen fallback implementations.
- Turn `apps.api.parsers` and `apps.api.models` into temporary compatibility
  Adapters.
- Move index-stage phase filtering out of parsing and into Pipeline selection.
- Write ADR and IMP follow-ups that make the temporary state converge to the
  final state.

## Non-Goals

- Do not change parser behavior or output shape.
- Do not change Rust parser behavior.
- Do not change API payload behavior.
- Do not retire ADR-005 Storage/Repo work.
- Do not redesign Spider runtime coordination.

## Architecture

`javdb.parsing` becomes the deep Module for JavDB HTML parsing. Callers use:

```python
from javdb.parsing import (
    parse_index_page,
    parse_detail_page,
    parse_category_page,
    parse_top_page,
    parse_tag_page,
    detect_page_type,
)
from javdb.parsing.models import MovieDetail, MovieIndexEntry, TagPageResult
from javdb.parsing.common import javdb_absolute_url, movie_href_lookup_values
```

`javdb.parsing.__init__` is the only production parser dispatch Interface. It
tries `javdb.rust_core` first and falls back to `javdb.parsing.fallback.*` when
the Rust extension is unavailable.

`apps.api.parsers` and `apps.api.models` remain temporarily as compatibility
Adapters. They re-export from `javdb.parsing` during migration and are not the
home of real parsing Implementation.

## Module Layout

`javdb/parsing/__init__.py`
: Rust-first production Interface. Exports parser functions and
  `RUST_PARSERS_AVAILABLE`.

`javdb/parsing/models.py`
: Parsing output dataclasses and sentinels: `MovieLink`, `ActorCredit`,
  `MagnetInfo`, `MovieIndexEntry`, `MovieDetail`, `IndexPageResult`,
  `CategoryPageResult`, `TopPageResult`, `TagOption`, `TagCategory`,
  `TagPageResult`, `NO_ACTOR_LISTING_ACTOR_NAME`, and
  `NO_ACTOR_LISTING_ACTOR_GENDER`.

`javdb/parsing/common.py`
: Shared parser helpers: URL normalization, absolute JavDB URL construction,
  href lookup variants, rate/comment extraction, video code extraction,
  `MovieLink` extraction, page type detection, category name extraction, and
  supporting-actor URL normalization.

`javdb/parsing/fallback/index_parser.py`
: Frozen BeautifulSoup fallback for index/category/top pages.

`javdb/parsing/fallback/detail_parser.py`
: Frozen BeautifulSoup fallback for detail pages.

`javdb/parsing/fallback/tag_parser.py`
: Frozen BeautifulSoup fallback for tag pages.

`javdb/parsing/search_exact.py`
: Exact video-code search helpers used by API search and migration alignment.

`javdb/pipeline/index_selection.py`
: Business selection for index-stage entries: phase 1/phase 2, ad hoc mode,
  today/yesterday release tags, subtitle/magnet tags, P2 rate/comment thresholds,
  invalid score handling, and conversion to the legacy entry dict shape.

`javdb/spider/parser.py`
: Temporary Spider runtime Adapter. It imports from `javdb.parsing` and
  `javdb.pipeline.index_selection`, preserving legacy `parse_index()` and
  `parse_detail()` for callers that still expect legacy dict/tuple shapes.

## Migration Plan

### Phase 1: Establish `javdb.parsing`

- Move real parser models, common helpers, parser dispatch, search helpers, and
  frozen Python fallbacks under `javdb/parsing`.
- Make `apps.api.parsers` and `apps.api.models` re-export from `javdb.parsing`.
- Change `javdb.spider.parser` to depend on `javdb.parsing`, while preserving
  legacy behavior exactly.
- Update primary parser tests to import from `javdb.parsing`.
- Keep compatibility tests for the old API-layer import paths.

Gate:

- Parser unit tests pass.
- Parser parity tests pass.
- Spider smoke tests pass.
- `from javdb.parsing import parse_index_page` is Rust-first when Rust is
  installed and fallback-backed when Rust is unavailable.
- Current output fixtures match pre-migration output.

### Phase 2: Migrate Internal Callers

- Migrate all non-Adapter internal imports from `apps.api.parsers` and
  `apps.api.models` to `javdb.parsing`.
- Add `javdb.pipeline.index_selection`.
- Move `parse_index()` phase filtering into `javdb.pipeline.index_selection`.
- Reduce `javdb.spider.parser` to a thin legacy Adapter over
  `javdb.parsing` and `javdb.pipeline.index_selection`.

Gate:

- No non-Adapter file imports `apps.api.parsers` or `apps.api.models`.
- `javdb.pipeline.index_selection` has focused unit coverage for phase 1,
  phase 2, ad hoc, ignored release date, invalid rate/comment, no video code,
  subtitle/magnet tag handling, and legacy entry dict output.
- Spider index-fetch and detail-flow smoke/integration tests pass.
- Parser output parity with the pre-migration behavior is still proven.

### Phase 3: Remove Compatibility

- Delete `apps.api.parsers` and `apps.api.models` parser re-export Adapters
  after all internal and documented consumers use `javdb.parsing`. If the API
  layer still needs local schema modules, those modules must not own or
  re-export parser symbols.
- Delete `javdb.spider.parser` after callers have moved to either
  `javdb.parsing`, `javdb.pipeline.index_selection`, or a more explicit Spider
  runtime Adapter.
- Update developer docs so `javdb.parsing` is the documented path.

Gate:

- Grep gate rejects non-historical imports of `apps.api.parsers`,
  `apps.api.models`, and `javdb.spider.parser`.
- Developer docs no longer teach new users to import parsing from `apps.api`.
- Compatibility removal has no parser behavior delta.
- No parser Implementation or parser re-export remains under `apps.api`.

## ADR And IMP Requirements

Create a new ADR, proposed as
`docs/design/adr/ADR-011-javdb-parsing-module.md`, that records:

- `javdb.parsing` as the canonical parsing Module.
- The behavior-preserving invariant.
- `apps.api.parsers` and `apps.api.models` as temporary compatibility Adapters.
- The three-phase convergence plan.
- ADR-005 D4/PR-6 is superseded by this ADR.

Create a new IMP, proposed as
`docs/design/impl/IMP-016-javdb-parsing-module.md`, that records the concrete
implementation tasks and checkboxes for all three phases. The IMP must include
the grep gates and must not stop at the Phase 1 compatibility state.

Amend ADR-005 to say:

- D4/PR-6 is extracted and superseded by the JavDB Parsing Module ADR.
- ADR-005 Storage/Repo work should import parsing helpers from
  `javdb.parsing.common` after this migration.
- ADR-005 no longer owns parser/helper relocation.

## Data Flow

1. API, Spider runtime, Migration tools, and ops tools call `javdb.parsing`.
2. `javdb.parsing.__init__` dispatches to `javdb.rust_core` when available.
3. If Rust import is unavailable, `javdb.parsing.fallback.*` handles parsing.
4. Parsers return `javdb.parsing.models` dataclasses.
5. During migration, `apps.api.parsers` and `apps.api.models` re-export those
   same objects.
6. During migration, `javdb.spider.parser` converts parsing results into the
   existing legacy Spider shapes.
7. Phase 2 moves index-stage filtering into `javdb.pipeline.index_selection`.

## Error Handling

- Rust import failure sets `RUST_PARSERS_AVAILABLE=False` and uses fallback.
- Rust parser runtime exceptions keep the current production behavior; the
  restructure must not add broad exception swallowing.
- Frozen Python fallbacks remain frozen. They are changed only to preserve
  parity with Rust or existing production behavior.
- Compatibility Adapters should avoid noisy `DeprecationWarning` in Phase 1 so
  tests and logs remain stable. Phase 3 removes the parser re-export Adapters
  after Phase 2 migration is complete.

## Testing Strategy

- Move or mirror `tests/unit/test_api_models.py` into parsing-model tests with
  primary imports from `javdb.parsing.models`.
- Move parser tests so primary production-entry tests import from
  `javdb.parsing`.
- Update parity tests so Rust output is compared with
  `javdb.parsing.fallback.*` output.
- Keep a small compatibility test suite for `apps.api.parsers` and
  `apps.api.models` re-exports while they exist.
- Add focused tests for `javdb.pipeline.index_selection`.
- Keep Spider smoke/integration tests as end-to-end behavior protection.
- Add fixture comparison or golden-output checks where needed to prove no parser
  behavior changed during structural moves.

## Documentation Strategy

- New ADR records the architectural decision.
- New IMP records all migration phases and gates.
- ADR-005 is amended to extract D4/PR-6.
- Developer API docs move their main import guidance from `apps.api.parsers` to
  `javdb.parsing`.
- Compatibility paths are documented only as transitional paths.
- During implementation, README and wiki updates are required if user-facing
  setup or import guidance changes.

## Open Questions Resolved

- Scope: move the whole JavDB Parsing Interface, not only three helpers.
- Home: use `javdb.parsing`, not `javdb.spider.parsing`.
- Migration style: staged compatibility first, forced convergence in Phase 3.
- Models: move dataclasses into `javdb.parsing.models`.
- Dispatch: production Rust-first dispatch lives in `javdb.parsing.__init__`.
- Fallback: use `javdb.parsing.fallback.*`.
- Legacy Spider wrapper: keep temporarily, mark for deletion.
- Index-stage filtering: move to `javdb.pipeline.index_selection`.
