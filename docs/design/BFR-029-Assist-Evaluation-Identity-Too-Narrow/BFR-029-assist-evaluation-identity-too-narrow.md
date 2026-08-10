# BFR-029: Assist rankings lose rank 1 when one info hash holds two candidate roles

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `javdb/quality/assist_evaluator.py`
**Related**: [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [IMP-ADR024-08](../ADR-024-Torrent-Quality-Evidence/IMP-ADR024-08-phase2-assist.md), issue #272, PR #232 (`NewWorks` composite PK)

---

## Symptom

No incident was observed. Found by review while promoting `dev` → `main` on the public mirror (TongWu/JAVDB_AutoSpider#153), filed as issue #272 and confirmed against the code.

In assist mode, when a torrent is both the production download **and** a probe runner-up for the same movie, the stored `TorrentQualityEvaluation` recommendation can lose rank 1, or lose the production row's replacement flag.

## Root Cause

`TorrentQualityRepo.list_evidence_for_movie` is a UNION of two branches — production evidence joined through `TorrentQualityEvaluation`, probe evidence joined through `TorrentProbeCandidate` — each hard-coding its own `target_role`. One `info_hash` that occupies both roles therefore surfaces as two rows, correctly, because they are two distinct `TorrentQualityEvidence` primary keys (`(info_hash, probe_schema_version, target_role)`).

`rank_candidates` handles that duplication deliberately, ranking by original position rather than by hash:

```python
# Rank by ORIGINAL POSITION, not info_hash: two candidates can share an
# info_hash (e.g. the same torrent surfaced as both production and a probe
# runner-up), and a hash-keyed rank map would collapse their ranks.
```

The evaluator then persisted every ranked candidate:

```python
for ranked in all_ranked:
    repo.upsert_evaluation(EvaluationRecord(
        info_hash=ranked["info_hash"],
        movie_href=ranked.get("movie_href", movie_href),
        scoring_version=scoring_version,
        ...
```

`TorrentQualityEvaluation`'s primary key is `(info_hash, movie_href, scoring_version)` — it has **no `target_role`**. The two candidates collapse onto one row and the second UPSERT overwrites the first's `shadow_rank` and `would_replace_current_choice`.

This is the same class of defect as the `NewWorks` composite-PK repair in #232: a row identity narrower than the set of rows being written. The evidence table already learned this lesson — `target_role` is part of *its* key. The evaluation table did not, and nothing in between reconciled the two grains, so a wider read fanned out onto a narrower write.

There is a second, subtler wrong answer hiding here. Even without the overwrite, ranking the same torrent twice lets it outrank *itself*: the probe copy takes rank 1, the production copy takes rank 2, and `would_replace_current_choice` is raised on the production row — advertising a "replacement" that is the identical info hash.

## Fix

Collapse duplicate-role candidates in the evaluator, before scoring, keeping the `production_download` row (it carries the current-choice semantics `would_replace_current_choice` is about):

```python
def _collapse_duplicate_roles(rows): ...
...
for row in _collapse_duplicate_roles(evidence_rows):
```

Chosen over widening the primary key, which the issue offered as the alternative: adding `target_role` to `TorrentQualityEvaluation`'s PK is a D1 schema migration on a table the shadow collector also writes, and it would preserve a row pair that is semantically one torrent. Collapsing fixes both symptoms at once — one row per identity, and no self-replacement flag — with no schema change.

`rank_candidates` is deliberately left as-is. It remains correct for duplicate hashes; the evaluator now simply never hands it any.

## Side Effects

Ranks are denser in the duplicate case. Previously a duplicated torrent consumed two rank slots, so an unrelated third candidate ranked 3; it now ranks 2. This only affects the case that was already producing a corrupted row.

The dropped probe row's features are discarded rather than merged. Both evidence rows describe the same torrent — same info hash, same file list — so the production row's promoted feature columns are equivalent; no signal is lost.

## Follow-Up

None. The behaviour is pinned by `test_duplicate_role_info_hash_writes_one_row_and_keeps_rank_1` in `tests/unit/test_quality_assist_evaluator.py`.
