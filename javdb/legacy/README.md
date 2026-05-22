# javdb.legacy — Deprecated Spider Implementation

Historical preservation of the **pre-Phase-1 spider entry point**
(`_spider_legacy.py`, 2519 lines). Lived at `legacy/_spider_legacy.py`
in the old tree until ADR-007 Phase 3 deleted the top-level `legacy/`
directory; restored to this location for the reasons below.

## What's here

- `_spider_legacy.py` — the monolithic spider that ran before
  Phase 1 split scraping into [`javdb.spider`](../spider/). Imports
  have been retargeted to the post-ADR-007 canonical paths
  (`javdb.storage.*`, `javdb.spider.*`, `apps.api.parsers`, …) so the
  module still parses cleanly under the modern layout.

## Why it's kept

1. **Emergency rollback artefact.** If a regression in
   [`javdb.spider`](../spider/) ever needs an "fall back to the
   single-file scraper" mitigation, this file is the starting point.
2. **Historical reference.** A 2,500-line single-file spider is the
   only place in the repo where the full pre-refactor data-flow lives
   in one readable sequence; useful when reading commit archaeology.

## What this is NOT

- **NOT** imported by any production code path, workflow, or test.
- **NOT** maintained — new spider features go to
  [`javdb.spider`](../spider/) only. Do not bring back imports of
  `javdb.legacy.*` without an ADR.
- **NOT** part of the supported public API. Do not link external
  callers (workflows, docs, frontend) to this module.

## See also

- [`../spider/`](../spider/) — canonical spider implementation
- [ADR-007](../../docs/design/_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md)
  — restructure decision that retired the original `legacy/` location
- [`../../docs/design/architecture/python-tree-2026-05.md`](../../docs/design/architecture/python-tree-2026-05.md)
  — current canonical tree map
