"""Centralised config accessor with per-variable fallback.

Every consumer imports the single :func:`cfg` helper instead of scattering
``try: from config import … except ImportError: …`` blocks across the
codebase.  Each variable is resolved independently so that a missing *new*
variable never causes an already-configured variable to silently fall back
to its hardcoded default.
"""

try:
    import config as _config_module
except ModuleNotFoundError as exc:
    if exc.name == 'config':
        _config_module = None
    else:
        raise


def cfg(name, default):
    """Return *config.<name>* if available, otherwise *default*."""
    if _config_module is None:
        return default
    return getattr(_config_module, name, default)


# ── Storage-mode helpers ──────────────────────────────────────────────────

from typing import Optional

_storage_mode_override: Optional[str] = None


# ── DB-write kill switch ──────────────────────────────────────────────────
#
# ``JAVDB_FORBID_DB_WRITES=1`` is a one-way override intended for
# TestIngestion / smoke-test workflows that exercise the spider on every
# code change but **must not** mutate D1 or any persisted SQLite database.
# When set:
#   * :func:`storage_backend` is forced to ``'sqlite'`` (never returns
#     ``d1`` / ``dual``), so no D1 client is ever constructed.
#   * :func:`storage_mode` is forced to ``'csv'`` so ``use_db_storage()``
#     returns ``False`` and the spider skips ``db_create_report_session``
#     entirely.  CSV writes still happen so the test can verify spider
#     output.
#   * Lower-level entry points (``init_db``, ``db_create_report_session``,
#     ``DualConnection``) raise ``RuntimeError`` defensively if anything
#     ever tries to take the DB-write path while the switch is engaged.
#
# Returning a bool (rather than reading the env var ad hoc) gives callers
# a single, testable contract.
def db_writes_forbidden() -> bool:
    """True when the ``JAVDB_FORBID_DB_WRITES`` kill switch is engaged."""
    import os
    val = os.environ.get('JAVDB_FORBID_DB_WRITES', '')
    if isinstance(val, str):
        val = val.strip().lower()
    return val in ('1', 'true', 'yes', 'on')


def force_storage_mode(mode: str) -> None:
    """Override storage mode for the rest of the process lifetime.

    Called automatically when the SQLite database file is detected as
    invalid (e.g. a Git LFS pointer that wasn't pulled).  Subsequent
    calls to :func:`use_sqlite` / :func:`use_csv` reflect the override.
    """
    global _storage_mode_override
    _storage_mode_override = mode


def storage_mode() -> str:
    """Return the active storage mode: ``'db'``, ``'csv'``, or ``'duo'``.

    Resolution order: runtime override → config module →
    ``VAR_STORAGE_MODE`` env var → ``'duo'``.
    The env-var fallback allows workflows that skip ``config_generator.py``
    (e.g. RcloneManager) to still control the mode.  Defaults to ``'duo'``
    so that both SQLite and CSV outputs are produced — the uploader path
    requires the spider CSV as input.
    """
    if db_writes_forbidden():
        # Kill switch overrides everything — TestIngestion must produce
        # CSVs only, never DB rows.
        return 'csv'
    if _storage_mode_override is not None:
        return _storage_mode_override
    import os
    mode = cfg('STORAGE_MODE', None)
    if mode is None:
        mode = os.environ.get('VAR_STORAGE_MODE', 'duo')
    if isinstance(mode, str):
        mode = mode.strip().lower()
    if mode not in ('db', 'csv', 'duo'):
        mode = 'duo'
    return mode


def use_sqlite() -> bool:
    """True when SQLite writes/reads are needed (``db`` or ``duo``)."""
    return storage_mode() in ('db', 'duo')


def use_csv() -> bool:
    """True when CSV writes/reads are needed (``csv`` or ``duo``)."""
    return storage_mode() in ('csv', 'duo')


def storage_backend() -> str:
    """Return the DB backend configured for platform DB connections."""
    if db_writes_forbidden():
        # Kill switch — never construct a D1 client, regardless of vars.
        return 'sqlite'
    import os
    backend = (
        os.environ.get('_STORAGE_BACKEND_INIT_OVERRIDE')
        or os.environ.get('STORAGE_BACKEND')
        or cfg('STORAGE_BACKEND', None)
    )
    if isinstance(backend, str):
        backend = backend.strip().lower()
    if backend in ('d1', 'dual'):
        return backend
    return 'sqlite'


def use_db_storage() -> bool:
    """True when any DB-backed storage path is enabled."""
    return use_sqlite() or storage_backend() in ('d1', 'dual')
