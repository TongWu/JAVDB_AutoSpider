from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)

from javdb.infra.config import cfg
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override


def _evidence_enabled() -> bool:
    return bool(cfg("TORRENT_QUALITY_EVIDENCE_ENABLED", False))


def _probe_enabled() -> bool:
    return bool(cfg("QUALITY_PROBE_ENABLED", False))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Probe runner-up magnets on the remote quality_probe qB (ADR-024)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max candidates to probe this run")
    parser.add_argument("--force", action="store_true", help="Run even when gates are off")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qB API requests",
        no_help="Force-disable proxy for qB API requests",
    )
    return parser.parse_args(argv)


def run_probe(*, limit=None, use_proxy=None):
    """Production wiring: build the probe client, run the queue, write evidence."""
    import time
    from datetime import datetime, timezone

    from javdb.integrations.qb.file_filter import service as ff
    from javdb.quality.probe_client import build_probe_client
    from javdb.quality.probe_runner import probe_candidates
    from javdb.storage.db import REPORTS_DB_PATH, get_db
    from javdb.storage.repos.torrent_probe_repo import TorrentProbeRepo
    from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

    # Honour the tri-state proxy override (True/False/None auto): pass it through
    # unchanged and wire the qB proxies helper so auto-mode defers to PROXY_MODULES.
    ff.initialize_proxy_helper(use_proxy)
    client = build_probe_client(
        use_proxy=use_proxy,
        proxies_getter=lambda up: ff.get_proxies_dict("qbittorrent", up),
    )
    if client is None:
        print("quality_probe endpoint unavailable; skipping.")
        return {"scanned": 0, "probed": 0, "timeout": 0,
                "capability_unsupported": 0, "errors": 0}

    now = datetime.now(timezone.utc).isoformat()  # CLI boundary — plain UTC stamp
    with get_db(REPORTS_DB_PATH) as conn:
        return probe_candidates(
            client=client,
            queue_repo=TorrentProbeRepo(conn),
            evidence_repo=TorrentQualityRepo(conn),
            now=now,
            poll=lambda: time.sleep(2),
            limit=limit,
        )


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.force and not (_evidence_enabled() and _probe_enabled()):
        print(
            "quality_probe disabled "
            "(needs TORRENT_QUALITY_EVIDENCE_ENABLED and QUALITY_PROBE_ENABLED); skipping."
        )
        return 0
    summary = run_probe(
        limit=args.limit,
        use_proxy=resolve_proxy_override(args.use_proxy, args.no_proxy),
    )
    print(
        "quality_probe summary: "
        f"scanned={summary['scanned']} probed={summary['probed']} "
        f"timeout={summary['timeout']} "
        f"capability_unsupported={summary['capability_unsupported']} "
        f"errors={summary.get('errors', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
