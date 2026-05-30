"""Manage content filter rules."""

from __future__ import annotations

import argparse
import sys

from javdb.infra.logging import setup_logging
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.content_filter_repo import ContentFilterRepo


DIMENSIONS = ("actor", "tag", "gender")
MODES = ("exclude", "include", "require_lead", "exclude_all_male")
VALID_RULE_MODES = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
    ("gender", "exclude_all_male"),
}
VALUE_REQUIRED = {
    ("actor", "exclude"),
    ("tag", "exclude"),
    ("tag", "include"),
    ("gender", "require_lead"),
}
GENDER_VALUES = ("female", "male")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.content_filter",
        description="Manage content filter rules.",
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))

    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add a content filter rule.")
    add.add_argument("--dimension", required=True, choices=DIMENSIONS)
    add.add_argument("--mode", required=True, choices=MODES)
    add.add_argument("--value", default="")

    subparsers.add_parser("list", help="List content filter rules.")

    remove = subparsers.add_parser("remove", help="Remove a content filter rule.")
    remove.add_argument("--id", type=int, required=True)

    enable = subparsers.add_parser("enable", help="Enable or disable a content filter rule.")
    enable.add_argument("--id", type=int, required=True)
    enable.add_argument("--off", action="store_true", help="Disable the rule instead of enabling it.")

    return parser


def _print_rules(repo: ContentFilterRepo) -> None:
    rules = repo.list_rules()
    if not rules:
        print("No content filter rules.")
        return

    print("id\tdimension\tmode\tvalue\tenabled")
    for rule in rules:
        print(
            f"{rule.id}\t{rule.dimension}\t{rule.mode}\t{rule.value}\t"
            f"{'yes' if rule.enabled else 'no'}"
        )


def _validate_add(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    rule_key = (args.dimension, args.mode)
    value = (args.value or "").strip()
    if rule_key not in VALID_RULE_MODES:
        parser.error(
            f"{args.dimension} rules do not support mode {args.mode!r}"
        )
    if rule_key in VALUE_REQUIRED and not value:
        parser.error(f"{args.dimension} {args.mode} rules require --value")
    if rule_key == ("gender", "require_lead"):
        normalized = value.casefold()
        if normalized not in GENDER_VALUES:
            parser.error(
                "gender require_lead rules require --value "
                f"to be one of {GENDER_VALUES}"
            )
        args.value = normalized
    elif rule_key == ("gender", "exclude_all_male"):
        if value:
            parser.error("gender exclude_all_male rules do not accept --value")
        args.value = ""
    else:
        args.value = value


def _rule_exists(repo: ContentFilterRepo, rule_id: int) -> bool:
    return any(rule.id == rule_id for rule in repo.list_rules())


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "add":
        _validate_add(args, parser)

    setup_logging(log_level=args.log_level)

    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        if args.command == "add":
            rule_id = repo.add_rule(args.dimension, args.mode, args.value)
            print(f"Added content filter rule {rule_id}.")
        elif args.command == "list":
            _print_rules(repo)
        elif args.command == "remove":
            if not _rule_exists(repo, args.id):
                print(f"Content filter rule {args.id} not found.", file=sys.stderr)
                return 1
            repo.remove_rule(args.id)
            print(f"Removed content filter rule {args.id}.")
        elif args.command == "enable":
            if not _rule_exists(repo, args.id):
                print(f"Content filter rule {args.id} not found.", file=sys.stderr)
                return 1
            enabled = not args.off
            repo.set_enabled(args.id, enabled)
            state = "Enabled" if enabled else "Disabled"
            print(f"{state} content filter rule {args.id}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
