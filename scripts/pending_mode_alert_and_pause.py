"""Inject ``pipeline_paused_until: <ISO>`` into ``.publish-config.yml``.

ADR-006 (Pending Mode default rollout) replaces the old Phase 3
auto-fallback mechanism — which switched WriteMode to audit on critical
pending-mode alerts — with a hard pause. When the email pipeline detects
a critical alert (``pending_residual_count > 0``,
``derived_recompute_drift > 0`` or ``cleanup_path_mismatch_count > 0``),
the workflow calls this script to mark the pipeline paused for the next
24h. The next ingestion run sees ``pipeline_paused_until`` in the future,
exits 0 cleanly via the pause gate, and waits for an operator to fix
the root cause and clear the marker.

Why a pause instead of an audit fallback: the old fallback let pending
mode failures degrade silently into audit mode, removing pressure to
fix the underlying bug. Forcing a pause makes the incident visible and
demands explicit operator action before the pipeline resumes.

Idempotent: if a future ``pipeline_paused_until`` is already in
the file, the timer is extended to the later of the two timestamps.
The default pause window is 24h so a single-run incident does not
silently quiet alerts forever.

Usage::

    python3 -m scripts.pending_mode_alert_and_pause \\
        --reason "derived_recompute_drift > 0 in session 12345" \\
        --hours 24
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_KEY = "pipeline_paused_until"
_REASON_KEY = "pipeline_paused_reason"
_GENERATED_BLOCK_HEADER = (
    "# ADR-006 pause marker — written by "
    "scripts/pending_mode_alert_and_pause.py."
)


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.pending_mode_alert_and_pause",
        description=(
            "Pause the ingestion pipeline by writing pipeline_paused_until "
            "to .publish-config.yml. Per ADR-006 this replaces the legacy "
            "audit-mode auto-fallback."
        ),
    )
    p.add_argument(
        "--config",
        default=".publish-config.yml",
        help="Path to .publish-config.yml (default: repo root file).",
    )
    p.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="How long to pause the pipeline (default: 24h).",
    )
    p.add_argument(
        "--reason",
        type=str,
        default="",
        help=(
            "Free-text reason persisted alongside the timestamp so an "
            "operator inspecting the config sees why the pause was "
            "engaged."
        ),
    )
    return p.parse_args(argv)


def _read_existing_until(path: str) -> Optional[datetime]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(
                    rf"^\s*{_KEY}:\s*['\"]?([0-9T:\-+Z\.]+)['\"]?",
                    line,
                )
                if m:
                    raw = m.group(1).replace("Z", "+00:00")
                    try:
                        dt = datetime.fromisoformat(raw)
                    except ValueError:
                        return None
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
    except Exception:
        return None
    return None


def _strip_existing_block(text: str) -> str:
    """Remove the entire pause block (header + comment lines + key lines).

    Strategy: when we hit the generated marker line, skip every
    subsequent line — comments, key/value pairs, blank lines — until
    we encounter a non-comment / non-key non-blank line that signals
    the start of unrelated content.  Stand-alone keys outside any
    marked block are also stripped so a hand-edited file converges
    after one auto-write.
    """
    lines = text.splitlines(keepends=True)
    out = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(_GENERATED_BLOCK_HEADER.strip()):
            skip = True
            continue
        if skip:
            if stripped == "":
                skip = False
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith(f"{_KEY}:") or stripped.startswith(
                f"{_REASON_KEY}:",
            ):
                continue
            # Hit unrelated content — stop skipping and keep this line.
            skip = False
        # Also strip stand-alone keys (in case the header was lost)
        if stripped.startswith(f"{_KEY}:") or stripped.startswith(
            f"{_REASON_KEY}:"
        ):
            continue
        out.append(line)
    return "".join(out)


def write_pause(
    config_path: str,
    *,
    until: datetime,
    reason: str,
) -> None:
    Path(os.path.dirname(config_path) or ".").mkdir(
        parents=True, exist_ok=True,
    )
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = ""

    text = _strip_existing_block(text).rstrip()
    if text:
        text += "\n\n"

    block_lines = [
        _GENERATED_BLOCK_HEADER,
        "# Engaged on critical pending-mode alert (residual / drift /",
        "# cleanup path mismatch). Every ingestion workflow (Daily / AdHoc)",
        "# checks this key in its pause gate and exits cleanly while the",
        "# timestamp is in the future. Once the root cause is fixed,",
        "# revert the commit that introduced these lines (or delete them",
        "# manually) so the next run re-engages.",
        f"{_KEY}: '{until.isoformat()}'",
    ]
    if reason:
        # Escape any single quotes in reason
        safe = reason.replace("'", "''")
        block_lines.append(f"{_REASON_KEY}: '{safe}'")

    text += "\n".join(block_lines) + "\n"

    # Atomic write so two concurrent invocations (Daily + AdHoc finishing
    # together) cannot interleave bytes inside .publish-config.yml.
    import tempfile
    target_dir = os.path.dirname(os.path.abspath(config_path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".publish-config.", suffix=".tmp", dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    args = _parse_args(argv)
    new_until = datetime.now(tz=timezone.utc) + timedelta(hours=args.hours)
    existing = _read_existing_until(args.config)
    if existing is not None and existing > new_until:
        # Don't shorten an already-engaged longer window.
        new_until = existing
    write_pause(args.config, until=new_until, reason=args.reason)
    print(
        f"pipeline_paused_until set to {new_until.isoformat()} "
        f"(reason: {args.reason or '<none>'})",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
