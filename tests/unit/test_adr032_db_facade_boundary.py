"""ADR-032 boundary guard: the Repo is the single caller-facing storage interface.

This test enforces the ADR-032 invariant that the ``Repo`` classes under
``javdb/storage/repos/`` (plus the session/rollback helpers that compose them)
are the *only* caller-facing storage interface. Production code outside the
storage-db package must reach the ``db_*`` primitives through a Repo, never by
importing ``db_*`` functions directly from the ``javdb.storage.db`` facade.

The facade still re-exports the ``db_*`` names (``__init__.__all__`` is intact)
so migration tools, tests, and implementation fall-backs keep working. This test
guards the *caller* boundary: any non-exempt production module that imports a
``db_*`` name from ``javdb.storage.db`` is a regression and fails here.

Exemptions (D6):
  * ``javdb/storage/db/``  — the facade and its ``_db_*`` implementation modules.
  * ``javdb/migrations/``  — migration tools are explicitly allowed to keep
    facade imports.

A flagged violation is reported as ``<file>: <name>`` so future regressions are
immediately actionable: repoint the import to the owning ``_db_*`` submodule.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Repo root = three levels up from this file (tests/unit/<file>).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Production roots to scan.
_SCAN_ROOTS = (_REPO_ROOT / "javdb", _REPO_ROOT / "apps")

# Directories whose files are exempt from the boundary rule (D6).
_EXEMPT_DIRS = (
    _REPO_ROOT / "javdb" / "storage" / "db",
    _REPO_ROOT / "javdb" / "migrations",
)

_FACADE_MODULE = "javdb.storage.db"


def _is_exempt(path: Path) -> bool:
    """True if *path* lives under an exempt directory."""
    for exempt in _EXEMPT_DIRS:
        try:
            path.relative_to(exempt)
            return True
        except ValueError:
            continue
    return False


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if _is_exempt(path):
                continue
            files.append(path)
    return files


def _facade_db_imports(path: Path) -> list[str]:
    """Return ``db_*`` names imported from the ``javdb.storage.db`` facade.

    Uses ``ast`` (not regex) so multi-line ``from ... import (a, b, c)`` blocks
    and aliased ``import ... as`` forms are handled correctly. Only the exact
    facade module is flagged; imports from ``javdb.storage.db._db_*`` submodules
    are *not* flagged (those are the sanctioned implementation imports).
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    offending: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # node.module is the dotted path after "from"; level==0 for absolute.
        if node.level != 0 or node.module != _FACADE_MODULE:
            continue
        for alias in node.names:
            if alias.name.startswith("db_"):
                offending.append(alias.name)
    return offending


def test_no_production_code_imports_db_star_from_facade():
    violations: list[tuple[str, str]] = []
    for path in _iter_python_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for name in _facade_db_imports(path):
            violations.append((rel, name))

    assert not violations, (
        "ADR-032 boundary violation: production code must reach db_* primitives "
        "through a Repo, not the javdb.storage.db facade. Repoint each import to "
        "the owning javdb.storage.db._db_* submodule.\n"
        + "\n".join(f"  {file}: {name}" for file, name in sorted(violations))
    )
