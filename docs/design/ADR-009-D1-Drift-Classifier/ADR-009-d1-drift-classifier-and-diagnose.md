# ADR-009: D1 Transient-Error Classifier Fix + Drift Diagnostic Tool

**Status**: Accepted — all phases implemented (2026-05-23)
**Date**: 2026-05-17
**Last verified**: 2026-05-24
**Paired IMPs**:
- [IMP-ADR009-01](IMP-ADR009-01-d1-transient-classifier-fix.md) — P0 / D1 classifier fix.
- [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md) — P1 / read-only diagnose CLI.
- [IMP-ADR009-03](IMP-ADR009-03-drift-diagnose-guarded-apply.md) — P2 / guarded `--apply` path.
- [IMP-ADR009-04](IMP-ADR009-04-drift-diagnose-email-integration.md) — P3 / email diagnosis integration.

## Implementation Status

- **P0 (D1 — Layer 0)** — `"connection lost"` added to `_TRANSIENT_ERROR_KEYWORDS` in [`javdb/storage/d1_client.py`](../../../javdb/storage/d1_client.py) + regression test `test_400_network_connection_lost_treated_as_transient`. ✅ Done.
- **P1 (D2, D3, D4 — Layer 1 diagnose)** — `drift_diagnose` CLI at [`apps/cli/db/drift_diagnose.py`](../../../apps/cli/db/drift_diagnose.py), delegating D1/SQLite diagnosis logic to [`javdb/storage/drift_diagnose.py`](../../../javdb/storage/drift_diagnose.py). Read-only diagnose mode with suspect discovery (verify-metric + D1-sweep), verdict classification (CLEAN/SAFE_TO_APPLY/ESCALATE/UNEXPECTED), JSON output, exit codes. Covered by `tests/unit/test_drift_diagnose.py` (64 unit tests across diagnose + apply). ✅ Done.
- **P2 (D5 — Layer 1 apply)** — `--apply --session-id` path with five hard safety rails, D1 DELETE execution, audit record to `d1_drift.jsonl`. Covered by `tests/unit/test_drift_diagnose.py`. ✅ Done.
- **P3 (D6 — email integration)** — subprocess invocation of `drift_diagnose --since 1 --json` in the email notification job, `[DRIFT-FIX-READY]`/`[DRIFT-ESCALATE]` subject prefix tagging, 60s timeout with fallback. Covered by `tests/unit/test_email_drift_integration.py` (20 unit tests). ✅ Done.

**Deciders**: Bake-period drift response (succeeds the manual forensic fix recorded as `kind: drift_resolution` in `reports/D1/d1_drift.jsonl` at 2026-05-17T14:00 UTC)
**Prerequisites**: None — bake-safe per [ADR-006](../_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md) amendment 3. The "bake-safe" claim is precise: **no effect on the D10 gate inputs** (no writes to D1/SQLite, no schema change, no `WriteMode` resolution change, no `.publish-config.yml` pause-mechanism change, no `pending_session_verify` emission). Layer 1 D6 *does* modify the `email-notification` job, but the modification is a read-only subprocess call with a 60-second timeout to a tool that itself only touches D10-monitored state via the operator-gated `--apply` path (which is **never** invoked from the workflow).

---

## Context

On 2026-05-17 at 12:28:04 UTC, during the ADR-006 bake period, `dual_connection` emitted a drift advisory:

```
db: history
committed: true
failure_count: 1
first_failed_sql: UPDATE PendingTorrentHistoryWrites SET ApplyState='applied' WHERE Seq IN (?,?)
first_error: D1PermanentError: D1 API returned HTTP 400:
             [{'code': 7500, 'message': 'Network connection lost.'}]
```

Manual forensic confirmed:
- Session `20260517T121617.445400Z-ea87-0000` (run `25990538491`, attempt 1) reached `Status='committed'`.
- Local SQLite cleaned its `Pending*` rows for that session (171 applied → deleted; 2 also applied locally but failed on D1).
- D1 retained 2 orphan rows in `PendingTorrentHistoryWrites` with `ApplyState='pending'` for movie `/v/k8n3e`.
- Live `MovieHistory` and `TorrentHistory` tables were **byte-for-byte identical** on both sides (3 rows for `/v/k8n3e` on each side).
- Resolution required ~10 minutes of cross-DB SQL forensic + one `DELETE` against D1. The fix is recorded as a `kind: drift_resolution` row in `d1_drift.jsonl`.

### Root-cause finding

The drift was **not** a Cloudflare D1 outage — it was a one-line classifier bug in [`javdb/storage/d1_client.py:105-116`](../../../javdb/storage/d1_client.py):

```python
_TRANSIENT_ERROR_KEYWORDS = (
    "D1_RESET_DO",
    "busy",
    "locked",
    "timeout",
    "overloaded",
    "internal error",
    "temporarily",
    "long-running export",
)
```

