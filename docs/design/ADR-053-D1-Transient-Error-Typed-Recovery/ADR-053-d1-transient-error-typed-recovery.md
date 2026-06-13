# ADR-053: Promote `D1TransientError`'s Monkey-Patched Recovery Signals to a Declared Typed Interface

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — execution in [IMP-ADR053-01](IMP-ADR053-01-d1-transient-error-typed-recovery.md) (single PR) |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-009](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md) (created the `D1TransientError` classifier — owns *which* errors are transient, not the recovery-signal interface), [ADR-042](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) (the recovery/commit-boundary **policy** these signals feed — unchanged), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md) (`d1_port.py` is the access port that sets the signals) |

> Originated from the 2026-06-13 architecture review (Candidate 6 — "promote `D1TransientError`'s recovery attributes to a typed interface"): [architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html).

## Context

`D1TransientError` (`javdb/storage/d1_client.py:137`) has an **empty class body** (just a docstring; chain `D1TransientError(D1Error(RuntimeError))`). Yet its instances carry **cross-module recovery signals** monkey-patched on at the raise/handle sites and read elsewhere via defensive `getattr` — an interface that exists but is invisible to the type system:

| Signal | Set on | Set sites (`# type: ignore[attr-defined]`) | Read site |
| --- | --- | --- | --- |
| `d1_recovery_outbox_required` | `D1TransientError` | `d1_port.py:292`, `:596` | `dual_connection._requires_durable_recovery:1125` |
| `d1_recovery_durable` | `D1TransientError` | `d1_port.py:293`, `:601`, `:608` | `dual_connection:1126` |
| `d1_recovery_blocker` | **bare `RuntimeError`** | `d1_port.py:263` | `dual_connection._blocks_queued_recovery_flush:1133` |
| `retry_after` | `D1TransientError` | `d1_port.py:448` | `d1_port._compute_backoff:490` (intra-module) |
| `is_export_lock` | `D1TransientError` | `d1_port.py:459`, `:483` | `d1_port:507` (intra-module) |

Two concrete defects fall out of the invisible interface:

1. **A typo silently disables recovery.** `_requires_durable_recovery` reads `getattr(exc, "d1_recovery_outbox_required", False)` — misspell the signal at either end and it defaults to `False`, **silently turning off outbox recovery with no error**. This is the exact failure class a declared field would make a type error.
2. **The blocker signal is set on a bare `RuntimeError`, not `D1TransientError`** (`d1_port.py:259-263`), so it cannot be a field on `D1TransientError`. Its reader even carries a **string-match fallback** — `"unresolved D1 recovery work" in str(exc)` (`dual_connection:1134`) — a belt-and-suspenders that exists *because* the monkey-patched attribute is fragile.

The `# type: ignore[attr-defined]` on every set site is the compiler telling us the interface is undeclared.

## Decision

Declare every recovery signal as a typed member, and replace the `getattr`/string-match reads with attribute/`isinstance` checks.

### Design Decisions

**D1. Declare the four `D1TransientError` signals as class-level typed fields with defaults.** On `D1TransientError`:

```python
class D1TransientError(D1Error):
    d1_recovery_outbox_required: bool = False
    d1_recovery_durable: bool = False
    is_export_lock: bool = False
    retry_after: Optional[str] = None
```

The signals are determined **after** the error is constructed (in the `except` handler, based on whether recovery was attempted), so they stay as **post-construction assignments** at the raise/handle sites — but now declared (no `__init__` kwargs, which would not fit the flow; no `# type: ignore`). The immutable defaults make every read safe.

**D2. The blocker becomes a typed `D1RecoveryBlockerError(RuntimeError)` subclass.** Because `d1_recovery_blocker` is raised on a bare `RuntimeError` (not `D1TransientError`), the typed representation is a dedicated exception class in `d1_client.py` (alongside `D1Error`). `d1_port.py:259-263` raises `D1RecoveryBlockerError("unresolved D1 recovery work for ordering key …")` instead of monkey-patching a flag onto a generic `RuntimeError`.

**D3. Reads become typed; delete the string-match fallback.** `_requires_durable_recovery` → `isinstance(exc, D1TransientError) and exc.d1_recovery_outbox_required and not exc.d1_recovery_durable`. `_blocks_queued_recovery_flush` → `isinstance(exc, D1RecoveryBlockerError)`. Since D2 makes `D1RecoveryBlockerError` the *sole* raise site, the fragile `"unresolved D1 recovery work" in str(exc)` fallback is now redundant and is **removed** — the typed check is authoritative.

**D4. Include the two intra-module signals (`retry_after`, `is_export_lock`).** Though only set and read within `d1_port.py` (lower invisible-interface risk), declaring them on `D1TransientError` (D1) removes the remaining `# type: ignore`s and makes the whole recovery interface visible in one place — a small, consistent completion rather than leaving two stragglers.

