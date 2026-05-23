# IMP-ADR009-04: ADR-009 Phase 3 - Email Drift Diagnosis Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value |
| ----------- | ----- |
| **Status**  | Completed |
| **Date**    | 2026-05-24 |
| **Phase**   | P3 |
| **Related** | [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md), [IMP-ADR009-03](IMP-ADR009-03-drift-diagnose-guarded-apply.md) |

**Goal:** Add non-blocking email rendering for ADR-009 drift diagnosis so operators can see verdicts and manual apply commands directly in pending-mode drift notifications.

**Architecture:** The email layer invokes `python -m apps.cli.db.drift_diagnose --since 1 --json` as a time-bounded subprocess only when a pending-mode drift advisory already exists. It accepts diagnose exit codes `0`, `1`, and `2`, validates JSON shape before rendering, and never invokes `--apply`.

**Tech Stack:** Python 3.11+, subprocess, JSON, pytest, GitHub Actions workflow review.

**Source spec:** [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), D6.

**Completion note:** P3 completion is scoped only to email drift diagnosis integration: read-only subprocess invocation, non-blocking fallback behavior, drift diagnosis subject prefixes, and workflow impact review. It does not cover the P2 guarded `--apply` implementation, which remains tracked independently in [IMP-ADR009-03](IMP-ADR009-03-drift-diagnose-guarded-apply.md).

**Checkbox semantics for this closure pass:** Only steps rerun or directly verified during this closure pass are checked. Historical RED, TDD-authoring, implementation, and documentation-authoring steps remain unchecked when they were not re-run; current completion status is based on the checked closure verification and the Completion Evidence below.

---

## Files

| Path | Responsibility |
| --- | --- |
| `javdb/integrations/notify/email.py` | Runs read-only diagnosis, renders fallback/body text, and applies subject prefixes. |
| `tests/unit/test_email_drift_integration.py` | Unit coverage for subprocess success, fallback, return-code allowlist, JSON schema validation, and subject tags. |
| `tests/unit/test_email_notification_p0.py` | Existing pending-mode email coverage to keep behavior stable. |
| `.github/workflows/DailyIngestion.yml` | Review whether email job env/path wiring must change. |
| `.github/workflows/AdHocIngestion.yml` | Review parity with DailyIngestion if workflow changes are needed. |
| `docs/handbook/en/ops/d1-rollback.md` | Notes how email diagnosis relates to manual operator apply. |
| `docs/handbook/zh/ops/d1-rollback.md` | Chinese mirror of the operator note. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md` | Records P3 status and final ADR completion evidence. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md` | Chinese mirror of P3/final status. |

---

## Task 1: Verify P1/P2 Baseline

- [ ] **Step 1: Run drift diagnose tests before touching email.**

```bash
pytest tests/unit/test_drift_diagnose.py -v
python3 -m apps.cli.db.drift_diagnose --help
```

Expected: tests pass and help exits 0. If this fails, complete [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md) and [IMP-ADR009-03](IMP-ADR009-03-drift-diagnose-guarded-apply.md) first.

Closure-pass note: the exact baseline pair was not run before the closure edits because the P3 implementation already existed. The CLI help and full `tests/unit/test_drift_diagnose.py` coverage were verified through Task 6 instead.

---

## Task 2: Add Email Diagnosis Tests

**Files:**
- Create: `tests/unit/test_email_drift_integration.py`
- Modify: `tests/unit/test_email_notification_p0.py` only if existing higher-level pending-alert assertions need adjustment.

- [ ] **Step 1: Add subprocess success tests.**

Closure-pass note: historical TDD-authoring step; not re-run during this closure pass. Existing subprocess success coverage was verified by `pytest tests/unit/test_email_drift_integration.py -v`.

Patch `javdb.integrations.notify.email.subprocess.run` and cover:

```python
def test_safe_to_apply_renders_section_with_suggested_command(mock_run):
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout=json.dumps({
            "suspects": [{
                "session_id": "sid",
                "verdict": "SAFE_TO_APPLY",
                "d1_orphan_movie_count": 0,
                "d1_orphan_torrent_count": 1,
                "suggested_command": "python3 -m apps.cli.db.drift_diagnose --apply --session-id sid",
            }]
        }),
        stderr="",
    )
```

Assertions:

- section contains `Drift Diagnosis`;
- section contains `SAFE_TO_APPLY`;
- section contains `python3 -m apps.cli.db.drift_diagnose --apply --session-id sid`;
- returned suspect list contains the parsed suspect.

- [ ] **Step 2: Add fallback tests.**

Closure-pass note: historical TDD-authoring step; not re-run during this closure pass. Existing timeout, invalid JSON, invalid schema, subprocess crash, and unsupported return-code coverage was verified by `pytest tests/unit/test_email_drift_integration.py -v`.

Cover:

