# ADR-006: Pending Mode Default Rollout + Retirement of Audit Auto-Fallback

**Status**: Completed — PR-A/C/D/E merged; **PR-F sign-off completed on 2026-05-21** (operator-approved 7-day clean bake bypass)
**Date**: 2026-05-16
**Deciders**: Architecture depth-pass round 2 (prerequisite for [ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md))
**Successor Trigger**: ADR-006 is complete; ADR-005 PR-2 is unblocked (PR-1 already shipped). The bypass does not authorize ADR-005 PR-4 audit-table deletion; the D10 trio must pass first.

## Outstanding Work

- **PR-F (sign-off) ✅** — on 2026-05-21, the maintainer approved bypassing the remainder of the 30-day window after one clean week and inserted "ADR-006 sign-off completed on 2026-05-21" at the top of ADR-005.
- `BakeCheck.yml` (`cron: 0 4 * * *`, `since: 2026-05-16`) remains as regression monitoring; the D10 trio must still be re-run before ADR-005 PR-4.

## Amendments

- **2026-05-16 amendment 1**: **PR-B cancelled**. The original plan to "change the SQLite schema `WriteMode TEXT DEFAULT 'audit'` to `DEFAULT 'pending'`" was rejected — investigation found that this DEFAULT only fires on two **historical data import paths**: the v5→v6 migration and the csv_to_sqlite backfill. Those paths handle genuinely Audit Mode historical sessions, where `'audit'` is the **correct** label, not a "target default". The normal write path ([`db_reports.py:128`](../../../javdb/storage/db/db_reports.py)) always passes `WriteMode` explicitly, so the DEFAULT never fires. Changing the DEFAULT would instead mislabel historical data. The schema DEFAULT stays as `'audit'`, serving as a defensive label meaning "assume legacy audit session when WriteMode is unknown". The PR sequence shrinks from 6 to 5.

- **2026-05-17 amendment 2**: After ADR-006 was accepted, [ADR-007](../ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md) reorganised the Python namespace (`packages/python/javdb_*` → top-level `javdb/`). Any PRs from this ADR's implementation order that have not yet merged when ADR-007 Phase 1 lands must operate on the new paths:

  - `packages/python/javdb_platform/db_session.py:188` → `javdb/storage/db/db_session.py:188`
  - `packages/python/javdb_platform/db_reports.py:128` → `javdb/storage/db/db_reports.py:128`
  - `scripts/pending_mode_alert_and_pause.py` → `apps/cli/db/pending_alert.py` (moved by ADR-007 Phase 2)
  - Workflow command `python3 -m scripts.pending_mode_alert_and_pause` → `python3 -m apps.cli.db.pending_alert` (updated in same Phase 2 PR)
  - Workflow command `python3 -m scripts.aggregate_pending_health` → `python3 -m apps.cli.db.pending_health`

  Path rename only — the D1–D5 decision, the 30-day bake gate, and the relationship to ADR-005 are unchanged.

- **2026-05-17 amendment 3**: **D5 carve-out for ADR-005 PR-1.** When D5 was written it blanket-blocked all ADR-005 PRs during bake. On review, this is over-broad. The bake gate exists to protect the three D10 monitoring metrics (`audit_session_count`, `orphan_audit_rows`, `pause_trigger_count`); a PR can only contaminate those metrics if it touches the write path, the WriteMode resolution, the pause mechanism, or the schema. **ADR-005 PR-1 touches none of these** — it adds new `HistoryRepo` / `OperationsRepo` / `ReportsRepo` / `StatsRepo` classes alongside the existing function family with zero caller migration. Its monitoring footprint is provably nil. Carve-out:

  | ADR-005 PR | Blocked during bake? | Reason |
  |---|---|---|
  | **PR-1** (introduce Repo classes alongside function family, zero caller change) | **No** — bake-safe per this amendment | Purely additive; no write/schema/workflow effect |
  | PR-2 (`db.py` internally forwards to Repos — dual-write phase) | **Yes** | Touches the actual write path; monitoring could be confounded |
  | PR-3 (migrate callers) | **Yes** | Same |
  | PR-4 (drop audit tables, remove audit code) | **Yes** | Touches schema + write path |
  | PR-5 (delete `db.py`) | **Yes** | Final cleanup post-retirement |

  The blanket ban in D5 is replaced by this per-PR matrix. PR-1 may start once this amendment is recorded; PR-2 onward still requires the 30-day bake + D10 sign-off.

- **2026-05-21 amendment 4**: **PR-F sign-off uses an operator-approved 7-day clean bake bypass.** The maintainer explicitly bypassed the remainder of D4's 30-day window after one clean week and allowed ADR-005 PR-2 to proceed. This bypass only lifts the PR-2 start blocker; it does not authorize early audit-table deletion. ADR-005 PR-4 still requires the D10 trio to pass before execution.

---

## Context

Immediately after ADR-005 was drafted, a D10 Audit Mode retirement safety check was run. **Two items failed**:

