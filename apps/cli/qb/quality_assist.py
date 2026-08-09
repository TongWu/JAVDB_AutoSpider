from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)

from javdb.infra.config import cfg


def _evidence_enabled() -> bool:
    return bool(cfg("TORRENT_QUALITY_EVIDENCE_ENABLED", False))


def _assist_mode() -> bool:
    return cfg("TORRENT_QUALITY_POLICY_MODE", "shadow") == "assist"


def _probe_enabled() -> bool:
    # Assist ranks production against probe runner-up evidence, which only exists
    # when probing is enabled — keep the gate aligned with that prerequisite and
    # with config.py.example / the QBFileFilter workflow gate.
    return bool(cfg("QUALITY_PROBE_ENABLED", False))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Assist evaluator: rank probe vs production evidence (ADR-024)"
    )
    parser.add_argument(
        "--days", type=int, default=2,
        help="Look-back window in days for recent evaluations to re-rank",
    )
    parser.add_argument("--force", action="store_true", help="Run even when gates are off")
    return parser.parse_args(argv)


def run_assist(*, days=2):
    """Production wiring: gather recent evidence, rank, upsert assist evaluations.

    Assist reads/writes D1 only — no qBittorrent or proxy involvement — so it
    takes no proxy override.
    """
    from javdb.quality.assist_evaluator import run_assist as _run_assist

    return _run_assist(days=days)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.force and not (_evidence_enabled() and _assist_mode() and _probe_enabled()):
        print(
            "quality_assist disabled (needs TORRENT_QUALITY_EVIDENCE_ENABLED, "
            "TORRENT_QUALITY_POLICY_MODE=assist, and QUALITY_PROBE_ENABLED); skipping."
        )
        return 0
    summary = run_assist(days=args.days)
    print(
        "quality_assist summary: "
        f"movies={summary['movies']} candidates={summary['candidates']} "
        f"would_replace={summary['would_replace']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