- `subprocess.TimeoutExpired` -> fallback text and empty suspects;
- non-JSON stdout -> fallback text and empty suspects;
- valid JSON that is not an object (`[]`, `null`) -> fallback text and empty suspects;
- `suspects` value that is not a list -> fallback text and empty suspects;
- return code outside `{0, 1, 2}` -> fallback text and empty suspects.

- [ ] **Step 3: Add subject prefix tests.**

Closure-pass note: historical TDD-authoring step; not re-run during this closure pass. Existing subject-prefix coverage was verified by `pytest tests/unit/test_email_drift_integration.py -v`.

Expected rules:

| Suspect verdicts | Prefix |
| --- | --- |
| at least one `ESCALATE_LIVE_DIVERGENCE` | `[DRIFT-ESCALATE] ` |
| at least one `UNEXPECTED_PATTERN` | `[DRIFT-ESCALATE] ` |
| at least one `SAFE_TO_APPLY` and no escalate verdict | `[DRIFT-FIX-READY] ` |
| all `CLEAN` or no suspects | empty string |

- [ ] **Step 4: Run tests and observe expected failure.**

```bash
pytest tests/unit/test_email_drift_integration.py -v
```

Expected: FAIL until email helpers are implemented.

Closure-pass note: historical RED step; not re-run during this closure pass because the implementation already existed. The current P3 email integration tests pass, as recorded in Completion Evidence.

---

## Task 3: Implement Non-Blocking Diagnosis Rendering

**Files:**
- Modify: `javdb/integrations/notify/email.py`

- [ ] **Step 1: Add a subprocess helper.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. The current helper behavior is covered by the passing P3 email integration tests in Completion Evidence.

The helper must call:

```python
[
    sys.executable,
    "-m",
    "apps.cli.db.drift_diagnose",
    "--since",
    "1",
    "--json",
]
```

with:

```python
capture_output=True
text=True
timeout=60
```

Do not pass `--apply`.

- [ ] **Step 2: Enforce return-code allowlist before JSON parsing.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Return-code allowlist behavior is covered by the passing P3 email integration tests in Completion Evidence.

Only parse stdout when:

```python
result.returncode in (0, 1, 2)
```

Any other code returns a fallback section:

```text
Automated diagnosis unavailable: unexpected exit code <code>.
Run manually: python3 -m apps.cli.db.drift_diagnose --since 1 --json
```

- [ ] **Step 3: Validate JSON shape before rendering.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. JSON shape validation behavior is covered by the passing P3 email integration tests in Completion Evidence.

Required checks:

```python
data = json.loads(stdout)
if not isinstance(data, dict):
    return fallback, []
suspects = data.get("suspects", [])
if not isinstance(suspects, list):
    return fallback, []
```

Fallback must never block email delivery.

- [ ] **Step 4: Render the diagnosis section.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Diagnosis rendering is covered by the passing P3 email integration tests in Completion Evidence.

Use a compact text block:

```text
--- Drift Diagnosis ---
  Session: sid
    verdict: SAFE_TO_APPLY
    orphan movies: 0, orphan torrents: 1
    suggested fix: python3 -m apps.cli.db.drift_diagnose --apply --session-id sid
```

Use the project email style if nearby sections already use box-drawing separators; keep tests aligned with the chosen text.

---

## Task 4: Integrate With Pending-Mode Drift Email

**Files:**
- Modify: `javdb/integrations/notify/email.py`

- [ ] **Step 1: Invoke diagnosis only for pending residual drift.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Existing pending-mode email behavior was verified by `pytest tests/unit/test_email_notification_p0.py -v` and the P3 email integration tests.

Run the helper only when the email is already rendering a pending-mode drift advisory with `pending_residual_count > 0`.

- [ ] **Step 2: Add subject prefixes.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Subject prefix behavior is covered by the passing P3 email integration tests in Completion Evidence.

Apply:

```text
[DRIFT-ESCALATE]
[DRIFT-FIX-READY]
```

Escalation takes priority over fix-ready.

- [ ] **Step 3: Preserve existing email delivery behavior.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Existing delivery-path behavior was verified by `pytest tests/unit/test_email_notification_p0.py -v`.

Timeout, subprocess crash, invalid JSON, and unsupported exit code must render a fallback section but must not raise out of the email path.

---

## Task 5: Review Workflow Impact

**Files:**
- Review: `.github/workflows/DailyIngestion.yml`
- Review: `.github/workflows/AdHocIngestion.yml`

- [x] **Step 1: Search for relevant email workflow steps.**

```bash
rg -n "email|pending_health|pending_alert|drift_diagnose" .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml
```

Expected: either no workflow changes are needed because email invokes the CLI via `sys.executable -m`, or both workflows receive matching env/path updates.

Closure-pass result: PASS. Relevant email, pending health, pending alert, and drift diagnose workflow references were reviewed with `rg`; no workflow edits were needed for P3 because email invokes the read-only CLI via the current Python interpreter and no workflow calls `drift_diagnose --apply`.

