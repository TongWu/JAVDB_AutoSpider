# ADR-014: Storage CLI Layering

**Status**: Accepted - implementation pending
**Date**: 2026-05-20
**Deciders**: Storage CLI layering brainstorming and grill session
**Related Implementation Plans**: [IMP-027](../impl/IMP-027-storage-cli-layering-phase1-guard.md) (Phase 1 - guard and direct storage imports), [IMP-028](../impl/IMP-028-storage-cli-layering-phase2-lifecycle-helpers.md) (Phase 2 - canonical lifecycle helpers), [IMP-029](../impl/IMP-029-storage-cli-layering-phase3-delete-legacy-wrappers.md) (Phase 3 - delete legacy wrappers)

## Outstanding Work

- Phase 1 - add the storage-to-CLI import guard, move the remaining commit-session CLI helper import to the storage helper, and update stale ADR/IMP notes.
- Phase 2 - move the shared helper implementation to `javdb.storage.sessions.lifecycle_helpers` and migrate production callers to the canonical path.
- Phase 3 - delete `apps.cli.db._session_helpers` and `javdb.storage.rollback.session_helpers`, then guard against both paths returning.

---

## Context

ADR-008 identified a storage layering inversion: rollback library code imported
helper code from `apps.cli.db._session_helpers`. That original issue has been
partially fixed:

- `javdb.storage.rollback.core` now imports from
  `javdb.storage.rollback.session_helpers`;
- `apps.cli.db._session_helpers` is a re-export shim;
- the helper implementation lives under `javdb.storage.rollback`.

The remaining issue is smaller but still important:

- `apps.cli.db.commit_session` still imports from the CLI shim;
- the canonical helper path is named as rollback-specific even though the code
  is shared by rollback, commit, API commit side effects, and session lifecycle
  operations;
- no architecture guard prevents `javdb.storage.*` from importing
  `apps.cli.*` again.

## Non-Negotiable Layering Invariant

`javdb.storage.*` must not import `apps.cli.*`.

CLI modules may import storage/library modules. Storage modules must remain
usable without importing CLI wrappers, CLI argument parsing, or CLI helper
shims.

## Non-Negotiable Runtime Invariant

This ADR is behavior-preserving.

The migration must not change rollback, commit, API commit side effects,
pending-mode, MovieClaim fanout, JSONL emission, `GITHUB_OUTPUT`, run identity,
timestamp parsing, session lookup, pre-state lookup, logging, or exit-code
semantics.

## Decision

### D1. Make Dependency Direction Explicit

Allowed:

- `apps.cli.* -> javdb.storage.*`
- `apps.api.* -> javdb.storage.*`
- `javdb.storage.rollback.core -> javdb.storage.sessions.lifecycle_helpers`
- `javdb.storage.sessions.commit -> javdb.storage.sessions.lifecycle_helpers`
- `javdb.storage.sessions.lifecycle_helpers -> javdb.storage.db.*`
- `javdb.storage.sessions.lifecycle_helpers -> javdb.proxy.coordinator.movie_claim_client`

Forbidden:

- `javdb.storage.* -> apps.cli.*`
- production imports of `apps.cli.db._session_helpers` after Phase 3
- production imports of `javdb.storage.rollback.session_helpers` after Phase 3

### D2. Add A Lightweight Architecture Guard In Phase 1

Phase 1 adds an AST-based test that scans `javdb/storage/**/*.py` and rejects
real imports from `apps.cli`.

The guard intentionally checks Python imports, not comments or docstrings, so
documentation can still mention CLI module names.

### D3. Complete The Remaining Direct Import Cleanup In Phase 1

`apps.cli.db.commit_session` stops importing from `apps.cli.db._session_helpers`
and imports directly from `javdb.storage.rollback.session_helpers`.

`apps.cli.db._session_helpers` remains as a temporary CLI compatibility shim.

### D4. Use A Neutral Canonical Helper Module In Phase 2

The canonical helper implementation moves to:

```text
javdb.storage.sessions.lifecycle_helpers
```

This name reflects the real domain: the helpers are shared session lifecycle
scaffolding, not rollback-only code.

### D5. Keep Legacy Wrappers During Phase 2

Phase 2 keeps both legacy paths as re-export wrappers:

- `apps.cli.db._session_helpers`
- `javdb.storage.rollback.session_helpers`

Production callers migrate to `javdb.storage.sessions.lifecycle_helpers` in the
same phase.

### D6. Delete Both Legacy Wrappers In Phase 3

Phase 3 deletes:

- `apps.cli.db._session_helpers`
- `javdb.storage.rollback.session_helpers`

Tests, monkeypatch targets, docs, and README references move to
`javdb.storage.sessions.lifecycle_helpers`.

### D7. Keep `write_github_output` In The Lifecycle Helper For This ADR

`write_github_output` is workflow-flavored, but it remains in
`lifecycle_helpers` for this ADR because it is part of the current session
lifecycle side-effect bundle.

The final module documents it as a workflow side-effect adapter, not storage
core. Moving GitHub output, JSONL, or reporting side effects to a workflow or
integrations package is follow-up ADR scope.

### D8. Preserve Helper Semantics Exactly

Every phase must preserve the observable behavior of:

- `normalize_run_started_at`
- `find_run_sessions`
- `find_window_sessions`
- `read_session_pre_state`
- `fanout_movie_claim`
- JSONL append helpers
- `GITHUB_OUTPUT` writing
- run identity attachment
- rollback CLI exit codes
- commit CLI exit codes
- API commit side effects

### D9. Update Historical Docs With Supersession Notes

ADR-008 and IMP-009 were correct when written, but the code has moved since
then. They receive short update notes that point to this ADR for the final
convergence work.

### D10. One ADR, Three Phase Plans

This ADR rolls out through three independent implementation plans:

- [IMP-027](../impl/IMP-027-storage-cli-layering-phase1-guard.md)
- [IMP-028](../impl/IMP-028-storage-cli-layering-phase2-lifecycle-helpers.md)
- [IMP-029](../impl/IMP-029-storage-cli-layering-phase3-delete-legacy-wrappers.md)

Each phase has its own test gate and can be implemented independently.

## Final Shape

```text
apps.cli.db.rollback / apps.cli.db.commit_session
  -> javdb.storage.rollback.core / javdb.storage.sessions.commit
  -> javdb.storage.sessions.lifecycle_helpers
  -> javdb.storage.db.* / MovieClaim client / filesystem and env side effects
```

The forbidden shape is:

```text
javdb.storage.* -> apps.cli.*
```

## Follow-Up ADR Scope

- Move GitHub Actions output helpers out of session lifecycle helpers.
- Move JSONL/reporting side effects into a workflow or integrations package.
- Broader CLI-to-library extraction for unrelated DB CLIs.

## Consequences

### Positive

- Storage/library code no longer depends on CLI helper modules.
- Shared session lifecycle helpers get a neutral canonical home.
- Architecture tests make the layering rule executable.
- Legacy wrappers have a defined deletion phase.

### Negative

- Phase 2 and Phase 3 require import churn across production code and tests.
- Compatibility wrappers exist for one phase after the canonical move.
- `write_github_output` remains in a storage-adjacent helper until a separate
  workflow-side-effect ADR handles it.

### Neutral

- This ADR does not redesign rollback, commit, pending-mode, MovieClaim, JSONL,
  or GitHub Actions output behavior. It changes ownership and dependency
  direction only.
