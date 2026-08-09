"""Manage content filter rules."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date

from javdb.infra.logging import setup_logging
from javdb.storage import db as _db
from javdb.storage.contract import fragments as _contract
from javdb.storage.db import get_db
from javdb.storage.repos.content_filter_repo import ContentFilterRepo


DIMENSIONS = ("actor", "tag", "gender", "age", "release_date")
MODES = (
    "exclude", "include", "require_lead", "exclude_all_male", "min_age", "max_age",
    "regex_exclude", "regex_include", "before", "after",
)
# (dimension:mode) allow-list — single source of truth is the ADR-055 contract
# registry (javdb/storage/contract/fragments.py CONSTANTS), mirrored to the TS
# Worker. Encoded as "dim:mode" strings so both backends share one representation.
VALID_RULE_MODES = frozenset(_contract.VALID_RULE_MODES.values)
VALUE_REQUIRED = frozenset(_contract.VALUE_REQUIRED.values)
GENDER_VALUES = ("female", "male")

_MAX_REGEX_LEN = 200
# Heuristic ReDoS guard at the write boundary, where the ingestion engine's
# matcher has no execution timeout. Two classic catastrophic-backtracking
# shapes are rejected (not exhaustive, but covers the common risky patterns;
# may flag a few benign ones such as (http|https)+ — a deliberate trade-off):
#   1. A quantified group whose body holds an unbounded quantifier, e.g.
#      (a+)+, (a*)*, (.*)+, (a{2,}){3,} — _NESTED_QUANTIFIER_RE.
#   2. A quantified group whose body holds an alternation, e.g. (a|a)+,
#      (.|.)+, ([ab]|[cd])+, (x|y)*, (a|a){2,} — _QUANTIFIED_ALTERNATION_RE.
#      Overlapping alternatives under a quantifier backtrack exponentially and
#      the nested check above does not catch them.
# "Unbounded quantifier" means *, +, OR an open-ended interval {n,} — the
# interval form backtracks just as catastrophically, so it must not bypass the
# guard (it previously did, since both regexes only looked for * / +).
# Both are shared by the CLI and the API router (mirrored in the TS Worker).
_UNBOUNDED_QUANTIFIER = r"(?:[*+]|\{\d+,\})"
_NESTED_QUANTIFIER_RE = re.compile(
    r"\([^()]*" + _UNBOUNDED_QUANTIFIER + r"[^()]*\)" + _UNBOUNDED_QUANTIFIER
)
_QUANTIFIED_ALTERNATION_RE = re.compile(
    r"\([^()]*\|[^()]*\)" + _UNBOUNDED_QUANTIFIER
)


def regex_write_risk(pattern: str) -> str | None:
    """Return an error message if a regex ``pattern`` is too risky to store, else None."""
    if len(pattern) > _MAX_REGEX_LEN:
        return f"regex pattern too long (max {_MAX_REGEX_LEN} characters)"
    if _NESTED_QUANTIFIER_RE.search(pattern):
        return (
            "regex pattern has nested quantifiers (catastrophic-backtracking risk); "
            "rewrite it without a quantified group inside another quantifier"
        )
    if _QUANTIFIED_ALTERNATION_RE.search(pattern):
        return (
            "regex pattern has a quantified alternation (catastrophic-backtracking "
            "risk); rewrite it without a quantifier applied to a group that "
            "contains an alternation (|)"
        )
    return None


def validate_rule_value(dimension: str, mode: str, value: str) -> str:
    """Validate + normalize a content-filter rule value; raise ``ValueError(message)``
    on bad input, return the normalized value otherwise.

    Single Python source of truth shared by the CLI (``_validate_add``) and the API
    router (``apps/api/routers/content_filter.py``) so the web CRUD enforces the
    same gender/age/release_date/value-required/ReDoS rules the CLI does (the TS
    Worker mirrors this — it cannot import Python — alongside the allow-list).

    NOTE: regex *compile* validation is intentionally NOT done here — Python ``re``
    and JS ``new RegExp`` dialects diverge (inline flags like ``(?i)`` throw in JS),
    so a shared compile check is impossible cross-backend. The CLI compiles
    separately; only the dialect-independent ReDoS heuristic above is shared.
    """
    value = (value or "").strip()
    rule_key = f"{dimension}:{mode}"
    if rule_key in VALUE_REQUIRED and not value:
        raise ValueError(f"{dimension} {mode} rules require a non-empty value")
    if rule_key == "gender:require_lead":
        normalized = value.casefold()
        if normalized not in GENDER_VALUES:
            raise ValueError(
                f"gender require_lead rules require a value of {GENDER_VALUES}"
            )
        return normalized
    if rule_key == "gender:exclude_all_male":
        if value:
            raise ValueError("gender exclude_all_male rules do not accept a value")
        return ""
    if dimension == "age":
        if not value.isdigit():
            raise ValueError("age rules require a non-negative integer value")
        return str(int(value))
    if dimension == "release_date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("release_date rules require an ISO date (YYYY-MM-DD)")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("release_date rules require an ISO date (YYYY-MM-DD)")
    if mode in ("regex_exclude", "regex_include"):
        risk = regex_write_risk(value)
        if risk:
            raise ValueError(risk)
        return value
    return value


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
    rule_key = f"{args.dimension}:{args.mode}"
    if rule_key not in VALID_RULE_MODES:
        parser.error(
            f"{args.dimension} rules do not support mode {args.mode!r}"
        )
    try:
        normalized = validate_rule_value(args.dimension, args.mode, args.value)
    except ValueError as exc:
        parser.error(str(exc))
    # The CLI additionally compile-validates regex (Python-side only; the
    # dual-backend web boundary cannot replicate the Python `re` dialect).
    if args.mode in ("regex_exclude", "regex_include"):
        try:
            re.compile(normalized)
        except re.error as exc:
            parser.error(f"--value must be a valid regular expression: {exc}")
    args.value = normalized


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
