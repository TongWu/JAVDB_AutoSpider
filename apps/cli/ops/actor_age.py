"""Inspect / refresh the ActorMetadata age cache (ADR-040 Phase 2).

  list                          show cached actor rows (age shown as current age)
  refresh --href <p> --name <n> force a re-lookup (overwrites the negative cache)
  clear  --href <p>             delete a cached row
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from javdb.infra.logging import setup_logging
from javdb.parsing.common import normalize_javdb_href_path
from javdb.spider.services.actor_age import build_default_resolver, compute_age
from javdb.storage.db import HISTORY_DB_PATH, get_db
from javdb.storage.repos.actor_metadata_repo import ActorMetadataRepo


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.actor_age",
        description="Inspect / refresh the ActorMetadata age cache.",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List cached actor rows.")

    refresh = sub.add_parser("refresh", help="Force a re-lookup for one actor.")
    refresh.add_argument("--href", required=True, help="javdb /actors/<id> path")
    refresh.add_argument("--name", required=True, help="actor name to search by")

    clear = sub.add_parser("clear", help="Delete a cached actor row.")
    clear.add_argument("--href", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    if args.command == "list":
        with get_db(HISTORY_DB_PATH) as conn:
            rows = ActorMetadataRepo(conn).list_all()
        if not rows:
            print("No cached actor metadata.")
            return 0
        today = date.today()
        print("actor_href\tbirthdate\tage_now\tsource")
        for r in rows:
            bd = r.get("birthdate")
            age = compute_age(bd, today) if bd else None
            print(f"{r['actor_href']}\t{bd or '-'}\t{age if age is not None else '-'}\t{r.get('source') or '-'}")
        return 0

    if args.command == "clear":
        href = normalize_javdb_href_path(args.href)
        if not href or not href.startswith("/actors/"):
            print("Invalid --href: expected /actors/<id>.", file=sys.stderr)
            return 2
        with get_db(HISTORY_DB_PATH) as conn:
            ActorMetadataRepo(conn).delete(href)
        print(f"Cleared cache for {href}.")
        return 0

    if args.command == "refresh":
        href = normalize_javdb_href_path(args.href)
        if not href or not href.startswith("/actors/"):
            print("Invalid --href: expected /actors/<id>.", file=sys.stderr)
            return 2
        # Force a fresh lookup: clear, then resolve via the real source chain.
        with get_db(HISTORY_DB_PATH) as conn:
            ActorMetadataRepo(conn).delete(href)
        resolver = build_default_resolver()

        class _OneActor:
            actors = [type("A", (), {"name": args.name, "href": href})()]
            release_date = ""  # CLI debug → current-age reference

        ages = resolver.ages_for(_OneActor())
        if href in ages:
            print(f"Resolved {args.name}: age {ages[href]} (as of today).")
        else:
            print(f"No birthdate found for {args.name} (cached as unknown).")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
