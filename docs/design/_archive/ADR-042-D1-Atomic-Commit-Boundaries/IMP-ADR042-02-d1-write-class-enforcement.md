# IMP-ADR042-02: D1 Write-Class Enforcement - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-042](ADR-042-d1-atomic-commit-boundaries.md) - this is **Phase 2** (the D6-enforcement item deferred by [IMP-ADR042-01](IMP-ADR042-01-d1-atomic-commit-boundaries.md)).

**Status:** Completed — 2026-06-01 (validator + unit test + workflow shipped; `Write-Class:` convention documented; ADR template field added; ADR-042 updated, all bilingual where required).

**Goal:** Turn ADR-042 **D6** from documented guidance into a real gate. Every new D1 *write surface* (a `CREATE TABLE` migration) must declare its write class, and CI must fail closed when it doesn't — so the classification can no longer be silently skipped.

**Architecture:** Two hooks, deliberately asymmetric:

1. **Migration header (enforced).** `javdb/migrations/d1/*.sql` is the canonical schema-change surface (per CLAUDE.md, schema lands on D1 first via these files), so it is the only place where *every* new write surface is guaranteed to appear. A new surface is introduced precisely by `CREATE TABLE`; column adds inherit the parent table's class, and indexes / version bumps / drops are not write surfaces. A `-- Write-Class: <class>` header on such migrations is checked on every PR by a dependency-free Python validator + a thin workflow, mirroring the `pr-branch-check.yml` fail-on-PR precedent. The classification habit already existed informally (e.g. `add_pipeline_event.sql`: *"Additive, append-only. Does NOT change the authoritative pending->commit path."*) — this just structures it.
2. **ADR template field (soft prompt).** An ADR can introduce a write without an obvious migration in the same change, and most ADRs don't touch D1 at all, so the ADR hook is a non-CI `**D1 Write Class:**` field with an `n/a` default that simply forces the author to consider the question at design-review time.

`CONTEXT.md` already owns the canonical vocabulary (from Phase 1); this phase adds the *enforcement mechanism* and points to it, without re-defining the classes.

**Tech Stack:** Python 3 (stdlib only — `re`, `argparse`, `subprocess`, `pathlib`), `pytest`, GitHub Actions, Markdown.

**Terminology note:** The migration tag accepts only the three ADR-042 classes (`authoritative` / `additive` / `diagnostic`). `n/a` is an **ADR-template-only** value (most ADRs are not D1 writes) and is explicitly rejected by the validator for a concrete `CREATE TABLE` migration. One migration file = one write class.

---

## File Structure

| Path | Create/Modify/Delete | Responsibility |
| --- | --- | --- |
| `scripts/ci/validate_d1_write_class.py` | Create | Dependency-free validator. Pure `find_violations((path, content)…)` core + a `--base <ref>` git wrapper (added `.sql` under the migrations dir) and a `--paths …` mode for local/testing. Emits `::error file=…::` and exits non-zero on violation. |
| `tests/unit/test_validate_d1_write_class.py` | Create | Unit test for the pure core + CLI: missing tag fails, valid tag passes, non-`CREATE TABLE` ignored, invalid / `n/a` value rejected, multi-table single-tag passes, non-`.sql` skipped, case-insensitive keyword. |
| `.github/workflows/validate-d1-write-class.yml` | Create | Runs on every `pull_request`; `fetch-depth: 0`; calls the validator with `--base ${{ github.event.pull_request.base.sha }}`. No-ops (exit 0) when no migration is added, so it is safe as a required check. |
| `javdb/migrations/README.md` | Modify | Document the `Write-Class:` header convention, the one-class-per-file rule, the exempt cases, and the CI gate. |
| `CONTEXT.md` | Modify | One-line enforcement pointer appended to the existing `写入边界分类` section (no re-definition). |
| `docs/design/_templates/ADR-TEMPLATE.md` + `.zh.md` | Modify | Add the `**D1 Write Class:**` header field (default `n/a`). |
| `ADR-042-d1-atomic-commit-boundaries.md` + `.zh.md` | Modify | D6 `Enforcement (Phase 2)` note, Phase 2 roadmap row, Related-IMP link, status-log entry. |

## Task 1: Ship the validator + unit test

**Files:** Create `scripts/ci/validate_d1_write_class.py`, `tests/unit/test_validate_d1_write_class.py`

