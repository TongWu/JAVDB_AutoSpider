#!/usr/bin/env python3
"""Validate the D1 ``Write-Class:`` header on newly-added migration files.

ADR-042 (D6) requires every new D1 *write surface* to declare its write class:
``authoritative``, ``additive``, or ``diagnostic``. A new write surface is
introduced precisely when a migration runs ``CREATE TABLE`` -- column adds
inherit the parent table's class, while indexes / version bumps / drops are not
write surfaces. This gate fails a PR when a newly-added
``javdb/migrations/d1/*.sql`` file contains ``CREATE TABLE`` but lacks a valid
``-- Write-Class: <class>`` header line.

The validator is intentionally dependency-free (mirrors ``select_tests.py``):
the core :func:`find_violations` is a pure function over ``(path, content)``
pairs, so it is unit-testable without git. The CLI is a thin wrapper that
resolves the set of added migration files (via ``git diff`` or an explicit
``--paths`` list), reads them, and emits ``::error::`` annotations for GitHub
Actions. ``n/a`` is an ADR-template-only value and is rejected for a concrete
migration that creates a table.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = "javdb/migrations/d1"
VALID_CLASSES = ("authoritative", "additive", "diagnostic")

# A write surface is introduced by CREATE TABLE (optionally IF NOT EXISTS).
_CREATE_TABLE_RE = re.compile(r"\bcreate\s+table\b", re.IGNORECASE)
# Header line: ``-- Write-Class: additive`` (case-insensitive keyword + value).
_WRITE_CLASS_RE = re.compile(
    r"^\s*--\s*write-class\s*:\s*([A-Za-z/]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class Violation:
    path: str
    message: str


def introduces_write_surface(content: str) -> bool:
    """True when the migration creates a new table (a new write surface)."""
    return bool(_CREATE_TABLE_RE.search(content))


def declared_classes(content: str) -> list[str]:
    """All ``Write-Class:`` values declared in the file, lower-cased."""
    return [m.group(1).lower() for m in _WRITE_CLASS_RE.finditer(content)]


def find_violations(files: list[tuple[str, str]]) -> list[Violation]:
    """Pure core: return ADR-042 D6 violations for the given (path, content) pairs.

    Only ``.sql`` files that contain ``CREATE TABLE`` are checked. Such a file
    must declare at least one valid ``Write-Class:`` (authoritative / additive /
    diagnostic). ``n/a`` is rejected on purpose: it is an ADR-template-only
    value, never valid for a concrete migration that creates a table.
    """
    violations: list[Violation] = []
    for path, content in files:
        if not path.endswith(".sql"):
            continue
        if not introduces_write_surface(content):
            continue
        classes = declared_classes(content)
        if not classes:
            violations.append(
                Violation(
                    path,
                    "migration creates a table but declares no `-- Write-Class:` "
                    f"header (ADR-042 D6). Add one of: {', '.join(VALID_CLASSES)}.",
                )
            )
            continue
        invalid = sorted({c for c in classes if c not in VALID_CLASSES})
        if invalid:
            violations.append(
                Violation(
                    path,
                    f"invalid Write-Class value(s) {invalid}; allowed: "
                    f"{', '.join(VALID_CLASSES)} (n/a is ADR-template-only).",
                )
            )
    return violations


def _git_added_sql(base: str) -> list[str]:
    """Paths of ``.sql`` files added under the migrations dir vs. ``base``."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", base, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    prefix = MIGRATIONS_DIR + "/"
    return [ln for ln in out.splitlines() if ln.startswith(prefix) and ln.endswith(".sql")]


def _read_files(paths: list[str]) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for p in paths:
        try:
            files.append((p, Path(p).read_text(encoding="utf-8")))
        except OSError as exc:  # pragma: no cover - defensive
            print(f"::warning file={p}::could not read migration: {exc}", file=sys.stderr)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--base",
        help="git base ref; validate .sql files added under the migrations dir since this ref",
    )
    group.add_argument(
        "--paths",
        nargs="*",
        help="explicit migration files to validate (local / testing)",
    )
    args = parser.parse_args(argv)

    if args.paths is not None:
        paths = args.paths
    elif args.base:
        paths = _git_added_sql(args.base)
    else:
        parser.error("one of --base or --paths is required")

    if not paths:
        print("No newly-added D1 migrations to validate.")
        return 0

    violations = find_violations(_read_files(paths))
    if not violations:
        print(f"OK: {len(paths)} migration(s) carry a valid Write-Class header.")
        return 0

    for v in violations:
        print(f"::error file={v.path}::{v.message}")
    print(
        f"\n{len(violations)} migration(s) violate ADR-042 D6. See "
        "javdb/migrations/README.md and ADR-042 for the Write-Class convention.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
