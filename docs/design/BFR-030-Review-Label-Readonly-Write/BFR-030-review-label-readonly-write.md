# BFR-030: Review-label writes accepted readonly JWTs despite being documented admin-only

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: High
**Affected**: `apps/api/routers/quality.py`, `server/routes/quality.ts` (`TongWu/JAVDB_AutoSpider_Web`)
**Related**: [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [IMP-ADR024-08](../ADR-024-Torrent-Quality-Evidence/IMP-ADR024-08-phase2-assist.md), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), issue #270

---

## Symptom

No incident was observed, and no exploitation is known. Found by review while promoting `dev` → `main` on the public mirror (TongWu/JAVDB_AutoSpider#153), filed as issue #270 and confirmed against the code.

`POST /api/quality/review-labels` accepted a **readonly** JWT. Any authenticated readonly user could write, overwrite, or flip the operator accept/reject/skip label on any evaluation.

## Root Cause

The route declared the wrong dependency:

```python
def write_review_label(
    body: ReviewLabelRequest,
    _user=Depends(_require_auth),      # authenticates; does NOT check role
) -> ReviewLabelResponse:
```

`_require_auth` decodes the JWT, rejects non-access tokens, and rate-limits. It never inspects `payload["role"]`. Role enforcement lives in a *separate* dependency, `require_role("admin")`, which wraps `_require_auth` and adds the check.

The API reference had said admin-only since the endpoint shipped:

> `POST /api/quality/review-labels` — admin-only. …

So this was not an undecided policy — it was documented intent that the wiring never implemented. Nothing detects that class of drift: the docs are prose, and the route's own tests all authenticated as admin, so a readonly caller was never exercised.

Two properties make it worth more than a one-line correction:

1. **The guarded resource is shared, not per-user.** Review labels are the training/tuning dataset ADR-024 Phase 3 sets quality thresholds from. One readonly user's edits change what the system later decides for everyone. Most readonly-vs-admin gaps expose *someone's* data; this one lets a low-privilege caller steer future automated behaviour.
2. **It was duplicated across backends.** ADR-017 has two implementations of this endpoint. The TypeScript Worker gated it on the global `requireAuth()` middleware only, with a comment asserting auth was handled — the same mistaken assumption, reached independently. A role decision expressed only in prose gets re-implemented wrongly by each backend.

## Fix

Python — swap the dependency; `require_role` depends on `_require_auth` internally, so the payload the reviewer identity reads is unchanged and the OpenAPI `BearerAuth` requirement still propagates:

```python
    _user=Depends(require_role("admin")),
```

TypeScript — add the middleware the repo already uses for every other admin mutation:

```ts
qualityRoutes.post("/review-labels", requireRole("admin"), async (c) => {
```

Both sides gained a readonly-caller test asserting `403` **and** that no row was written; each fails against its pre-fix route. `docs/api/openapi.json` was regenerated.

## Side Effects

A readonly user that previously could write labels now gets `403`. That is the intended contract, and the API reference already documented it, so no doc change was needed — the code moved to meet the docs.

No data migration: existing labels keep whatever `reviewer` value they were stored with. If a readonly account was ever used to label in earnest, those rows are indistinguishable from admin-written ones; the `reviewer` column records the JWT subject, so they can be audited by username if that matters.

## Follow-Up

- [x] Python: `require_role("admin")` on the route + regression test.
- [x] TypeScript: `requireRole("admin")` + regression test (branch `claude/verify-fix-open-issues-n8srgf` in `TongWu/JAVDB_AutoSpider_Web`).
- [ ] Consider a contract test that pins documented role requirements against the actual dependency wiring, so prose-only role decisions cannot drift again. Not scoped here.