Cloudflare returned **`"Network connection lost."`** wrapped in HTTP 400 + error code 7500. The classifier inspects the *message text* (correctly, per the in-file rationale) but the substring `"connection lost"` is missing from the keyword list. Result:

1. The HTTP 400 + code 7500 envelope → classifier looks at message text → no keyword matches → raises `D1PermanentError`.
2. `_post_with_retry` re-raises `D1PermanentError` immediately (line 333-335) without retry.
3. `dual_connection` catches the failure → SQLite already committed → drift advisory appended to jsonl.

Cloudflare's `Network connection lost.` message is **categorically transient** — the next request on a fresh TCP connection succeeds (the conventional retry-with-backoff resolution). The classifier never gave it the chance.

### Bake monitoring response

The drift was caught by the existing `pending_session_verify` metric (`pending_residual_count: 2`), which counts as one `pause_trigger` for the bake-period D10 gate. The bake budget allows ≤ 1 trigger/month; this incident consumed today's slot. **A second similar event within the bake window would FAIL the bake gate**, blocking ADR-005 PR-2+.

The monitoring observed the drift but offered no diagnosis path. Closing that 10-minute forensic gap, while also fixing the actual classifier bug, is the goal of this ADR.

---

## Decision

A two-layer response, each layer independently bake-safe:

### D1 — Layer 0: Classifier fix (one-line correctness)

Add `"connection lost"` to `_TRANSIENT_ERROR_KEYWORDS` in [`javdb/storage/d1_client.py`](../../../javdb/storage/d1_client.py). Add a regression unit test asserting that an HTTP 400 + `"Network connection lost"` body classifies as `D1TransientError` (not `D1PermanentError`), so `_post_with_retry` exercises its existing exponential-backoff loop.

This is the **root-cause fix**. Once landed, today's exact class of drift becomes a logged retry (and almost certainly a successful retry — CF network blips heal within seconds) rather than a drift incident.

### D2 — Layer 1: `drift_diagnose` CLI

New CLI at `apps/cli/db/drift_diagnose.py` with two modes. The CLI boundary handles argument parsing, output formatting, and exit-code orchestration; the D1/SQLite discovery, classification, deletion, and audit writes live in `javdb/storage/drift_diagnose.py`.

**Diagnose (default)** — read-only. Scans recent drift indicators, classifies each suspect session, and prints a report. Suggested fix commands are emitted for any session classified as safely-recoverable.

**Apply** (`--apply --session-id <id>`) — re-runs diagnosis on the named session, refuses to act unless verdict is `SAFE_TO_APPLY`, executes the minimum SQL to delete D1 orphan `Pending*` rows, appends a `kind: drift_resolution` audit record to `d1_drift.jsonl`.

### D3 — Suspect discovery (union of two signals)

- **Verify-metric path (q)**: read `reports/D1/d1_drift.jsonl`, extract `pending_session_verify` records with `pending_residual_count > 0` within the configured time window.
- **D1-sweep path (r)**: query D1 for `ReportSessions` with `Status='committed'` in the window; for each, query D1 `Pending{Movie,Torrent}HistoryWrites` rows with `ApplyState='pending'`; sessions with any non-zero count are suspect.

Union both signals; tag each suspect with provenance (`verify-tagged` / `sweep-only` / `both`). The asymmetric subset matters operationally — `sweep-only` (residual on D1 but no verify-line trail) indicates the verify emission path itself is broken and warrants escalation.

### D4 — Verdict classification

| Verdict | Trigger | Suggested action | Exit code contribution |
|---|---|---|---|
| `CLEAN` | No actual orphan despite suspect tag | None (verify metric stale) | 0 |
| `SAFE_TO_APPLY` | D1 orphan + SQLite clean + live tables byte-identical on both sides | Emit `--apply --session-id <id>` command | 1 |
| `ESCALATE_LIVE_DIVERGENCE` | Live `MovieHistory` / `TorrentHistory` differs between sides | Manual investigation; do NOT auto-fix | 2 |
| `UNEXPECTED_PATTERN` | Anything else (in-progress / missing / unverifiable `ReportSessions` row, D1 pending-table read failure, SQLite-side orphan, mixed) | Manual investigation | 2 |

Process exit code = `max(verdicts)` across all suspects. Allows shell-level branching on severity.

### D5 — Five hard safety rails for `--apply`

1. `--apply` MUST accept `--session-id` (argparse-enforced).
2. `--apply` re-runs full diagnosis at apply time; refuses unless verdict is still `SAFE_TO_APPLY` (catches state changes between report generation and execution).
3. Target session MUST currently be `Status='committed'`. In-progress / finalizing / failed are refused.
4. Orphan row count MUST be ≤ `--max-deletes` (default 100). Protects against bulk-DELETE abuse if D1 token leaks.
5. The DELETE statement MUST include both `AND SessionId=?` and `AND ApplyState='pending'` predicates as a code-level invariant (not caller-supplied).

