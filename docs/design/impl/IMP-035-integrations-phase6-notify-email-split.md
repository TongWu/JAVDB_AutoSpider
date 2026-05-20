# IMP-035: ADR-015 Phase 6 - Notify Email Split

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 6 by splitting notify email into a typed command package with separate log analysis, report building, delivery, and service orchestration modules while keeping a short bake wrapper.

**Architecture:** `apps.cli.notify.email` becomes the real CLI adapter. `javdb.integrations.notify.email` becomes a package with `options.py`, `result.py`, `service.py`, `log_analysis.py`, `report_builder.py`, and `delivery.py`. A bake wrapper keeps selected legacy exports until IMP-036.

**Tech Stack:** Python 3.11, dataclasses, argparse, pytest, smtplib/email stdlib, existing logging/config/storage helpers.

**Source spec:** [ADR-015](../adr/ADR-015-integrations-interface-boundary.md), D1-D6, D8, and D10.

**Non-negotiable:** Preserve notify flags/defaults, log analysis semantics, report body/subject content, SMTP failure exit code, pending-mode sections, dedup section, proxy-ban attachments/summary, drift advisory, git side effects, temporary attachment cleanup, streaming logs, and current dry-run behavior.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/notify/email.py` | Move into package as bake legacy implementation. |
| `javdb/integrations/notify/email/__init__.py` | Temporary bake wrapper exports plus new contract exports. |
| `javdb/integrations/notify/email/options.py` | `EmailNotificationOptions` dataclass. |
| `javdb/integrations/notify/email/result.py` | `EmailNotificationResult` dataclass. |
| `javdb/integrations/notify/email/log_analysis.py` | Log parsing, stats extraction, pending-mode/drift/proxy-ban input analysis. |
| `javdb/integrations/notify/email/report_builder.py` | Subject/body formatting. |
| `javdb/integrations/notify/email/delivery.py` | SMTP delivery and dry-run send behavior. |
| `javdb/integrations/notify/email/service.py` | Email notification orchestration. |
| `apps/cli/notify/email.py` | Real CLI parser and adapter. |
| `tests/unit/test_email_notification_options.py` | New options/result/parser tests. |
| `tests/unit/test_email_notification_p0.py` | Update imports and preserve SMTP failure coverage. |
| `tests/unit/test_email_notification_extended.py` | Update imports and preserve report/log coverage. |
| `tests/integration/test_pipeline.py` | Update imports for report builder functions. |
| `apps/cli/notify/README.md` | Mark alias as replaced by real CLI adapter. |
| `javdb/integrations/notify/README.md` | Document package split and bake wrapper. |

---

## Task 1: Move Email Module Into A Package

**Files:**
- Move: `javdb/integrations/notify/email.py`
- Create: `javdb/integrations/notify/email/__init__.py`

- [ ] **Step 1: Move current implementation.**

Run:

```bash
git mv javdb/integrations/notify/email.py javdb/integrations/notify/email_legacy_tmp.py
mkdir -p javdb/integrations/notify/email
git mv javdb/integrations/notify/email_legacy_tmp.py javdb/integrations/notify/email/_legacy.py
```

- [ ] **Step 2: Add bake wrapper exports.**

Create `javdb/integrations/notify/email/__init__.py`:

```python
"""Email notification service package.

Selected legacy exports remain during ADR-015 Phase 6 and are removed by
IMP-036 after the bake window.
"""

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult
from javdb.integrations.notify.email.service import run_email_notification
from javdb.integrations.notify.email._legacy import (
    analyze_pikpak_log,
    analyze_pipeline_log,
    analyze_spider_log,
    analyze_uploader_log,
    check_workflow_job_status,
    convert_log_to_txt,
    extract_adhoc_info_from_csv,
    extract_dedup_statistics,
    extract_pikpak_statistics,
    extract_proxy_ban_summary,
    extract_spider_statistics,
    extract_uploader_statistics,
    find_latest_adhoc_csv,
    find_latest_daily_csv,
    find_proxy_ban_html_files,
    format_adhoc_info,
    format_email_report,
    get_proxy_ban_summary,
    get_report_display_datetime,
    send_email,
)

__all__ = [
    "EmailNotificationOptions",
    "EmailNotificationResult",
    "run_email_notification",
    "analyze_pikpak_log",
    "analyze_pipeline_log",
    "analyze_spider_log",
    "analyze_uploader_log",
    "check_workflow_job_status",
    "convert_log_to_txt",
    "extract_adhoc_info_from_csv",
    "extract_dedup_statistics",
    "extract_pikpak_statistics",
    "extract_proxy_ban_summary",
    "extract_spider_statistics",
    "extract_uploader_statistics",
    "find_latest_adhoc_csv",
    "find_latest_daily_csv",
    "find_proxy_ban_html_files",
    "format_adhoc_info",
    "format_email_report",
    "get_proxy_ban_summary",
    "get_report_display_datetime",
    "send_email",
]
```

---

## Task 2: Add Notify Contract

**Files:**
- Create: `javdb/integrations/notify/email/options.py`
- Create: `javdb/integrations/notify/email/result.py`
- Create: `tests/unit/test_email_notification_options.py`

- [ ] **Step 1: Write options/result tests.**

Create `tests/unit/test_email_notification_options.py`:

```python
from __future__ import annotations