| Gate item | Status | Measured |
|---|---|---|
| Last-30-day `WriteMode='audit'` count is 0 | FAIL | Last 30 days: audit=54 / pending=13; all-time: audit=354 / pending=13 |
| No orphan audit rows | PASS | 0 |
| Workflows removed audit option 7 days ago | FAIL | 3 workflows still list `audit` as a valid value; DailyIngestion has a live auto-fallback to audit |

### Documentation inaccuracies found alongside

| Source | Claim | Reality |
|---|---|---|
| CLAUDE.md L88 / CONTEXT.md "Write Mode" | "Pending Mode (default)" | `db_session.py:188` `return "audit"` is the code fallback |
| Same | "Audit Mode deprecated, scheduled to be removed 2026-08-13" | 80% of live sessions are still audit |
| ADR-001 docstring | Phase 3 / pending already default | In practice audit is the main path |

### Why this state

- `db_session.py:188` **defaults to returning `'audit'`** when there is no env var, no explicit override, and no config file override — a legacy carry-over: when the Pending tables first shipped, audit was kept as the default to protect the new code.
- `.github/workflows/DailyIngestion.yml:1093` implements an **auto-fallback** that, on a critical pending alert, auto-commits `.publish-config.yml` to switch back to audit for 24 hours; executed by `scripts/pending_mode_auto_fallback.py` (212 lines). This is the operational safety net for when Pending Mode is unstable.
- The workflow `write_mode_override` input still accepts `'audit'` as a valid value; in the operator's mental model audit/pending remain dual options.

ADR-005's D2(c) "fully retire Audit Mode" assumed the documentation was true, but audit is in fact **the main path + a safety net** — hard retirement would remove the safety net and require changing the runtime mode of 80% of sessions.

---

## Decision

Lift the audit-retirement prerequisites out of ADR-005 into this dedicated ADR. Drive Pending Mode to 100% in the following 4 steps, leave a 30-day bake period, then release the ADR-005 D10 gate.

### D1: Change the code default to pending

Changes:
- `javdb/storage/db/db_session.py:188` `return "audit"` → `return "pending"` (path updated per amendment 2)
- Add a unit test that pins the new resolution order

**Note**: The original D1 also included "change the SQLite schema `WriteMode` column DEFAULT to `'pending'`", which is cancelled in amendment 1. See the amendment above for rationale — the schema DEFAULT only serves historical migration paths, where `'audit'` is semantically correct. The runtime default is now controlled by the Python fallback alone.

### D2: Change the workflow defaults to pending

Modify the `write_mode_override` input in `DailyIngestion.yml` / `AdHocIngestion.yml` / `TestIngestion.yml`:
- `options:` list changes from `['', 'pending', 'audit']` to `['', 'pending']` (remove audit; keep pending + empty string)
- Description text changes from `"... (audit | pending)"` to `"... (pending only)"`
- Remove the `pending_mode_disabled_until` field in `.publish-config.yml` (the DailyIngestion runtime config) and prune the related conditional branches

### D3: Redesign auto-fallback — don't switch to audit, alert and pause instead

Rename `scripts/pending_mode_auto_fallback.py` to `scripts/pending_mode_alert_and_pause.py`, with new behaviour:
- On a critical pending alert, **do not** switch to audit
- Instead: send an alert email + write `pipeline_paused_until: <timestamp+24h>` into `.publish-config.yml`
- Add a gate in the `DailyIngestion.yml` setup step: when `pipeline_paused_until` is in the future, `exit 0` directly (job succeeds but skips the run)
- The operator reviews the alert, fixes the Pending Mode root cause, and manually clears `pipeline_paused_until` to resume

**Rationale**: The audit fallback turned Pending failures into "known risks" we silently tolerated. Switching to "alert + pause" forces the root cause to actually be fixed. If it cannot be fixed, that is itself an explicit decision, not a silent slide back onto the audit path.

### D4: 30-day bake period + exit verification

After D1-D3 ship, **bake for at least 30 days** unless an operator explicitly approves a shorter clean bake. During the bake, operations monitors:
- Daily check: `SELECT COUNT(*) FROM ReportSessions WHERE WriteMode='audit' AND DateTimeCreated > date('now','-1 day')` should hold steady at 0
- Check the trigger count of `scripts/pending_mode_alert_and_pause.py` — more than 1/month indicates an unfixed defect in Pending Mode
- After the bake or operator-approved bypass, re-run the D10 trio before ADR-005 PR-4; passing all three is still required before audit-table deletion.

### D5: Block all ADR-005 PRs during the bake

> **See amendments 3 and 4 above.** This decision was first narrowed so PR-1 was carved out as bake-safe, then PR-F sign-off on 2026-05-21 used an operator-approved 7-day clean bake bypass to unblock PR-2. PR-4 audit-table deletion still requires the D10 trio to pass first.

ADR-005's PR-1 shipped as a bake-safe additive change. ADR-005 PR-2 may proceed after the 2026-05-21 sign-off bypass. ADR-005 PR-4 remains outside the bypass and may only start after all three D10 items sign off.

---

## Alternatives Considered

### Alternative A: Keep the audit auto-fallback permanently as a safety net

