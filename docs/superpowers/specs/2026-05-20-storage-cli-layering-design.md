# Storage CLI Layering Design

Date: 2026-05-20

## Context

The original layering inversion was documented in ADR-008: storage rollback
logic imported helper code from `apps.cli.db._session_helpers`. That specific
storage-to-CLI import has already been partially fixed:

- `javdb.storage.rollback.core` now imports from
  `javdb.storage.rollback.session_helpers`;
- `apps.cli.db._session_helpers` is a re-export shim;
- the helper implementation lives under `javdb.storage.rollback`.

The remaining issue is smaller but still sharp:

- `apps.cli.db.commit_session` still imports from the CLI shim instead of the
  storage/library helper;
- the canonical helper path is named as rollback-specific even though it is
  shared by rollback, commit, API commit side effects, and session lifecycle
  operations;
- no architecture guard prevents `javdb.storage.*` from importing
  `apps.cli.*` again.

## Goals

- Make the dependency direction explicit: CLI may import storage/library code;
  storage must not import CLI code.
- Complete the migration from CLI helper shim to storage/library helper.
- Rename the canonical helper location to a neutral session lifecycle module.
- Delete legacy wrappers after a bake/migration window.
- Update stale docs that still describe the old `storage -> CLI` import.
- Preserve commit/rollback behavior and exit-code semantics.

## Non-Goals

- Do not redesign rollback or commit behavior.
- Do not split GitHub Actions output helpers into a workflow package in this
  ADR.
- Do not move all CLI utilities into libraries.
- Do not change pending-mode, MovieClaim fanout, JSONL metrics, or
  `GITHUB_OUTPUT` behavior.

## Selected Approach

Use three phases.

Phase 1 fixes the dependency rule and the remaining CLI shim usage without
moving the canonical implementation. This phase adds an architecture guard that
prevents Python imports from `javdb/storage/**/*.py` to `apps.cli.*`.

Phase 2 creates the neutral canonical module
`javdb.storage.sessions.lifecycle_helpers`, moves the implementation there, and
migrates production callers to the new path. Legacy paths become re-export
wrappers for a short compatibility window.

Phase 3 deletes both legacy wrappers:

- `apps.cli.db._session_helpers`
- `javdb.storage.rollback.session_helpers`

The final shape is:

```text
apps.cli.db.rollback / apps.cli.db.commit_session
  -> javdb.storage.rollback.core / javdb.storage.sessions.commit
  -> javdb.storage.sessions.lifecycle_helpers
  -> javdb.storage.db.*, MovieClaim client, filesystem/env side effects
```

The forbidden shape is:

```text
javdb.storage.* -> apps.cli.*
```

## Components

`javdb.storage.sessions.lifecycle_helpers`
: Final canonical implementation. Owns shared session lifecycle scaffolding:
  timestamp normalization, run/window lookup helpers, pre-state read,
  MovieClaim fanout, JSONL emission, GitHub output writes, and run identity
  attachment.

`javdb.storage.rollback.session_helpers`
: Phase 2 temporary storage compatibility wrapper. Deleted in Phase 3.

`apps.cli.db._session_helpers`
: Phase 1 and Phase 2 CLI compatibility shim. Deleted in Phase 3.

`apps.cli.db.commit_session`
: Phase 1 imports storage helper directly from
  `javdb.storage.rollback.session_helpers`. Phase 2 imports the new canonical
  `javdb.storage.sessions.lifecycle_helpers`.

`javdb.storage.rollback.core`
: Phase 1 remains on `javdb.storage.rollback.session_helpers`. Phase 2 imports
  the new canonical lifecycle helpers.

`javdb.storage.sessions.commit`
: Phase 1 remains unchanged. Phase 2 imports the new canonical lifecycle
  helpers.

Architecture guard
: AST-based test that scans `javdb/storage/**/*.py` and rejects real imports of
  `apps.cli` while allowing comments/docstrings to mention CLI modules.

## Dependency Direction

Allowed:

- `apps.cli.* -> javdb.storage.*`
- `apps.api.* -> javdb.storage.*`
- `javdb.storage.rollback.core -> javdb.storage.sessions.lifecycle_helpers`
- `javdb.storage.sessions.commit -> javdb.storage.sessions.lifecycle_helpers`
- `javdb.storage.sessions.lifecycle_helpers -> javdb.storage.db.*`
- `javdb.storage.sessions.lifecycle_helpers -> javdb.proxy.coordinator.movie_claim_client`

Forbidden:

- `javdb.storage.* -> apps.cli.*`
- production code importing `apps.cli.db._session_helpers` after Phase 3;
- production code importing `javdb.storage.rollback.session_helpers` after
  Phase 3.

## Behavior Invariants

