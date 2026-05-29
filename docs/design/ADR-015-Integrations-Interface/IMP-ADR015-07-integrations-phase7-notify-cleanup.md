# IMP-ADR015-07: ADR-015 Phase 7 - Notify Cleanup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 7 by deleting the notify email bake wrapper, updating remaining imports and tests, and removing notify from the ADR-015 architecture allowlists.

**Architecture:** After this phase, `apps.cli.notify.email` is the only notify email CLI surface. `javdb.integrations.notify.email` exposes typed service/report/log/delivery modules without command-line parsing or process exit behavior.

**Tech Stack:** Python 3.11, pytest, AST architecture guard.

**Source spec:** [ADR-015](ADR-015-integrations-interface-boundary.md), D6, D8, and D10.

**Non-negotiable:** Preserve notify behavior and exit codes while removing compatibility exports. SMTP failure must still return exit code 2 through `apps.cli.notify.email`.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/notify/email/__init__.py` | Remove bake wrapper exports. |
| `javdb/integrations/notify/email/_legacy.py` | Delete after remaining code is moved. |
| `javdb/integrations/notify/email/service.py` | Own final orchestration. |
| `javdb/integrations/notify/email/log_analysis.py` | Own final log/stat analysis helpers. |
| `javdb/integrations/notify/email/report_builder.py` | Own final report formatting helpers. |
| `javdb/integrations/notify/email/delivery.py` | Own final SMTP/log-conversion helpers. |
| `tests/architecture/test_integrations_interface_boundary.py` | Remove notify allowlist entries. |
| `tests/unit/test_email_notification_p0.py` | Update imports and assert SMTP failure exit behavior. |
| `tests/unit/test_email_notification_extended.py` | Update imports. |
| `tests/integration/test_pipeline.py` | Update imports. |
| `apps/cli/notify/README.md` | Remove bake wrapper note. |
| `javdb/integrations/notify/README.md` | Document final service/report/log/delivery boundary. |

---

## Task 1: Move Remaining Legacy Logic Into Final Modules

**Files:**
- Modify: `javdb/integrations/notify/email/service.py`
- Modify: `javdb/integrations/notify/email/log_analysis.py`
- Modify: `javdb/integrations/notify/email/report_builder.py`
- Modify: `javdb/integrations/notify/email/delivery.py`
- Modify: `javdb/integrations/notify/email/_legacy.py`

- [ ] **Step 1: Move service orchestration out of `_legacy.py`.**

Move `_legacy.run_email_notification_from_options(options)` into
`service.py` (it becomes the body of, or is called by,
`run_email_notification`).

Keep the public signature
`run_email_notification(options: EmailNotificationOptions) -> EmailNotificationResult`.

The result must preserve:

- exit code `2` when SMTP send fails and `dry_run` is false;
- exit code `0` for dry runs;
- current success/failure subject construction.

> **Implementation note (patchability + no CLI surface).** The orchestration
> calls many helpers (`send_email`, `analyze_*`, `extract_*`,
> `find_proxy_ban_html_files`, `extract_proxy_ban_summary`, `find_latest_*`,
> `_resolve_default_*`, `_load_pending_verify_records`, the report/section
> builders, etc.). Import them INTO `service.py` from their responsibility
> modules (`delivery`/`log_analysis`/`report_builder`) and reference them as
> `service`-module globals, so the `test_email_notification_p0.py` tests can
> monkeypatch them at `javdb.integrations.notify.email.service.<name>`. Do NOT
> bring `argparse`, `parse_arguments`, `main`, the `__main__` block, or
> `sys.exit` into `service.py` (verified: `sys.exit` lives ONLY in
> `_legacy.main()`; the orchestration returns an `EmailNotificationResult` and
> never calls `sys.exit`). After the move `service.py` must have no CLI surface.

- [ ] **Step 2: Move remaining helper functions.**

Move any helper still imported from `_legacy.py` into one of:

```text
log_analysis.py
report_builder.py
delivery.py
```

Use the responsibility boundaries from IMP-ADR015-06. Do not leave production code
calling `_legacy.py`.

---

## Task 2: Delete Bake Wrapper

**Files:**
- Modify: `javdb/integrations/notify/email/__init__.py`
- Delete: `javdb/integrations/notify/email/_legacy.py`

- [ ] **Step 1: Replace `__init__.py` with final exports.**

> **Implementation note (retain `send_email` re-export).** `send_email` has live
> PRODUCTION callers that import it from the package path and must stay
> re-exported (the ADR-015 boundary allows "non-CLI helper exports where
> intentionally retained"): `apps/api/routers/operations.py` (lines 227, 292,
> `from javdb.integrations.notify.email import send_email`) and
> `tests/unit/test_operations_endpoints.py` (patches
> `javdb.integrations.notify.email.send_email`). It now lives in `delivery.py`.
> Dropping it breaks the REST email-test endpoint and those tests. Do NOT edit
> operations.py or test_operations_endpoints.py.

Use:

```python
"""Email notification service package."""

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult
from javdb.integrations.notify.email.service import run_email_notification
from javdb.integrations.notify.email.delivery import send_email

