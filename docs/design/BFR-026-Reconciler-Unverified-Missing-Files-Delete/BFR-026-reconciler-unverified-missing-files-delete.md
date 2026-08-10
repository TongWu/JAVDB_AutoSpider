# BFR-026: Reconciler deletes `missingFiles` torrents with files, unverified

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: High
**Affected**: `javdb/ops/reconcile/service.py`, `javdb/ops/reconcile/models.py`, `apps/cli/ops/reconcile.py`, `.github/workflows/ReconcileLibrary.yml`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), `javdb/integrations/qb/purge_missing_files.py`, PRs #210 and #251

---

## Symptom

No incident was observed. The defect was found by review while promoting `dev` → `main` on the public mirror (TongWu/JAVDB_AutoSpider#153) and confirmed against the code.

The acquisition reconcile pass deleted every `missingFiles` torrent it had a tracked `AcquisitionOutcome` row for, **with `delete_files=True`**, based only on a state snapshot taken earlier in the same run:

```python
for qb_hash in missing_files_hashes:
    if qb_hash not in active:
        continue
    _client.delete_torrents([qb_hash], delete_files=True)
```

`ReconcileLibrary.yml` runs this hourly.

## Root Cause

`missingFiles` is **ambiguous**. qB reports it both when the content was genuinely removed from disk and when the disk holding it is temporarily unavailable — a flaky mount, an unmounted NAS, a storage box mid-reboot. qB offers no way to tell the two apart from state alone.

Two consequences follow, and both are bad:

- If the mount returns between the snapshot and the delete call, `delete_files=True` removes content that is **present and wanted**.
- If it stays unavailable, the tracked torrent entry is discarded anyway, even though the files may reappear.

The deeper flaw is ownership. #210 gave the reconciler this deletion. #251 then added `javdb/integrations/qb/purge_missing_files.py`, which solves exactly this ambiguity properly — it stops each torrent, forces a recheck so qB re-verifies against disk, reads per-file presence, **keeps** anything it cannot verify, and only considers torrents that completed at least `min_age_hours` (default 22) ago. Its own module docstring names the ambiguity as the reason it exists.

What #251 did not do was retire the older, unguarded path. The codebase was left with **two owners of the same destructive action**, one careful and one not, and the careless one ran 24× more often.

## Evidence

| Path | Verification before `delete_files=True` | Schedule |
| --- | --- | --- |
| `javdb/ops/reconcile/service.py` | none — state snapshot only | hourly |
| `javdb/integrations/qb/purge_missing_files.py` | stop → recheck → per-file presence → keep-if-unverified → ≥22h age guard | daily |

The purge path is also strictly broader in coverage: it scans **every** `missingFiles` torrent, while the reconciler only handled those with an `AcquisitionOutcome` row. Nothing was reachable only through the reconciler.

## Fix

Delete the destructive block from the reconcile pass outright, rather than porting the verification into it. Reasons:

1. The safe implementation already exists, already runs daily, and already covers a superset of the torrents.
2. Porting the logic would leave two copies of a subtle, safety-critical routine that must then be kept in step.
3. The reconciler's actual job — advancing `AcquisitionOutcome` state — is untouched.

**The `completed` transition is preserved.** It never depended on the deletion path: `missingFiles` is a member of `_QB_COMPLETED_STATES` in `javdb/ops/reconcile/collectors.py`, so the promotion happens through the ordinary observation flow.

Removed as orphans of this change: the `missing_files_hashes` set that fed only the deleted block, the function-scope `_client` hoist that existed only to survive until it, and the `ReconcileResult.missing_files_deleted` counter plus its CLI summary line (it could only ever read 0 after this, which would have been actively misleading).

Deferred deletion is the only behavioural cost: a genuinely-gone torrent now lingers in qB for up to ~24h instead of ~1h. That is strictly safer, and the purge path deliberately waits 22h anyway.

## Side Effects

- `ReconcileResult.missing_files_deleted` is gone from the result model and the `--json` / text summaries. Nothing else read it.
- Operators watching the hourly reconcile summary will no longer see a "Missing files deleted from qB" line. The daily `PurgeMissingFiles` run reports its own counts.
- No D1 schema change; no migration.

## Follow-Up

- [x] Remove the unverified deletion from the acquisition pass
- [x] Pin the new contract with tests (`test_run_treats_missing_files_as_completed_without_deleting_from_qb`, `test_run_never_deletes_untracked_missing_files_hashes`)
- [x] Correct `ReconcileLibrary.yml`'s header comment and the `github-actions-setup.md` handbook entry, both of which documented the deletion
- [ ] Consider a contract test asserting that `delete_torrents(..., delete_files=True)` is reachable from `purge_missing_files` only, so a third caller cannot reintroduce this class of bug