**Rejected**: The very existence of the safety net gives Pending Mode root-cause bugs a permanent "way around". When maintainers see the alert, their first reaction is "the fallback caught it, fix tomorrow", and the bug never actually gets fixed. After ADR-005, having HistoryRepo carry this branch would also break D5 (simple signature).

### Alternative B: Keep the audit auto-fallback but move it after ADR-005

**Rejected**: If a Pending alert fires during ADR-005 execution, without the fallback the result is a production outage. The bake period must complete before the restructure, forcing Pending Mode to prove itself reliable with no safety net.

### Alternative C: Bake for 7 days / 14 days

**Rejected**: Current run frequency is roughly daily; 30 days = 30 daily runs + several adhoc runs, enough to cover monthly cron / weekends / holidays variations. A sample size under 30 days is insufficient to declare stability at the ~1% audit failure-rate order of magnitude.

---

## Implementation Order (PR Sequence)

```
PR-A  Code default to pending: db_session.py:188 + new tests/unit/test_default_write_mode.py  [merged #35]
      Verify existing runs are not broken (legacy audit sessions can still complete commit/rollback)

PR-B  Schema default switch                                                                   [cancelled, see amendment 1]
      The original plan was a v14 migration changing the WriteMode column DEFAULT to 'pending'.
      Investigation found the DEFAULT only serves historical migration / backfill paths,
      where 'audit' is the correct label. Skip.

PR-C  Workflow config to pending: 3 workflows remove the audit option + description updates

PR-D  Auto-fallback redesign: pending_mode_alert_and_pause.py replaces
      pending_mode_auto_fallback.py; DailyIngestion.yml setup step adds the pause gate

PR-E  Fix the "pending is default" inaccuracy in CONTEXT.md / CLAUDE.md / ADR-001 docstring   [merged #34]
      (independent small PR, may land in parallel with PR-A; no need to wait for the bake)

PR-F  Sign-off PR completed on 2026-05-21 via operator-approved 7-day clean bake bypass:
      insert "ADR-006 sign-off completed on 2026-05-21" at the top of ADR-005,
      unblocking ADR-005 PR-2 while preserving the D10 gate before PR-4
```

Each PR is independently revertable. PR-A / PR-C / PR-D are the core; PR-E went ahead; PR-F is now complete via operator-approved bypass. `BakeCheck.yml` continues as regression monitoring, not as an active blocker.

---

## Consequences

### Positive

1. **Pending Mode becomes the genuine main path** — docs and reality align
2. **Failure modes are forced into the open** — Pending alerts no longer have a silent retreat path
3. **Unblocks ADR-005** — the D10 gate can pass after the bake
4. **Operator mental model simplifies** — only one mode remains; operators no longer weigh audit vs pending

### Negative

1. **The 30-day bake was bypassed for ADR-005 PR-2** — the 2026-05-21 operator sign-off trades the original wait for continued BakeCheck regression monitoring
2. **Pending Mode defects surface more directly as ops incidents** — issues previously hidden by the fallback now become pipeline pauses
3. **Manual pause/resume flow requires operator training** — the runbook needs an update

### Risks

1. **New Pending Mode defects appear during the bake** → pipeline pauses repeatedly, triggering complaints
   - **Mitigation**: tighten monitoring in the first week of the bake; loosen alert thresholds, preferring over-alerting to missing
2. **An existing `pending_mode_disabled_until` field in `.publish-config.yml` is read by an external script** → deletion causes an outage
   - **Mitigation**: grep confirms only DailyIngestion itself and `pending_mode_auto_fallback.py` reference it; no external dependencies
3. **After cancelling PR-B, the schema DEFAULT and runtime default disagree** (schema is still `'audit'`, runtime default is `'pending'`)
   - **Impact**: the DEFAULT only fires on the two historical migration paths; semantically "unknown historical session" is still labelled audit, which is correct
   - **Mitigation**: amendment 1 records the decision rationale; if future code adds INSERTs that omit WriteMode, they must explicitly supply a value

---

## Related ADRs

- **Successor**: [ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) — PR-2 may proceed after the 2026-05-21 sign-off bypass; PR-4 still requires the D10 trio to pass
- **Corrects past commitment**: [ADR-001](../ADR-001-Split-Db-Module/ADR-001-split-db-module.md) Phase 3's "Pending Mode default" commitment is genuinely delivered by this ADR

---

## References

- [CONTEXT.md](../../../../CONTEXT.md) — Write Mode section
- SQL used in the D10 check:
  ```sql
  SELECT WriteMode, COUNT(*) FROM ReportSessions
  WHERE DateTimeCreated > datetime('now','-30 days') GROUP BY WriteMode;
  ```
- Retired auto-fallback (replaced by D3 alert+pause): [`.github/workflows/DailyIngestion.yml`](../../../../.github/workflows/DailyIngestion.yml), `apps/cli/db/pending_alert.py` (path updated per amendment 2)
- Default implementation: [`javdb/storage/db/db_session.py`](../../../javdb/storage/db/db_session.py) (path updated per amendment 2)
