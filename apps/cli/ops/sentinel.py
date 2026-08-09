# apps/cli/ops/sentinel.py
"""Evaluate parse field-health for site-contract drift (ADR-035).

Three modes:
  * --session-id S   evaluate a daily run's persisted fills (Phase 1; the gate).
  * --canary         run the independent canary over pinned pages (Phase 2).
  * --capture-anchors --url U [--url U2 ...]
                     fetch each URL, print current parsed anchor values as JSON
                     (to populate SENTINEL_CANARY_ANCHORS).

Read-only by default; exit code 4 signals critical drift so a workflow can act."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from javdb.infra.logging import setup_logging
from javdb.ops.sentinel.service import CanaryError, evaluate_session, run_canary

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.sentinel",
        description="Evaluate parse field-health for site-contract drift.",
    )
    p.add_argument("--session-id", default=None,
                   help="Evaluate this run's persisted fills (Phase 1 gate mode).")
    p.add_argument("--canary", action="store_true",
                   help="Run the independent canary over the pinned pages (Phase 2).")
    p.add_argument("--capture-anchors", action="store_true",
                   help="Fetch --url pages and print current parsed anchor values as JSON.")
    p.add_argument("--url", action="append", default=[], dest="urls",
                   help="Detail-page URL to capture (repeatable; with --capture-anchors).")
    p.add_argument("--run-id", default=None)
    p.add_argument("--attempt", type=int, default=None, dest="run_attempt")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def _print_verdict(verdict, json_output: bool) -> None:
    if json_output:
        print(json.dumps({
            "critical": verdict.critical,
            "evaluated": verdict.evaluated,
            "findings": [asdict(f) for f in verdict.findings],
        }, ensure_ascii=False))
    else:
        logger.info("Sentinel: critical=%s evaluated=%d findings=%d",
                    verdict.critical, verdict.evaluated, len(verdict.findings))


def _capture_anchors(urls: list[str]) -> int:
    """Fetch each detail URL and print its current parsed anchor values."""
    from javdb.ops.sentinel.service import _canary_use_proxy
    from javdb.parsing import parse_detail_page
    from javdb.spider.spider_gateway import create_gateway

    if not urls:
        print("ERROR: --capture-anchors requires at least one --url.", file=sys.stderr)
        return 2
    # Honour PROXY_MODULES (same javdb fetch policy as the canary), not hard-coded.
    gw = create_gateway(
        use_proxy=_canary_use_proxy(), use_cf_bypass=True, use_cookie=True)
    captured = []
    for url in urls:
        html = gw.fetch_html(url)
        if not html:
            logger.warning("capture-anchors: fetch failed: %s", url)
            captured.append({"url": url, "error": "fetch_failed"})
            continue
        d = parse_detail_page(html)
        captured.append({
            "url": url,
            "video_code": getattr(d, "video_code", ""),
            "title_contains": (getattr(d, "title", "") or "")[:24],
            "min_magnets": len(getattr(d, "magnets", []) or []),
        })
    print(json.dumps(captured, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    if args.capture_anchors:
        return _capture_anchors(args.urls)

    try:
        if args.canary:
            verdict = run_canary(run_id=args.run_id, run_attempt=args.run_attempt)
        else:
            if not args.session_id:
                print("ERROR: provide --session-id (gate mode) or --canary.",
                      file=sys.stderr)
                return 2
            verdict = evaluate_session(
                args.session_id, run_id=args.run_id, run_attempt=args.run_attempt)
    except CanaryError:
        # The canary could not do its job (fetched nothing, or drift could not be
        # persisted). Exit 3 — distinct from clean (0), recorded drift (4), and an
        # unexpected internal error (1) — so the workflow fails and alerts.
        logger.error("Canary could not complete; failing the run so it is not "
                     "treated as clean", exc_info=True)
        return 3
    except Exception:
        logger.exception("Sentinel evaluation failed")
        return 1

    _print_verdict(verdict, args.json_output)
    return 4 if verdict.critical else 0


if __name__ == "__main__":
    sys.exit(main())