**D5. No behaviour change.** The signals carry the same meanings, set at the same points, read for the same decisions. Only their *representation* changes: monkey-patch → declared field, bare-`RuntimeError`+flag → typed subclass, `getattr`/string-match → attribute/`isinstance`. The recovery policy (ADR-042) and the transient classification (ADR-009) are untouched.

## Consequences

### Positive

- **interface** — `D1TransientError`'s recovery contract is declared in one place; the class definition is the source of truth, not six scattered assignments.
- **correctness** — a misspelled signal is now a type/attribute error, not a silent `False` that disables outbox recovery; the fragile string-match coupling is deleted.
- **locality** — all recovery-signal semantics live on the error classes; readers state intent (`isinstance(exc, D1RecoveryBlockerError)`) instead of duck-typing.
- **tests hit the real seam** — construct `D1TransientError(...)` with `d1_recovery_outbox_required=...` (or set the field) and `D1RecoveryBlockerError`; no monkey-patching in tests.
- **the `# type: ignore[attr-defined]`s vanish** — 8 suppressions removed; the type checker now sees the interface.

### Negative

- **A new exception class + four declared fields.** Trivial; it replaces an undeclared, monkey-patched contract.
- **Class-level mutable-looking defaults.** Mitigated: all defaults are immutable (`bool`/`None`), so the class-attribute-as-default idiom is safe.

### Risks

- **A reader assumed the signal on a non-`D1TransientError` exc.** Mitigated: the grounding enumerated every set/read site; `outbox_required`/`durable` are only set on `D1TransientError`, `blocker` only via the new subclass. The `isinstance` guards match the actual raise types.
- **Removing the string fallback misses a blocker raised elsewhere.** Mitigated: `d1_port.py:259-263` is the *sole* site that signalled a blocker; converting it to `D1RecoveryBlockerError` makes `isinstance` complete.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 (only) | [IMP-ADR053-01](IMP-ADR053-01-d1-transient-error-typed-recovery.md) | Typed fields on `D1TransientError`; `D1RecoveryBlockerError` subclass; raise/read sites converted; string fallback deleted; `# type: ignore`s removed; unit tests for the typed signals; CONTEXT.md terms | — |

### Explicit non-goals (YAGNI)

- **Not** changing recovery policy or backoff behaviour (ADR-042) — same decisions, typed inputs.
- **Not** changing transient/permanent classification (ADR-009).
- **Not** introducing a `D1RecoveryState` dataclass — four bools/strings on the exception are enough (see Alternatives).

## Domain Language (additions for CONTEXT.md)

- **D1 recovery signals** — the typed members on `D1TransientError` that tell the dual facade how to recover a failed D1 write: `d1_recovery_outbox_required` (recovery was attempted via the outbox), `d1_recovery_durable` (the outbox event was persisted), `retry_after` / `is_export_lock` (backoff hints). Declared fields with safe defaults (ADR-053) — formerly monkey-patched with `# type: ignore`.
- **`D1RecoveryBlockerError`** — a `RuntimeError` subclass raised when unresolved D1 recovery work blocks flushing queued writes; the typed replacement for the former `d1_recovery_blocker` flag monkey-patched onto a bare `RuntimeError`. `dual_connection` detects it via `isinstance`, not a string match.

## Alternatives Considered

- **A `D1RecoveryState` dataclass attached as `exc.recovery`.** Rejected: overkill for two/four scalar signals; the reads (`exc.recovery.outbox_required`) are no clearer than declared fields and add an indirection.
- **`__init__` kwargs on `D1TransientError`.** Rejected (D1): the signals are determined *after* construction in the `except` handler, not at raise time; post-construction assignment to declared fields fits the actual flow.
- **Keep `getattr` reads with defaults.** Rejected (D3): that *is* the silent-`False`-on-typo defect this ADR removes.
- **Keep the string-match fallback.** Rejected (D3): with `D1RecoveryBlockerError` the sole raise site, `isinstance` is authoritative; the fallback retains the fragile coupling.

## References

- [ADR-009 — D1 Drift Classifier & Diagnose](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-042 — D1 Atomic Commit Boundaries](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)
- [ADR-010 — D1 Access Port](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- 2026-06-13 architecture review: [architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html)

## Status Log

- 2026-06-13: Proposed (from the 2026-06-13 architecture review, Candidate 6). Grounding verified: `D1TransientError` body is empty; 5 signals monkey-patched across `d1_port`/`dual_connection` with 8 `# type: ignore`; `d1_recovery_blocker` is set on a **bare `RuntimeError`** (hence the new subclass); `_blocks_queued_recovery_flush` carries a fragile `str(exc)` fallback. Decided (grilling): declare all 5 (3 cross-module + 2 intra-module) as typed members; `D1RecoveryBlockerError(RuntimeError)`; convert reads to `isinstance`/attribute; **delete** the string fallback. ADR-009 (classifier) and ADR-042 (policy) own classification/policy, not the signal representation — no collision. IMP-ADR053-01 pending.