- [ ] **Step 2: If workflows change, update them in both files.**

Any workflow change must preserve:

- existing pending health generation;
- existing pending alert pause behavior;
- no `--apply` invocation in GitHub Actions.

Closure-pass note: no workflow changes were needed during this closure pass.

---

## Task 6: Final ADR-009 Verification And Documentation

- [x] **Step 1: Run focused ADR-009 verification.**

```bash
pytest tests/unit/test_d1_dual.py::test_400_network_connection_lost_treated_as_transient tests/unit/test_drift_diagnose.py tests/unit/test_email_drift_integration.py -v
```

Expected: PASS.

Closure-pass result: PASS; see Completion Evidence.

- [x] **Step 2a: Run CLI help smoke check.**

```bash
python3 -m apps.cli.db.drift_diagnose --help
```

Expected: `--help` exits 0.

Closure-pass result: PASS using `/opt/anaconda3/bin/python -m apps.cli.db.drift_diagnose --help`. The local equivalent interpreter was used for the help smoke because system `python3` may not have this project's dependencies installed in this environment.

- [ ] **Step 2b: Run JSON diagnose smoke check when D1 credentials are available.**

```bash
python3 -m apps.cli.db.drift_diagnose --since 1 --json
```

Expected: the JSON command exits 0, 1, or 2 depending on local drift state and emits valid JSON when D1 credentials are available. If local D1 credentials are absent, record that as an environment limitation rather than a code failure.

Closure-pass note: not run. Local context did not expose D1/Cloudflare credentials or a `STORAGE_BACKEND=d1` environment (`env | rg -n "CLOUDFLARE|D1|WRANGLER|STORAGE_BACKEND|ACCOUNT_ID|DATABASE"` returned no matches), and running the command would require live D1/network access. This is recorded as an environment limitation, not a code failure.

- [ ] **Step 3: Update ADR status after all phases are implemented.**

Update both ADR files:

```markdown
**Status**: Implemented
**Last verified**: <date>
```

Then replace the pending implementation bullets with completion evidence for P0-P3 and links to all four IMPs.

Closure-pass note: `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md` and `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md` were not edited during this closure pass. ADR status update is outside this P3 email-integration closure scope and is handled by the final ADR closure review; this P3 IMP evidence does not claim ADR status verification.

- [ ] **Step 4: Run test selection.**

```bash
python3 scripts/ci/select_tests.py
```

Expected: exits 0 and reports selected tests for the final change set.

Closure-pass note: not run because this closure pass is scoped to a single IMP update and the user requested the explicit verification commands recorded below.

- [x] **Step 5: Verify this IMP diff has no whitespace errors.**

```bash
git diff --check -- docs/design/ADR-009-D1-Drift-Classifier/IMP-ADR009-04-drift-diagnose-email-integration.md
```

Expected: exits 0.

---

## Completion Evidence

Closure verification run on 2026-05-24:

```bash
pytest tests/unit/test_email_drift_integration.py -v
```

Result: PASS (`21 passed in 0.20s`). This covers read-only subprocess invocation through `sys.executable`, timeout/fallback behavior, allowed diagnose return codes `0`, `1`, and `2`, JSON schema validation, diagnosis rendering, and subject prefix priority.

```bash
pytest tests/unit/test_email_notification_p0.py -v
```

Result: PASS (`9 passed in 0.42s`). This preserves existing pending-mode email notification behavior.

```bash
pytest tests/unit/test_d1_dual.py::test_400_network_connection_lost_treated_as_transient tests/unit/test_drift_diagnose.py tests/unit/test_email_drift_integration.py -v
```

Result: PASS (`87 passed in 0.86s`). This verifies transient D1 error classification, ADR-009 drift diagnosis behavior, and P3 email integration together.

```bash
/opt/anaconda3/bin/python -m apps.cli.db.drift_diagnose --help
```

Result: PASS (exited 0 and printed diagnose/apply CLI usage).

```bash
rg -n "email|pending_health|pending_alert|drift_diagnose" .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml
```

Result: PASS (exited 0). Reviewed `email-notification`, `pending_health`, and `pending_alert` wiring in both workflows; no workflow edits were needed for P3, and no GitHub Actions `drift_diagnose --apply` invocation was introduced.

```bash
env | rg -n "CLOUDFLARE|D1|WRANGLER|STORAGE_BACKEND|ACCOUNT_ID|DATABASE"
```

Result: no matches. `python3 -m apps.cli.db.drift_diagnose --since 1 --json` was intentionally not run because local D1 credentials/config were unavailable and a live diagnose would require D1/network access.

```bash
git diff --check -- docs/design/ADR-009-D1-Drift-Classifier/IMP-ADR009-04-drift-diagnose-email-integration.md
```

Result: PASS (exited 0 with no output).