__all__ = [
    "EmailNotificationOptions",
    "EmailNotificationResult",
    "run_email_notification",
    "send_email",
]
```

- [ ] **Step 2: Delete `_legacy.py`.**

Run:

```bash
git rm javdb/integrations/notify/email/_legacy.py
```

---

## Task 3: Update Tests And Allowlist

**Files:**
- Modify: `tests/architecture/test_integrations_interface_boundary.py`
- Modify: `tests/unit/test_email_notification_p0.py`
- Modify: `tests/unit/test_email_notification_extended.py`
- Modify: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Update imports.**

Use:

```text
apps.cli.notify.email
```

for parser and CLI exit tests.

Use:

```text
javdb.integrations.notify.email.service
javdb.integrations.notify.email.log_analysis
javdb.integrations.notify.email.report_builder
javdb.integrations.notify.email.delivery
```

for domain behavior tests.

> **Implementation note (`test_email_notification_p0.py` migration — it currently
> targets the deleted `_legacy`).** Phase 6 left p0 doing
> `from javdb.integrations.notify.email import _legacy as en`, patching ~17
> helpers on `en` (`send_email`, `analyze_*`, `extract_*`,
> `find_proxy_ban_html_files`, `extract_proxy_ban_summary`, `find_latest_*`,
> `_resolve_default_*`, `_load_pending_verify_records`), and calling `en.main()`
> (which `sys.exit`s) inside `with pytest.raises(SystemExit)`. After Phase 7:
> - rebind `en` to `javdb.integrations.notify.email.service` and patch the
>   helpers at `service.<name>` (the orchestration now resolves them there);
> - replace `en.main()` with `apps.cli.notify.email.main([])`, which RETURNS the
>   int exit code (it does not `sys.exit`; only the adapter's `__main__` wraps it
>   in `SystemExit`). So change `with pytest.raises(SystemExit) as exc: en.main()`
>   / `assert exc.value.code == N` to `assert apps.cli.notify.email.main([]) == N`.
>   Keep asserting exit code 2 on SMTP failure (outside dry-run) and 0 otherwise.
> Confirm `tests/unit/test_email_notification_extended.py` and
> `tests/integration/test_pipeline.py` have no remaining `_legacy` references
> (Phase 6 already pointed them at the responsibility submodules).

- [ ] **Step 2: Remove notify allowlist entries.**

Remove notify entries from:

```python
INTEGRATION_CLI_SURFACE_ALLOWLIST
APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST
```

No `javdb/integrations/notify/email/*` file may contain:

```text
argparse
parse_arguments
def main
sys.exit
__main__
```

---

## Task 4: Update Documentation

**Files:**
- Modify: `apps/cli/notify/README.md`
- Modify: `javdb/integrations/notify/README.md`

- [ ] **Step 1: Remove bake wording.**

Remove any Phase 6 bake-wrapper note from both READMEs.

- [ ] **Step 2: Document final boundary.**

`apps/cli/notify/README.md` describes `email.py` as the real CLI adapter.

`javdb/integrations/notify/README.md` describes:

- `email/service.py` as orchestration;
- `email/log_analysis.py` as log/stat extraction;
- `email/report_builder.py` as subject/body formatting;
- `email/delivery.py` as SMTP/log-conversion delivery helpers.

---

## Task 5: Verify Phase 7

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_integrations_interface_boundary.py -v
pytest tests/unit/test_email_notification_options.py -v
pytest tests/unit/test_email_notification_p0.py -v
pytest tests/unit/test_email_notification_extended.py -v
pytest tests/integration/test_pipeline.py -k email -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI-surface searches.**

```bash
rg -n "argparse|parse_arguments|def main|sys\\.exit|__main__" javdb/integrations/notify/email
rg -n "_legacy|sys\\.modules\\[__name__\\]" javdb/integrations/notify/email apps/cli/notify/email.py tests/unit/test_email_notification_p0.py tests/unit/test_email_notification_extended.py tests/integration/test_pipeline.py
```

Expected: no results.

- [ ] **Step 3: Commit.**

```bash
git add javdb/integrations/notify/email \
        tests/architecture/test_integrations_interface_boundary.py \
        tests/unit/test_email_notification_p0.py \
        tests/unit/test_email_notification_extended.py \
        tests/integration/test_pipeline.py \
        apps/cli/notify/README.md \
        javdb/integrations/notify/README.md \
        docs/design/ADR-015-Integrations-Interface/IMP-ADR015-07-integrations-phase7-notify-cleanup.md
git add -u javdb/integrations/notify/email/_legacy.py
git commit -m "refactor(integrations): remove notify email bake wrapper"
```
