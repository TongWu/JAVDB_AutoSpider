"""Inject ``pending_mode_disabled_until: <ISO>`` into ``.publish-config.yml``.

Phase 3 (Ingestion Perfect Rollback) — when the email pipeline detects
a critical pending-mode alert (``pending_residual_count > 0``,
``derived_recompute_drift > 0`` or ``cleanup_path_mismatch_count > 0``),
the workflow auto-fallback step calls this script to disable the new
write path for the next 24h.  The next ingestion run reads the updated
config, sees ``pending_mode_disabled_until`` is in the future, and falls
back to the legacy audit path until the operator either reverts the
commit or lets the timer expire.

Idempotent: if a future ``pending_mode_disabled_until`` is already in
the file, the timer is extended to the later of the two timestamps.
The default fallback window is 24h so a single-run incident does not
silently quiet alerts forever.

Usage::

    python3 -m scripts.pending_mode_auto_fallback \\
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


_KEY = "pending_mode_disabled_until"
_REASON_KEY = "pending_mode_disabled_reason"
_GENERATED_BLOCK_HEADER = (
    "# Phase 3 auto-fallback marker — written by "
    "scripts/pending_mode_auto_fallback.py."
)


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.pending_mode_auto_fallback",
        description=(
            "Auto-fallback Ingestion Perfect Rollback to the audit path "
            "by writing pending_mode_disabled_until to .publish-config.yml."
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
        help="How long to disable pending mode (default: 24h).",
    )
    p.add_argument(
        "--reason",
        type=str,
        default="",
        help=(
            "Free-text reason persisted alongside the timestamp so an "
            "operator inspecting the config sees why the auto-fallback "
            "fired."
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
    """Remove the entire auto-fallback block (header + comment lines + key lines).

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


def write_fallback(
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
        "# Set on critical pending-mode alert (residual / drift / cleanup",
        "# path mismatch).  The DailyIngestion / AdHocIngestion `Resolve",
        "# effective WriteMode` step reads this key and forces the audit",
        "# path while the timestamp is in the future.  Revert the commit",
        "# that introduced these lines (or delete them manually) once the",
        "# root cause is fixed to re-engage the pending path.",
        f"{_KEY}: '{until.isoformat()}'",
    ]
    if reason:
        # Escape any single quotes in reason
        safe = reason.replace("'", "''")
        block_lines.append(f"{_REASON_KEY}: '{safe}'")

    text += "\n".join(block_lines) + "\n"

    # P1: write atomically. Without this, two concurrent auto-fallback
    # invocations (e.g. AdHocIngestion + DailyIngestion finishing at the
    # same moment) could interleave bytes inside ``.publish-config.yml``
    # and produce an unparsable file. ``os.replace`` is atomic on the
    # same filesystem and replaces the file in a single step.
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
    write_fallback(args.config, until=new_until, reason=args.reason)
    print(
        f"pending_mode_disabled_until set to {new_until.isoformat()} "
        f"(reason: {args.reason or '<none>'})",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