- [x] **Step 1: Write the validator.** Pure core `find_violations`: for each `.sql` file containing `CREATE TABLE` (case-insensitive), require ≥1 `-- Write-Class: <class>` line whose value is in {`authoritative`, `additive`, `diagnostic`}. Missing → violation; invalid value (incl. `n/a`) → violation. `--base` resolves added files via `git diff --name-only --diff-filter=A <base> HEAD` filtered to `javdb/migrations/d1/*.sql`; `--paths` validates explicit files.
- [x] **Step 2: Write the unit test** (`from scripts.ci import validate_d1_write_class`, matching `test_select_tests.py`'s import style).
- [x] **Step 3: Verify.**

```bash
python3 -m pytest tests/unit/test_validate_d1_write_class.py -q
```

Expected: all green. (Result: 14 passed.)

## Task 2: Add the CI workflow

**Files:** Create `.github/workflows/validate-d1-write-class.yml`

- [x] **Step 1:** `pull_request` trigger, `permissions: contents: read`, `actions/checkout` with `fetch-depth: 0` (pinned to the repo's SHA convention), run the validator with `--base ${{ github.event.pull_request.base.sha }}`.
- [x] **Step 2: Verify the gate end-to-end locally** (the workflow itself proves out on the first real PR):

```bash
# happy path: no added migrations on this branch vs main → exit 0
python3 scripts/ci/validate_d1_write_class.py --base main
# negative: an untagged CREATE TABLE migration → ::error:: + exit 1
tmp=$(mktemp -d); printf -- '-- x\nCREATE TABLE Bad (id TEXT);\n' > "$tmp/bad.sql"
python3 scripts/ci/validate_d1_write_class.py --paths "$tmp/bad.sql"; echo "exit=$?"; rm -rf "$tmp"
```

Expected: first prints "No newly-added…" exit 0; second prints an `::error::` annotation and exits 1. (Both confirmed.)

## Task 3: Document the convention

**Files:** Modify `javdb/migrations/README.md`, `CONTEXT.md`

- [x] **Step 1:** Add a `## Write-Class header convention (ADR-042 D6)` section to the migrations README: the `-- Write-Class:` syntax, allowed values, one-class-per-file rule, exempt cases (column adds / indexes / version bumps / drops), and the CI gate (added-files-only, existing migrations grandfathered).
- [x] **Step 2:** Append a one-line enforcement pointer to the existing `写入边界分类` section of `CONTEXT.md` (link to the migrations README; do not re-define the classes).

## Task 4: Add the ADR template field

**Files:** Modify `docs/design/_templates/ADR-TEMPLATE.md`, `docs/design/_templates/ADR-TEMPLATE.zh.md`

- [x] **Step 1:** Add `**D1 Write Class:** authoritative | additive | diagnostic | n/a` (with an HTML-comment hint, default `n/a`) under the `Related Implementation Plans` line in both the English and Chinese templates.

## Task 5: Wire D6 to its enforcement in ADR-042

**Files:** Modify `ADR-042-d1-atomic-commit-boundaries.md` + `.zh.md`

- [x] **Step 1:** Add an `**Enforcement (Phase 2).**` note under D6 (both points; the exempt cases; link to this IMP).
- [x] **Step 2:** Add the Phase 2 ✅ roadmap row, the Related-IMP link, and a status-log entry. Mirror all of it in `.zh.md` (code/paths verbatim, prose translated).

## Task 6: Final validation

- [x] **Step 1: Markdown / whitespace hygiene.**

```bash
git diff --check
```

- [x] **Step 2: Bilingual pairing + no banned term.**

```bash
rg -n "IMP-ADR042-02|Enforcement \(Phase 2\)|强制方式（Phase 2）" docs/design/ADR-042-D1-Atomic-Commit-Boundaries/
rg -ni "logical acid|逻辑 ACID" docs/design/ADR-042-D1-Atomic-Commit-Boundaries/ CONTEXT.md   # expect: no hits
```

- [x] **Step 3: Confirm only intended files changed** (`git status --short`): the validator, its test, the workflow, the two READMEs/CONTEXT, the two templates, the two ADR files, and this IMP.

## Rollback

Delete `scripts/ci/validate_d1_write_class.py`, its test, and `.github/workflows/validate-d1-write-class.yml`; revert the doc edits. No schema, migration, or runtime-write behavior changes in this phase — the gate is build-time only.

## Out of Scope

- **Backfilling existing migrations.** The gate inspects only files added in a PR; the ~15 existing `CREATE TABLE` migrations are grandfathered (their classes are already evident in prose and in ADR-042).
- **Gating code-level writes to existing tables.** A new repo method that writes to an already-classified table is not a new *surface*; we gate the schema/new-table surface, not every write statement.
- **CI-enforcing the ADR template field.** The ADR field is a soft design-time prompt by decision; only the migration header is fail-closed.
- **Per-table mixed-class syntax.** One migration file carries one write class; split the migration if you need different classes.
