# IMP-ADR009-04: ADR-009 Phase 3 - Email Drift Diagnosis Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value |
| ----------- | ----- |
| **Status**  | Draft |
| **Date**    | 2026-05-24 |
| **Phase**   | P3 |
| **Related** | [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md), [IMP-ADR009-03](IMP-ADR009-03-drift-diagnose-guarded-apply.md) |

**Goal:** Add non-blocking email rendering for ADR-009 drift diagnosis so operators can see verdicts and manual apply commands directly in pending-mode drift notifications.

**Architecture:** The email layer invokes `python -m apps.cli.db.drift_diagnose --since 1 --json` as a time-bounded subprocess only when a pending-mode drift advisory already exists. It accepts diagnose exit codes `0`, `1`, and `2`, validates JSON shape before rendering, and never invokes `--apply`.

**Tech Stack:** Python 3.11+, subprocess, JSON, pytest, GitHub Actions workflow review.

**Source spec:** [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), D6.

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

---

## Task 2: Add Email Diagnosis Tests

**Files:**
- Create: `tests/unit/test_email_drift_integration.py`
- Modify: `tests/unit/test_email_notification_p0.py` only if existing higher-level pending-alert assertions need adjustment.

- [ ] **Step 1: Add subprocess success tests.**

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

Cover:

- `subprocess.TimeoutExpired` -> fallback text and empty suspects;
- non-JSON stdout -> fallback text and empty suspects;
- valid JSON that is not an object (`[]`, `null`) -> fallback text and empty suspects;
- `suspects` value that is not a list -> fallback text and empty suspects;
- return code outside `{0, 1, 2}` -> fallback text and empty suspects.

- [ ] **Step 3: Add subject prefix tests.**

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

---

## Task 3: Implement Non-Blocking Diagnosis Rendering

**Files:**
- Modify: `javdb/integrations/notify/email.py`

- [ ] **Step 1: Add a subprocess helper.**

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

Run the helper only when the email is already rendering a pending-mode drift advisory with `pending_residual_count > 0`.

- [ ] **Step 2: Add subject prefixes.**

Apply:

```text
[DRIFT-ESCALATE]
[DRIFT-FIX-READY]
```

Escalation takes priority over fix-ready.

- [ ] **Step 3: Preserve existing email delivery behavior.**

Timeout, subprocess crash, invalid JSON, and unsupported exit code must render a fallback section but must not raise out of the email path.

---

## Task 5: Review Workflow Impact

**Files:**
- Review: `.github/workflows/DailyIngestion.yml`
- Review: `.github/workflows/AdHocIngestion.yml`

- [ ] **Step 1: Search for relevant email workflow steps.**

```bash
rg -n "email|pending_health|pending_alert|drift_diagnose" .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml
```

Expected: either no workflow changes are needed because email invokes the CLI via `sys.executable -m`, or both workflows receive matching env/path updates.

- [ ] **Step 2: If workflows change, update them in both files.**

Any workflow change must preserve:

- existing pending health generation;
- existing pending alert pause behavior;
- no `--apply` invocation in GitHub Actions.

---

## Task 6: Final ADR-009 Verification And Documentation

- [ ] **Step 1: Run focused ADR-009 verification.**

```bash
pytest tests/unit/test_d1_dual.py::test_400_network_connection_lost_treated_as_transient tests/unit/test_drift_diagnose.py tests/unit/test_email_drift_integration.py -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI smoke checks.**

```bash
python3 -m apps.cli.db.drift_diagnose --help
python3 -m apps.cli.db.drift_diagnose --since 1 --json
```

Expected: `--help` exits 0. The JSON command exits 0, 1, or 2 depending on local drift state and emits valid JSON when D1 credentials are available. If local D1 credentials are absent, record that as an environment limitation rather than a code failure.

- [ ] **Step 3: Update ADR status after all phases are implemented.**

Update both ADR files:

```markdown
**Status**: Implemented
**Last verified**: <date>
```

Then replace the pending implementation bullets with completion evidence for P0-P3 and links to all four IMPs.

- [ ] **Step 4: Run test selection.**

```bash
python3 scripts/ci/select_tests.py
```

Expected: exits 0 and reports selected tests for the final change set.
