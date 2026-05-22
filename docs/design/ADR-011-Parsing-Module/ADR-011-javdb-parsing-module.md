# ADR-011: JavDB Parsing Module

**Status**: Accepted — implementation pending
**Date**: 2026-05-20
**Deciders**: Parsing module architecture review
**Supersedes**: [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) D4 / PR-6 parser-helper relocation
**Related Implementation Plans**: [IMP-ADR011-01](IMP-ADR011-01-parsing-phase1-core-module.md) (Phase 1 — core module), [IMP-ADR011-02](IMP-ADR011-02-parsing-phase2-caller-migration.md) (Phase 2 — caller migration), [IMP-ADR011-03](IMP-ADR011-03-parsing-phase3-delete-compat.md) (Phase 3 — compatibility deletion)

## Context

JavDB HTML parsing currently lives under `apps.api.parsers` and uses
dataclasses from `apps.api.models`. That is the wrong architectural home:
Spider runtime, Storage, Migration tools, API services, and ops profiling all
depend on parsing behavior, but parsing is not an API-layer concern.

ADR-005 identified a narrower version of the same issue: three helpers in
`apps.api.parsers.common` are imported by Storage and should move down. This ADR
extracts that work from ADR-005 and expands the boundary to the full JavDB
Parsing Interface.

## Non-Negotiable Invariant

The migration is behavior-preserving. Parsing has been running in production for
months, so structural cleanup must not change parser output, fallback behavior,
Rust-first dispatch semantics, legacy adapter return shapes, URL normalization,
sentinel values, tag interpretation, or edge-case details.

Every phase must prove parity against the current behavior before it is
complete. Parser behavior changes are out of scope for this ADR. If a parser
behavior change is required later, it must land as a separate PR with its own
fixtures, review, and parity explanation.

## Decision

### D1. Canonical Module

`javdb.parsing` becomes the canonical production Interface for JavDB HTML
parsing. New code imports parsing from:

```python
from javdb.parsing import (
    detect_page_type,
    parse_category_page,
    parse_detail_page,
    parse_index_page,
    parse_tag_page,
    parse_top_page,
)
from javdb.parsing.common import javdb_absolute_url, movie_href_lookup_values
from javdb.parsing.models import MovieDetail, MovieIndexEntry, TagPageResult
```

`apps.api.parsers` and `apps.api.models` are not canonical parser homes after
Phase 1. They exist only as temporary compatibility Adapters.

### D2. Rust-First Dispatch

`javdb.parsing.__init__` owns production parser dispatch. It tries
`javdb.rust_core` first and falls back to frozen Python implementations when the
Rust extension is unavailable.

The migration must preserve current dispatch behavior. It must not add broad
exception swallowing, alter fallback activation, or make direct fallback imports
the production path.

### D3. Parsing Models

Parser output dataclasses and sentinels move to `javdb.parsing.models`:

- `MovieLink`
- `ActorCredit`
- `MagnetInfo`
- `MovieIndexEntry`
- `MovieDetail`
- `IndexPageResult`
- `CategoryPageResult`
- `TopPageResult`
- `TagOption`
- `TagCategory`
- `TagPageResult`
- `NO_ACTOR_LISTING_ACTOR_NAME`
- `NO_ACTOR_LISTING_ACTOR_GENDER`

During compatibility phases, `apps.api.models` re-exports these same objects.

### D4. Common Helpers

Shared parser helpers move to `javdb.parsing.common`, including URL
normalization, JavDB absolute URL construction, href lookup variants,
rate/comment extraction, video-code extraction, `MovieLink` extraction, page
type detection, category name extraction, and supporting-actor URL
normalization.

ADR-005 Storage/Repo work should import these helpers from
`javdb.parsing.common` after Phase 1 lands.

### D5. Frozen Fallbacks

The BeautifulSoup parser implementations move under:

```text
javdb/parsing/fallback/index_parser.py
javdb/parsing/fallback/detail_parser.py
javdb/parsing/fallback/tag_parser.py
```

They are frozen fallback implementations. They are changed only when needed to
preserve parity with existing production behavior.

### D6. Search Helpers

Exact video-code search helpers move to `javdb.parsing.search_exact`. API
services and migration tools may call that module directly after caller
migration.

### D7. Index Selection Belongs To Pipeline