from apps.cli.notify.email import options_from_args, parse_args
from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult


def test_email_options_defaults():
    options = EmailNotificationOptions()

    assert options.csv_path is None
    assert options.mode == "daily"
    assert options.dry_run is False
    assert options.from_pipeline is False
    assert options.session_id is None
    assert options.verify_jsonl is None
    assert options.health_snapshot is None


def test_email_cli_maps_flags_to_options():
    options = options_from_args(
        parse_args([
            "--mode",
            "adhoc",
            "--csv-path",
            "reports/AdHoc/file.csv",
            "--dry-run",
            "--from-pipeline",
            "--session-id",
            "42",
            "--verify-jsonl",
            "reports/D1/d1_drift.jsonl",
            "--health-snapshot",
            "reports/D1/pending_health_24h.json",
        ])
    )

    assert options.mode == "adhoc"
    assert options.csv_path == "reports/AdHoc/file.csv"
    assert options.dry_run is True
    assert options.from_pipeline is True
    assert options.session_id == "42"
    assert options.verify_jsonl == "reports/D1/d1_drift.jsonl"
    assert options.health_snapshot == "reports/D1/pending_health_24h.json"


def test_email_result_exit_code_for_smtp_failure():
    result = EmailNotificationResult(email_sent=False, dry_run=False, subject="subject")

    assert result.exit_code == 2


def test_email_result_exit_code_for_dry_run():
    result = EmailNotificationResult(email_sent=False, dry_run=True, subject="subject")

    assert result.exit_code == 0
```

- [ ] **Step 2: Implement options/result.**

Create `javdb/integrations/notify/email/options.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EmailNotificationOptions:
    csv_path: str | None = None
    mode: Literal["daily", "adhoc"] = "daily"
    dry_run: bool = False
    from_pipeline: bool = False
    session_id: str | None = None
    verify_jsonl: str | None = None
    health_snapshot: str | None = None
```

Create `javdb/integrations/notify/email/result.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EmailNotificationResult:
    email_sent: bool
    dry_run: bool
    subject: str
    has_critical_errors: bool = False
    attachments: Sequence[str] = field(default_factory=tuple)
    cleanup_errors: Sequence[str] = field(default_factory=tuple)

    @property
    def exit_code(self) -> int:
        if not self.dry_run and not self.email_sent:
            return 2
        return 0
```

---

## Task 3: Replace CLI Alias With Real Adapter

**Files:**
- Modify: `apps/cli/notify/email.py`

- [ ] **Step 1: Replace `apps/cli/notify/email.py`.**

Use:

```python
from __future__ import annotations

import argparse

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.service import run_email_notification


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Email Notification for JavDB Pipeline")
    parser.add_argument("--csv-path", type=str, help="Path to the CSV file to attach")
    parser.add_argument("--mode", type=str, choices=["daily", "adhoc"], default="daily", help="Pipeline mode: daily or adhoc (default: daily)")
    parser.add_argument("--dry-run", action="store_true", help="Print email content without sending")
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--session-id", type=str, default=None, help="Report session ID for fetching stats from SQLite")
    parser.add_argument("--verify-jsonl", type=str, default=None, help="Path to reports/D1/d1_drift.jsonl. When provided, the email renders a 'Pending Mode Verification' section using the pending_session_verify records and may prefix the subject with [PENDING-ALERT] / [PENDING-PAUSE]. Defaults to $REPORTS_DIR/D1/d1_drift.jsonl when the file exists.")
    parser.add_argument("--health-snapshot", type=str, default=None, help="Path to reports/D1/pending_health_24h.json (Phase 3 Health Snapshot). When provided, an additional 24h aggregate block is rendered after Pending Mode Verification.")
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> EmailNotificationOptions:
    return EmailNotificationOptions(
        csv_path=args.csv_path,
        mode=args.mode,
        dry_run=args.dry_run,
        from_pipeline=args.from_pipeline,
        session_id=args.session_id,
        verify_jsonl=args.verify_jsonl,
        health_snapshot=args.health_snapshot,
    )


