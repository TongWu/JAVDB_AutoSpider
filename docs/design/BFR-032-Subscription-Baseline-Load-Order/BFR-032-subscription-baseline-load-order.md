# BFR-032: Subscription monitor drops a release shared by two subscribed actors

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `javdb/pipeline/subscription_monitor.py`
**Related**: [ADR-054](../_archive/ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md), [IMP-ADR054-02](../_archive/ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md), issue #274, PR #232 (`NewWorks` composite PK)

---

## Symptom

No incident was observed. Found by review while promoting `dev` → `main` on the public mirror (TongWu/JAVDB_AutoSpider#153), filed as issue #274 and confirmed against the code — the regression test reproduces it exactly, logging `Actor /actors/B: 0 new work(s) added to feed (scraped 1)`.

When two subscribed actors co-star in a newly released work, only the first actor gets a `NewWorks` feed row. The second actor never sees the release.

## Root Cause

Each actor's baseline was loaded at the top of its own loop iteration:

```python
for raw_href in actor_hrefs:
    actor_href = normalize_javdb_href_path(raw_href) or raw_href
    seen_before = load_seen_video_codes(actor_href, db_path=db_path)        # <-- here
    session_id = scrape_actor(actor_href, use_proxy=use_proxy)
    commit_spider_session(session_id)                                       # <-- commits
    scraped = load_actor_works_from_history(actor_href, db_path=db_path)
    total_added += process_actor(..., seen_video_codes=seen_before, ...)
```

`commit_spider_session` promotes the scrape's pending rows into `MovieHistory`, and `load_seen_video_codes` reads that same table — matching on `ActorLink` **or** any entry in `SupportingActors`. So iteration *n* mutates the very state iteration *n+1* samples.

For a work starring A with B supporting: A's scrape commits it with B already in `SupportingActors`. B's baseline is then loaded and contains the work. `process_actor` sees `work.video_code in seen_video_codes` and `continue`s, so B's `(actor_href, video_code)` row is never written.

The deeper flaw is a mismatch between *when* the baseline is sampled and *what* it is supposed to mean. "Already seen" is a property of the world **before this run**, but the code sampled it per-actor mid-run, making it "before this actor's scrape" — which is only equivalent when actors' catalogues are disjoint. Co-starring makes them overlap, and overlap is exactly the case #232 widened the `NewWorks` primary key to `(actor_href, video_code)` to support. The schema could represent the row; the monitor never wrote it.

Ordering also made it silently asymmetric: whichever actor happened to be scraped first won the release, so which actor lost their feed row depended on `list_active_hrefs()` ordering rather than on anything meaningful.

## Fix

Snapshot every actor's baseline before the scrape loop begins, so no actor's diff can be contaminated by a sibling's commit:

```python
normalized_hrefs = [normalize_javdb_href_path(h) or h for h in actor_hrefs]
baselines = {
    actor_href: load_seen_video_codes(actor_href, db_path=db_path)
    for actor_href in normalized_hrefs
}
```

Href normalization is hoisted alongside it so the snapshot is keyed identically to the loop's lookups. The query count is unchanged — one `load_seen_video_codes` per actor, just all up front.

The alternative the issue offered — deriving new rows from each scrape session instead of from post-commit history — was not taken: it would restructure the diff to read session-scoped state rather than `MovieHistory`, a much larger change for the same outcome.

## Side Effects

Memory now holds every subscribed actor's video-code set simultaneously rather than one at a time. For realistic subscription counts this is negligible (a set of short strings per actor).

**The fix is forward-looking only.** From the first fully successful run after it, a shared release reaches every subscribed actor's feed. It does *not* recover rows missed before the fix, and cannot: the baseline lives only in the run that computes it, so a work already committed to `MovieHistory` in an earlier run is in every actor's baseline from then on. Two cases stay lost, both by design rather than oversight:

- A release missed by the old code before this fix.
- A run interrupted after actor A committed but before actor B was processed — B's next run sees the work as already seen. The same applies if an earlier version already advanced B's `last_seen_href` past it.

Recovering those would need a persisted, per-actor baseline advanced only on a fully successful run, which is a larger change than this defect warrants; it is recorded below rather than smuggled in here. `NewWorksRepo.add` is idempotent, so re-runs never duplicate.

## Follow-Up

- [x] Same-run correctness, pinned by `test_shared_release_lands_in_both_actors_feeds` in `tests/unit/test_subscription_monitor.py`.
- [ ] Cross-run recovery is **out of scope here**: a persisted per-actor baseline, advanced only after a fully successful run, would also cover an interrupted run and a cursor already advanced by an older version. Worth doing only if missed co-starring releases are observed in practice — the in-memory snapshot closes the reported defect.