Each safety-rail violation maps to a distinct exit code and a clearly-named log line so operators can tell which guard fired.

### D6 — Email integration (subprocess, time-bounded)

The DailyIngestion `email-notification` job, when it already renders the `⚠️ D1 DRIFT ADVISORY ⚠️` block, additionally invokes `drift_diagnose --since 1 --json` as a `subprocess.run` with a 60-second timeout.

- Successful diagnose output → rendered as a `─── Drift Diagnosis ───` section beneath the existing advisory, with per-session verdict + suggested apply command.
- Subprocess timeout / non-JSON output / non-zero exit (other than 0/1/2) → fallback section "Automated diagnosis unavailable: <reason>", with manual-run pointer. Subprocess failure NEVER blocks email delivery.
- Subject prefix tagging:
  - `[DRIFT-FIX-READY]` is appended when ≥1 session is classified `SAFE_TO_APPLY` — operator scanning inbox knows a one-line fix is ready inside.
  - `[DRIFT-ESCALATE]` is appended when ≥1 session is classified `ESCALATE_LIVE_DIVERGENCE` or `UNEXPECTED_PATTERN`.

The drift_diagnose tool is **never** invoked with `--apply` from the email job. Manual operator action remains the gate for all production-side `DELETE`s (the brainstorming "(b) semi-auto" decision).

### D7 — Implementation phases