This migration is structural. These behaviors must remain unchanged:

- `normalize_run_started_at` handles ISO timestamps, `Z`, and offset-aware
  inputs exactly as it does today.
- `find_run_sessions` keeps warn-and-empty error handling.
- `find_window_sessions` keeps the `raise_on_error` split.
- `read_session_pre_state` keeps fallback to `write_mode="audit"` when the
  lookup fails.
- `fanout_movie_claim` keeps coordinator-disabled behavior, retry behavior,
  unavailable handling, summary shape, logging, and client close behavior.
- JSONL append behavior remains unchanged.
- `GITHUB_OUTPUT` writing remains unchanged.
- run identity attachment remains unchanged.
- rollback CLI exit codes remain unchanged.
- commit CLI exit codes remain unchanged.
- API commit side effects remain unchanged.

## GitHub Output Helper Scope

`write_github_output` is workflow/CI flavored, but it stays in
`lifecycle_helpers` for this ADR because it is part of the shared session
lifecycle side-effect bundle today.

The ADR labels it a workflow side-effect adapter, not storage core. A future ADR
may move GitHub output and JSONL/reporting side effects into a workflow or
integrations package.

## Rollout

### Phase 1: Guard And Direct Storage Helper Imports

Implementation plan: `IMP-027`.

Actions:

- add architecture guard forbidding `javdb/storage/**/*.py` imports from
  `apps.cli`;
- change `apps.cli.db.commit_session` to import helpers from
  `javdb.storage.rollback.session_helpers`;
- keep `apps.cli.db._session_helpers` as a shim;
- update stale ADR-008/IMP-009 references with updated/superseded notes.

Required checks:

- architecture guard fails on real storage-to-CLI imports;
- `tests/unit/test_session_helpers.py` passes;
- rollback core/library tests pass;
- rollback CLI tests pass;
- commit CLI tests pass.

### Phase 2: Canonical Lifecycle Helpers

Implementation plan: `IMP-028`.

Actions:

- create `javdb.storage.sessions.lifecycle_helpers`;
- move helper implementation from `javdb.storage.rollback.session_helpers` to
  the new canonical module;
- convert `javdb.storage.rollback.session_helpers` to a re-export wrapper;
- convert `apps.cli.db._session_helpers` to re-export the new canonical module;
- migrate production callers to the canonical module:
  - `apps.cli.db.commit_session`
  - `javdb.storage.rollback.core`
  - `javdb.storage.sessions.commit`

Required checks:

- helper import/identity tests pass;
- session helper behavior tests pass;
- rollback core/library tests pass;
- rollback CLI tests pass;
- commit CLI tests pass;
- API commit path tests pass when they touch helper side effects.

### Phase 3: Delete Legacy Wrappers

Implementation plan: `IMP-029`.

Actions:

- delete `apps.cli.db._session_helpers`;
- delete `javdb.storage.rollback.session_helpers`;
- update test monkeypatch targets to `javdb.storage.sessions.lifecycle_helpers`;
- extend architecture guard to reject imports of the two deleted legacy paths;
- update docs and README references to the canonical lifecycle helper module.

Required checks:

- import deletion tests prove old wrapper paths are gone;
- architecture guard rejects old wrapper imports;
- session helper behavior tests pass through the new canonical path;
- rollback CLI tests pass;
- commit CLI tests pass.

## Documentation Updates

ADR-014 should document the final dependency rule and the three-phase rollout.

ADR-008 and IMP-009 should receive updated notes rather than full rewrites:
their historical statement was correct when written, but the code has already
partially moved. The note should say the original storage-to-CLI import is
resolved and ADR-014 tracks final convergence.

`apps/cli/db/README.md` and storage rollback/session docs should be updated in
Phase 2/3 to name `javdb.storage.sessions.lifecycle_helpers` as the canonical
helper path.

## Testing Strategy

Phase 1:

- architecture import guard;
- `tests/unit/test_session_helpers.py`;
- rollback core/library tests;
- rollback CLI tests;
- commit CLI tests.

Phase 2:

- helper canonical import/identity tests;
- `tests/unit/test_session_helpers.py`;
- rollback core/library tests;
- rollback CLI tests;
- commit CLI tests;
- API commit path tests that cover helper side effects.

Phase 3:

- architecture guard extended for deleted wrappers;
- import deletion tests;
- `tests/unit/test_session_helpers.py`;
- rollback CLI tests;
- commit CLI tests.

Optional final verification:

- broader storage + CLI related test set.

## Future ADR Candidates

- Move GitHub Actions output helpers and JSONL workflow reporting side effects
  into a workflow/integrations package.
- Continue extracting user-facing CLI orchestration into reusable libraries
  where API or workflow code needs the same behavior.
