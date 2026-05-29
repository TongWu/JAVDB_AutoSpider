# BFR-008: Post-ADR-015 integration latent issues

**Status**: Fixed
**Date**: 2026-05-29
**Severity**: Medium
**Affected**: `javdb/integrations/rclone/manager/service.py`, `javdb/integrations/notify/email/_config.py`, `javdb/integrations/notify/email/log_analysis.py`, `javdb/integrations/notify/email/service.py`, `javdb/integrations/qb/uploader/service.py`, `javdb/integrations/qb/file_filter/service.py`, `javdb/integrations/pikpak/bridge/service.py`, `apps/cli/{notify/email,qb/uploader,qb/file_filter,pikpak/bridge,rclone/manager}.py`
**Related**: [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [PR #114](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/114), [Issue #115](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/115)

---

## Symptom

CodeRabbit's review of the ADR-015 PR (#114) surfaced six findings on the diff. Three are genuine latent defects, three are robustness/hygiene gaps. They were filed as #115 and deferred out of #114 to keep that refactor strictly behavior-preserving; this BFR fixes them.

1. **rclone marks inherited sessions as failed.** In `run_manager_from_options`, the scan-failure paths called `mark_session_failed(_staging_session_id)` guarded only by `_staging_session_id is not None`. When rclone runs inside a pipeline that already has an active workflow session, `_staging_session_id` is *inherited*, so a partial scan flips the shared pipeline session to `failed`.
2. **Import-time `os.chdir` / `sys.path` mutation.** Five integration service modules ran `os.chdir(REPO_ROOT)` + `sys.path.insert(...)` at module import. Merely importing them (e.g. the REST API importing `send_email` / `run_rclone_manager` / `pikpak_bridge`, or any test/concurrent task) mutated process-global cwd and `sys.path`.
3. **D1 drift JSONL parsing aborts on bad data.** `log_analysis` assumed `rec['ts']` is a string and that the counter fields are `int`-coercible; one malformed JSONL record raised and aborted the whole email notification.
4. **`run_bridge` returned a placeholder result.** The PikPak CLI service returned a near-empty `PikPakBridgeResult` (only `dry_run`), so programmatic callers saw all-zero stats despite a real transfer.
5. **`requests.Session` leak in qB services.** `run_uploader` and `run_file_filter_cli` returned from multiple branches without closing their `requests.Session`.
6. **Notify session-start read bypassed the repo layer.** `service.py` read `ReportSessions.DateTimeCreated` via raw SQL instead of the storage repo.

## Root Cause

ADR-015 was a **strictly behavior-preserving** refactor: it moved code verbatim out of monolithic modules into command/service packages. That faithfully preserved several *pre-existing* latent issues (1, 2, 3, 5, 6) — they predate ADR-015 but became newly visible in the large diff. Finding 4 is new: the `run_bridge` wrapper introduced in ADR-015 Phase 3 deliberately returned a placeholder because the legacy `pikpak_bridge` returns `None` and the old `main()` only used the process exit code.

The core design flaw behind (1): the failure path was asymmetric with the success path — `mark_session_committed` was already gated by "did we create this session locally" (`_created_local_staging_session`), but `mark_session_failed` was not, so an inherited session could be mutated. Behind (2): module import is the wrong place for process-global side effects; the cwd contract belongs at the CLI entrypoint, not at import of a reusable service.

## Fix

Implemented on branch `claude/bfr-008-post-adr015-fixes`:

1. **rclone** — gate all three failure-path `mark_session_failed` calls with `if _created_local_staging_session:` (symmetric with the success path). `drop_rclone_staging` stays **unconditional** — the per-session staging table is always ours to clean up, even for inherited sessions. (This intentionally diverges from CodeRabbit's literal suggestion, which would also have skipped the drop and leaked the staging table.)
2. **chdir/sys.path** — removed the import-time `os.chdir(REPO_ROOT)` + `sys.path.insert(...)` from all five service modules (kept `REPO_ROOT`). The chdir was relocated to each CLI adapter, **at module top, before the integration imports**. (Initial attempt placed it in `main()`, but CodeRabbit/Codex review of the PR correctly noted that is too late: importing the adapter's `options` submodule runs the package `__init__`, which imports the service whose module-level `setup_logging()` (file logger / `logs/` creation) and `cfg()` resolution happen at import time and must run with cwd == repo root. The chdir therefore must precede the first `javdb.integrations.*` import in the adapter.) Net effect: importing a *service* no longer changes cwd (the REST API / tests / concurrent tasks are no longer polluted); the CLI entrypoints still establish cwd == repo root before their service loads. Production behavior is unchanged (CLIs run as `python -m apps.cli.<command>` — e.g. `apps.cli.notify.email`, `apps.cli.qb.uploader` — from repo root).
3. **JSONL hardening** — coerce `ts = str(...)` and parse the three counters inside `try/except (TypeError, ValueError): continue`; a malformed record is skipped (and not counted) without aborting the email flow.
4. **`run_bridge` stats** — `_pikpak_bridge_impl` now returns a stats dict at every exit; `run_bridge` maps it into a fully-populated `PikPakBridgeResult` (`exit_code` unchanged). `pikpak_bridge` keeps its pass-through return.
5. **Session lifecycle** — wrapped both qB services' `requests.Session()` in `try/finally: session.close()` covering every return path.
6. **Repo layer** — notify session-start read now goes through `SessionsRepo(_conn).get(_sid).created_at`. The connection comes from `get_local_sqlite_db(REPORTS_DB_PATH)` — the purpose-built helper that always yields a raw `sqlite3.Connection` (never D1) — rather than the backend-routing `get_db()`. This keeps the read sqlite-local (the email module must not paper over D1 lag) and satisfies `SessionsRepo`'s sqlite-only contract even under `STORAGE_BACKEND=dual`. (CodeRabbit suggested `get_db('reports')`, but that auto-routes to a D1-backed connection under dual and would break both invariants.)

New regression tests cover (1) inherited-vs-local session marking, (3) malformed JSONL skip, and (4) populated `run_bridge` result.

## Side Effects

- (1) Behavioral correction: an inherited/upstream pipeline session is no longer flipped to `failed` by a partial rclone scan. Locally-created sessions still get marked failed. Staging-table cleanup is unchanged.
- (2) Importing any integration **service** no longer changes the process cwd / `sys.path`. The CLI **adapters** still chdir to repo root at import (before loading their service) — adapters are entrypoints not imported by the REST API, so this is benign. Production CLI behavior is identical (cwd is already repo root). This removes the cross-contamination hazard for the REST API, tests, and concurrent tasks that import services.
- (4) `_pikpak_bridge_impl` / `pikpak_bridge` now return a dict instead of `None`; the REST caller ignores the return value, so no impact there.
- (3), (5), (6): no happy-path behavior change; (3) only changes the malformed-input path (crash → skip), (5) is resource hygiene, (6) is the same data via the repo abstraction.
- Verification: targeted suites + full `tests/unit` + `tests/architecture` = 3189 passed, 73 skipped; architecture guard green; importing all five services leaves cwd unchanged.

## Follow-Up

- [ ] `javdb/integrations/notify/email/_config.py` still calls `setup_logging(...)` at import time (a remaining, smaller import-time side effect not in scope here). Consider moving global logging setup to the CLI entrypoints too.
- [ ] The same import-time `setup_logging` pattern exists in the other integration service modules; evaluate alongside the notify case.
