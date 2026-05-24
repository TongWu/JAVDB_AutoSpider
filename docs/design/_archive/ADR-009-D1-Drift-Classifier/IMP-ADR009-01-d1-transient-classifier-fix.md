# IMP-ADR009-01: ADR-009 Phase 0 - D1 Transient Classifier Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value |
| ----------- | ----- |
| **Status**  | Completed |
| **Date**    | 2026-05-24 |
| **Phase**   | P0 |
| **Related** | [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md) |

**Goal:** Ensure Cloudflare D1 HTTP 400 / code 7500 responses containing `Network connection lost.` are classified as transient and retried.

**Architecture:** Keep the D1 retry policy centralized in `javdb.storage.d1_client`. Extend the existing text-keyword classifier and pin the behavior with one focused unit test.

**Tech Stack:** Python, pytest, D1 client error-classification helpers.

**Source spec:** [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), D1 / Layer 0.

---

## Files

| Path | Responsibility |
| --- | --- |
| `javdb/storage/d1_client.py` | Owns D1 transient/permanent error classification and retry behavior. |
| `tests/unit/test_d1_dual.py` | Regression coverage for classifying `Network connection lost.` as transient. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md` | Records P0 completion evidence. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md` | Chinese mirror of P0 completion evidence. |

---

## Task 1: Add The Transient Keyword

- [x] **Step 1: Extend `_TRANSIENT_ERROR_KEYWORDS`.**

Add the lowercase keyword:

```python
"connection lost",
```

Expected location: `javdb/storage/d1_client.py`, next to other retryable D1 message fragments such as `timeout`, `overloaded`, and `temporarily`.

- [x] **Step 2: Keep the match message-based.**

Do not special-case HTTP status `400` or Cloudflare code `7500` directly. ADR-009 depends on the existing design where the response envelope is insufficient and the human-readable D1 message determines retryability.

---

## Task 2: Pin The Regression

- [x] **Step 1: Add a focused unit test.**

Expected behavior:

```python
def test_400_network_connection_lost_treated_as_transient():
    """HTTP 400 + code 7500 + Network connection lost is retryable."""
```

The test must assert that the D1 client raises or routes through `D1TransientError`, not `D1PermanentError`.

- [x] **Step 2: Run the focused test.**

```bash
pytest tests/unit/test_d1_dual.py::test_400_network_connection_lost_treated_as_transient -v
```

Expected: PASS.

---

## Completion Evidence

- 2026-05-24 local verification: `pytest tests/unit/test_d1_dual.py::test_400_network_connection_lost_treated_as_transient -v` passed.
- 2026-05-24 source check: `rg -n '"connection lost"' javdb/storage/d1_client.py tests/unit/test_d1_dual.py` found the classifier keyword and regression coverage.

No further work belongs in this phase unless the regression test fails again.