def main(argv: list[str] | None = None) -> int:
    return run_email_notification(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Task 4: Split Notify Responsibilities

**Files:**
- Create: `javdb/integrations/notify/email/log_analysis.py`
- Create: `javdb/integrations/notify/email/report_builder.py`
- Create: `javdb/integrations/notify/email/delivery.py`
- Create: `javdb/integrations/notify/email/service.py`
- Modify: `javdb/integrations/notify/email/_legacy.py`

- [ ] **Step 1: Move log analysis functions.**

Move these functions from `_legacy.py` to `log_analysis.py`:

```text
analyze_spider_log
analyze_uploader_log
analyze_pikpak_log
analyze_pipeline_log
extract_spider_statistics
extract_uploader_statistics
extract_pikpak_statistics
extract_dedup_statistics
find_proxy_ban_html_files
extract_proxy_ban_summary
get_proxy_ban_summary
check_workflow_job_status
_load_pending_verify_records
_evaluate_pending_alerts
_build_dual_drift_advisory
```

- [ ] **Step 2: Move report-building functions.**

Move these functions to `report_builder.py`:

```text
get_report_display_datetime
extract_adhoc_info_from_csv
format_adhoc_info
find_latest_adhoc_csv
find_latest_daily_csv
format_email_report
_plain_to_html
_format_pending_verify_section
_format_health_snapshot_section
_build_pending_subject_prefix
```

- [ ] **Step 3: Move delivery functions.**

Move these functions to `delivery.py`:

```text
send_email
convert_log_to_txt
```

- [ ] **Step 4: Create orchestration service.**

Create `service.py` with:

```python
from __future__ import annotations

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult


def run_email_notification(options: EmailNotificationOptions) -> EmailNotificationResult:
    from javdb.integrations.notify.email import _legacy

    return _legacy.run_email_notification_from_options(options)
```

- [ ] **Step 5: Extract legacy main body into `run_email_notification_from_options`.**

In `_legacy.py`, extract the current body of `main()` after argument parsing
into `run_email_notification_from_options(options: EmailNotificationOptions) -> EmailNotificationResult`.

Replace every `args.<field>` access with `options.<field>`. Replace final
`sys.exit(2)` and `sys.exit(0)` with `EmailNotificationResult(email_sent=email_sent, dry_run=options.dry_run, subject=subject)`.

Keep `_legacy.main()` and `parse_arguments()` during Phase 6 for the bake
wrapper. IMP-036 removes them.

---

## Task 5: Update Tests And Docs

**Files:**
- Modify: `tests/unit/test_email_notification_p0.py`
- Modify: `tests/unit/test_email_notification_extended.py`
- Modify: `tests/integration/test_pipeline.py`
- Modify: `apps/cli/notify/README.md`
- Modify: `javdb/integrations/notify/README.md`

- [ ] **Step 1: Update tests by responsibility.**

Use:

```text
javdb.integrations.notify.email.log_analysis
```

for log/stat extraction tests.

Use:

```text
javdb.integrations.notify.email.report_builder
```

for `format_email_report`, subject, pending-mode, dedup, and drift body tests.

Use:

```text
javdb.integrations.notify.email.delivery
```

for SMTP send tests.

Use:

```text
apps.cli.notify.email
```

for parser and CLI exit-code tests.

- [ ] **Step 2: Keep wrapper compatibility tests.**

Keep imports from `javdb.integrations.notify.email` working during Phase 6.
These imports are migrated and the wrapper is deleted in IMP-036.

- [ ] **Step 3: Update READMEs.**

Document that notify is in a Phase 6 bake state: real CLI adapter exists under
`apps.cli.notify.email`, while selected legacy package exports remain until
IMP-036.

---

## Task 6: Verify Phase 6

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/unit/test_email_notification_options.py -v
pytest tests/unit/test_email_notification_p0.py -v
pytest tests/unit/test_email_notification_extended.py -v
pytest tests/integration/test_pipeline.py -k email -v
pytest tests/architecture/test_integrations_interface_boundary.py -v
```

Expected: PASS.

- [ ] **Step 2: Verify apps CLI alias is gone.**

```bash
rg -n "sys\\.modules\\[__name__\\]|import_module\\(\"javdb\\.integrations\\.notify\\.email" apps/cli/notify/email.py
```

Expected: no results.

- [ ] **Step 3: Review workflows and docs.**

```bash
rg -n "apps\\.cli\\.notify|email_notification|notify\\.email" .github/workflows README.md JAVDB_AutoSpider.wiki 2>/dev/null
```

Expected: workflow command invocations remain unchanged.

- [ ] **Step 4: Commit.**

```bash
git add javdb/integrations/notify/email \
        apps/cli/notify/email.py \
        tests/unit/test_email_notification_options.py \
        tests/unit/test_email_notification_p0.py \
        tests/unit/test_email_notification_extended.py \
        tests/integration/test_pipeline.py \
        apps/cli/notify/README.md \
        javdb/integrations/notify/README.md
git add -u javdb/integrations/notify/email.py
git commit -m "refactor(integrations): split notify email service"
```