| Phase | Scope | Independent of? |
|---|---|---|
| **P0** | D1 classifier fix (D1 above) — one keyword line + one unit test | Yes — single micro-PR, fully independent of P1-P3 |
| **P1** | `drift_diagnose` diagnose mode (D2, D3, D4) — read-only CLI | Yes — usable alone (replaces today's manual forensic) |
| **P2** | `--apply` path (D5) | Builds on P1 |
| **P3** | Email integration (D6) | Builds on P1 (P2 optional but expected) |

Each phase ships as its own PR. P0 can land immediately. P1-P3 sequence by review velocity.

### D8 — Explicit out-of-scope

The following alternatives were considered and **defer to post-ADR-006-bake / ADR-005 PR-2+**:

- **L2a — Idempotent bookkeeping** (collapse `UPDATE ApplyState='applied'` + `DELETE WHERE applied` into a single `DELETE ... WHERE ApplyState='pending'`). Touches `db_commit_session_history`'s write path; violates the ADR-006 D5 → amendment 3 PR-2 carve-out logic during bake.
- **L2b — D1 atomic batch** (wrap all commit-time D1 writes in a single `batch_execute` so partial failure is impossible). Same bake-gate constraint.
- **L3 — Periodic orphan sweep workflow** (full automation, no human review). Rejected during brainstorming as it removes the operator-audit step that the "(b) semi-auto" decision deliberately retained.
- **L4 — Move `Pending*` tables to SQLite-only** (skip D1 mirror entirely for ephemeral bookkeeping). Touches `dual_connection` write strategy; should be re-evaluated alongside ADR-005 Repo class migration.

These are **not** abandoned — they are deferred until the bake-period constraints lift. ADR-005 PR-2 design should explicitly consider L2a as a candidate inclusion.

---

## Alternatives Considered

### Alternative A: Layer 0 only (classifier fix; no diagnostic tool)

Ship the one-line keyword fix and rely on the existing manual forensic for any future drift class not covered by transient-error retry.

**Rejected because**: The keyword set is necessarily incomplete (Cloudflare's error vocabulary is open-ended). Future drift events from unforeseen transient classes will still need the 10-minute manual forensic. The classifier fix treats the *known* failure mode but leaves operators unprepared for the *next* one.

### Alternative B: Full automation (Layer 0 + Layer 1 + Layer 3 periodic sweep, no operator review)

After Layer 0 + Layer 1 are in place, add a cron that automatically applies all `SAFE_TO_APPLY` verdicts.

**Rejected because**: removes the operator-audit step. Brainstorming explicitly evaluated this as option (a) and chose the semi-automated (b) instead. The cited reason: removing operator review eliminates the only mechanism that builds intuition about Cloudflare's actual reliability profile — operators stop noticing the frequency of D1 hiccups and lose the signal that would prompt deeper infrastructure changes.

### Alternative C: Layer 0 + Layer 1 + Layer 2a (idempotent bookkeeping)

Include the `db_commit_session_history` rewrite (`UPDATE+DELETE` → single `DELETE`) inside this ADR.

**Rejected because**: Layer 2a touches the actual write path. Under ADR-006 amendment 3, that places it in the PR-2 category — blocked during bake. Folding it into this ADR would either delay the entire ADR until bake completion or violate the bake gate. The cleaner path is to defer L2a to ADR-005 PR-2's design and ship monitoring + classifier independently now.

### Alternative D: Do nothing — accept manual forensic

Stay with the existing process: drift advisory + manual investigation per incident.

**Rejected because**: today's incident already consumed 1 of the bake's ≤ 1/month `pause_trigger` budget on a single class of error that is provably preventable with a one-line code change. Continuing to allow that class of error costs both operator time and bake-gate margin.

---

## Consequences

### Positive

- **Today's exact drift class becomes impossible** (Layer 0) — a future "Network connection lost." HTTP 400 will retry, succeed, and never reach the drift logger.
- **All future drift events get a 30-second diagnose path** (Layer 1) instead of 10-minute manual SQL forensic.
- **Operators retain the audit gate** (no auto-apply) — Cloudflare reliability signal stays visible.
- **Bake monitoring stays meaningful** — the BakeCheck workflow and `pause_trigger` budget continue to surface real divergences without being numbed by automated fixes.
- **Each layer is independently shippable** — Layer 0 can land in hours; Layer 1 phases at review velocity.

### Negative

- **Layer 0** widens the transient classifier surface: a future *truly permanent* error whose message text happens to include `"connection lost"` would be retried 5 times × backoff (~30s total) before failing. Cloudflare's error vocabulary uses that phrase only for the transient case in current usage, but the classifier remains a heuristic.
- **Layer 1** adds ~800 lines of new code + tests + a new CLI + an email-integration code path that has to stay aligned with the underlying schema. Maintenance burden increases.
- **Email subject grows tags** (`[DRIFT-FIX-READY]` / `[DRIFT-ESCALATE]`) — operator filter rules may need updating.

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Layer 0 false-positive — a true permanent error text contains `"connection lost"` | Low | Cloudflare's current usage of that phrase is exclusively transient; if a permanent variant appears, classifier can be made stricter (e.g. require also `"Network "`). Bounded cost on false-positive: 5 retries × ~30s = one delayed failure, not data corruption. |
| Layer 1 `_compare_live_tables` heuristic misses subtle divergence | Medium | Tests cover field-level flip detection. Any uncertainty path routes to `ESCALATE` rather than `SAFE_TO_APPLY`. Operator final-audits all `--apply` invocations. |
| `--apply` race between operator and email-job-generated suggestion | Low | The re-diagnosis at apply time catches state changes (D5 rail 2); DELETE is naturally idempotent. |
| Operator habituation — auto-running the suggested command without reading rationale | Medium | Report format always shows the WHY (D1 orphan count, live identical check, suspect provenance). Verdict labels are explicitly named to require comprehension (no traffic-light icons). |
| Email subprocess hangs longer than 60s | Low | Hard timeout; fallback section renders; subject tags omitted; email delivery completes on time. |

---

## Related Decisions

- **[ADR-006](../_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md) amendment 3** — bake-safe carve-out logic. Layer 0 has no workflow effect at all. Layer 1 D6 modifies the `email-notification` job but only via a read-only subprocess with strict timeout; it does not change any D10 gate input (no writes to D1/SQLite, no schema change, no Pause-mechanism change). Both fall on the bake-safe side under the corrected framing above.
- **[ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) PR-2 (deferred)** — should explicitly consider L2a (idempotent bookkeeping) as a candidate inclusion when its design is revisited post-bake.
- **PR #50** (BakeCheck.yml) — orthogonal monitoring layer; drift_diagnose is a *diagnosis* layer that complements but does not replace the gate.

---

## References

- The 2026-05-17 drift incident, recorded as `kind: drift_resolution` in `reports/D1/d1_drift.jsonl` (committed via the post-incident sync).
- [`javdb/storage/d1_client.py`](../../../javdb/storage/d1_client.py) — current classifier (Layer 0 target).
- [`javdb/storage/dual_connection.py`](../../../javdb/storage/dual_connection.py) — drift detection + advisory writer (Layer 1 reads from its output).
- [`javdb/integrations/notify/email.py`](../../../javdb/integrations/notify/email.py) — email rendering pipeline (D6 integration target) — *post-ADR-007 path; the pre-restructure module at `packages/python/javdb_integrations/email_notification.py` no longer exists*.

---

## Open follow-ups

- The drift advisory record format itself **does not log SQL `params`** — that gap is what forced today's forensic to reverse-engineer the affected `Seq` values from the verify metric. A separate small PR should extend the advisory record schema to include the failed SQL's params; the drift_diagnose tool will then have a direct signal instead of the time-window correlation it currently relies on. This is **not** required for this ADR's acceptance but is the natural complement.