`parse_index()` currently mixes HTML parsing with Spider phase filtering. The
phase 1 / phase 2, ad hoc mode, today/yesterday release tag, subtitle/magnet
tag, score threshold, invalid score, and legacy dict conversion logic moves to
`javdb.pipeline.index_selection`.

Parsing modules return parsed page data. Pipeline selection decides which parsed
entries a run should process.

### D8. Spider Runtime Adapter Is Temporary

`javdb.spider.parser` remains temporarily as a Spider runtime Adapter. It may
wrap `javdb.parsing` and `javdb.pipeline.index_selection` to preserve legacy
`parse_index()` / `parse_detail()` return shapes while callers migrate.

The adapter must be deleted in Phase 3. Keeping it as a permanent wrapper would
leave the codebase with two parser Interfaces and defeat this ADR.

### D9. Three-Phase Convergence

Implementation is split into three independently reviewable phases:

| Phase | Implementation plan | Outcome |
|---|---|---|
| Phase 1 | [IMP-ADR011-01](IMP-ADR011-01-parsing-phase1-core-module.md) | Establish `javdb.parsing`; API parser/model modules become compatibility Adapters. |
| Phase 2 | [IMP-ADR011-02](IMP-ADR011-02-parsing-phase2-caller-migration.md) | Internal callers move to `javdb.parsing`; index selection moves to Pipeline. |
| Phase 3 | [IMP-ADR011-03](IMP-ADR011-03-parsing-phase3-delete-compat.md) | Delete API parser/model re-export Adapters and the legacy Spider parser Adapter. |

Phase 1 compatibility is not the final architecture. The implementation must
continue through Phase 3 before this ADR is fully delivered.

## Module Layout

```text
javdb/parsing/
├── __init__.py
├── common.py
├── models.py
├── search_exact.py
└── fallback/
    ├── __init__.py
    ├── detail_parser.py
    ├── index_parser.py
    └── tag_parser.py

javdb/pipeline/
└── index_selection.py
```

Temporary compatibility locations:

```text
apps/api/parsers/
apps/api/models.py
javdb/spider/parser.py
```

## Gates

- Parser unit tests pass.
- Parser parity tests pass against current Rust and Python fallback behavior.
- Spider index/detail smoke or integration tests pass.
- Output fixtures or golden-output checks prove no parser behavior changed.
- Each phase has grep gates for the import paths it is responsible for.
- Developer docs stop teaching `apps.api.parsers` before compatibility deletion.

## Consequences

### Positive

1. Parsing becomes a deep domain module rather than an API implementation
   detail.
2. Storage, Migration, Spider, API, and ops tooling can share parsing helpers
   without reverse-importing from `apps.api`.
3. Rust-first production parsing and Python fallback parsing are visible as one
   coherent Interface.
4. Phase 3 removes the legacy wrappers, preventing permanent dual Interfaces.

### Negative

1. Caller migration touches API services, Spider runtime, migration tools,
   parity tests, unit tests, and developer docs.
2. Compatibility must be managed deliberately across phases.
3. Golden/parity coverage is required before each structural move is accepted.

### Risks

1. **A structural move accidentally changes parsing behavior.**
   - **Mitigation**: parity fixtures, existing parser tests, Spider smoke tests,
     and the non-negotiable invariant in every phase.
2. **Phase 1 compatibility becomes the permanent state.**
   - **Mitigation**: Phase 2 and Phase 3 have their own IMPs and grep gates.
3. **External/private scripts still import old parser paths.**
   - **Mitigation**: compatibility Adapters remain through Phase 2; Phase 3
     updates developer docs and uses grep gates before deletion.

## ADR-005 Amendment

ADR-005 D4 / PR-6 is superseded by this ADR. ADR-005 remains responsible for
the Storage/Repo work, but no longer owns parser/helper relocation. Once Phase 1
lands, ADR-005 Storage work should import parsing helpers from
`javdb.parsing.common`.

## References

- [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
- [IMP-ADR011-01](IMP-ADR011-01-parsing-phase1-core-module.md)
- [IMP-ADR011-02](IMP-ADR011-02-parsing-phase2-caller-migration.md)
- [IMP-ADR011-03](IMP-ADR011-03-parsing-phase3-delete-compat.md)
