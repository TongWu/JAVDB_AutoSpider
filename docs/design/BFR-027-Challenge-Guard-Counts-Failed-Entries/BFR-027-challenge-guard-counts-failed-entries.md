# BFR-027: Challenge guard counts failed entries as discovery

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: High
**Affected**: `javdb/spider/runtime/report.py`
**Related**: [BFR-025](../BFR-025-Site-Wide-Challenge-Bans-Pool-Via-Coordinator/BFR-025-site-wide-challenge-bans-pool-via-coordinator.md), [BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.md), [PR #154](https://github.com/TongWu/JAVDB_AutoSpider/pull/154) (review finding)

---

## Symptom

Latent — found by automated review on the public-mirror promotion PR, not by a
failed run. The guard BFR-025 added to catch a fully-walled run does not fire
when the index phase survives the wall but the detail phase does not.

In that shape the run ends like a quiet day:

- exit code `0`
- a header-only CSV (the BFR-025-era `csv_writer` change guarantees the file exists)
- no proxy marked banned, because a site-wide challenge deliberately bans nothing

Downstream, an empty ingest is indistinguishable from a genuinely empty day: the
pipeline commits its session, the notification reports zero new entries, and
nothing signals that JavDB walled off every proxy.

## Root Cause

BFR-025 removed the site-wide challenge from proxy-ban accounting — 28 proxies
should not be burned for a wall none of them caused. That removal took away the
only signal that used to fail a fully-walled run, so BFR-025 added a replacement
guard in `report.py`:

```python
if site_challenge_seen and total_discovered == 0:
    ...
    sys.exit(2)
```

The flaw is the choice of `total_discovered`, defined at `report.py:79`:

```python
total_discovered = len(rows) + skipped_history_count + no_new_torrents_count + failed_count
```

`failed_count` is part of that sum. It is the right definition for the "found"
line of the summary — every entry the index surfaced, whatever became of it —
but the wrong one for a guard asking *did this run produce anything?*

The two paths through a site-wide challenge diverge on exactly that term:

| Path | `rows` | `failed_count` | `total_discovered` | Guard fires? |
| --- | --- | --- | --- | --- |
| Index itself walled | 0 | 0 | 0 | Yes |
| Index survives, all details walled | 0 | N | N | **No** |

The second path is not exotic. Index pages and detail pages are separate
requests, and a detail-phase challenge is handled at
`fetch_engine.py:1204-1213`: the task records `failed_proxies`, latches
`site_challenge_seen`, and skips soft-ban accounting. Those tasks land in
`failed_count` via `p1_result['failed']` / `p2_result['failed']`
(`run_service.py:764`, `:838`). So the run reaches the guard with
`site_challenge_seen` true, zero rows, and a positive total — and is waved
through.

The underlying design error is treating one aggregate as if it answered two
different questions. "How many entries did we see?" and "did we get anything
usable?" need different numerators, and `failed_count` is the term that
separates them.

## Fix

Subtract the failures before asking the question, and say so at the call site:

```python
legitimate_discovered = total_discovered - failed_count
if site_challenge_seen and legitimate_discovered == 0:
```

`skipped_history_count` and `no_new_torrents_count` stay in the sum
deliberately. Both mean the index fetch really landed and its entries were
matched against history — real work, and the pre-existing contract that a
partially-recovered run stays a success. Only `failed_count` is excluded.

The error message now reports how many discovered entries failed, since
"discovered ZERO entries" is no longer accurate on this path.

Tests in `tests/unit/test_cf_challenge_handling.py`
(`TestZeroEntriesUnderChallengeFailsTheRun`) gained a `failed` parameter and
three cases:

| Case | Expected |
| --- | --- |
| `rows=[]`, `failed=40`, challenge seen | `SystemExit(2)` |
| `rows=[]`, `failed=40`, no challenge | no raise — this guard is challenge-only |
| `rows=[]`, `skipped=30`, `failed=10`, challenge seen | no raise — history skips are real work |

The first fails against the pre-fix guard and passes after it, so it pins the
regression rather than merely describing it.

## Side Effects

A run that is fully walled after a successful index fetch now exits `2` where it
previously exited `0`. That is the intended correction, but it is a live
behavioural change on a path that has been silently passing:

- The `cleanup-on-failure` step will now dispatch for these runs, so the
  session is rolled back rather than committed empty.
- Daily Ingestion will surface a failure on days where JavDB walls the detail
  phase. This is a real signal, not noise, but it changes the run's outcome and
  the notification it produces.

No change to the ban accounting introduced by BFR-025, and no change to the
summary report's `found` figure — `total_discovered` still counts failures for
display.

## Follow-Up

- [x] Exclude `failed_count` from the guard's discovery total
- [x] Cover the index-survives / details-walled path in unit tests
- [ ] Confirm on the next real site-wide challenge that the run fails at the
      report stage and `cleanup-on-failure` rolls the session back as expected
- [ ] Decide whether to attribute failures to the challenge rather than infer
      it. The guard reads a run-wide `site_challenge_seen` latch and an
      unattributed `failed_count`, so a run where a challenge latched early and
      then every entry failed for unrelated reasons — with no history skip and
      no no-new-torrents hit to hold the guard off — exits `2` on the strength
      of a coincidence. The window is narrow and the error is fail-safe, so this
      is deferred rather than fixed here. A precise version cannot key on
      `error='site_challenge_exhausted'` alone: that marker is emitted only on
      the exhausted-streak branch (`fetch_engine.py:1215-1230`), and challenge
      failures that requeue and later die as `all_proxies_failed` carry no
      marker, which would reintroduce this same defect in narrower form. It
      needs task-level challenge attribution threaded through `fetch_engine` →
      `detail/runner` → `run_service` → `report`. Raised by automated review on
      [PR #276](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/276).
