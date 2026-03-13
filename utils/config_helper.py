"""Centralised config accessor with per-variable fallback.

Every consumer imports the single :func:`cfg` helper instead of scattering
``try: from config import … except ImportError: …`` blocks across the
codebase.  Each variable is resolved independently so that a missing *new*
variable never causes an already-configured variable to silently fall back
to its hardcoded default.
"""

try:
    import config as _config_module
except ImportError:
    _config_module = None


def cfg(name, default):
    """Return *config.<name>* if available, otherwise *default*."""
    if _config_module is None:
        return default
    return getattr(_config_module, name, default)


# ── Storage-mode helpers ──────────────────────────────────────────────────

_storage_mode_override: str | None = None


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
    ``VAR_STORAGE_MODE`` env var → ``'db'``.
    The env-var fallback allows workflows that skip ``config_generator.py``
    (e.g. RcloneInventory) to still control the mode.
    """
    if _storage_mode_override is not None:
        return _storage_mode_override
    import os
    mode = cfg('STORAGE_MODE', None)
    if mode is None:
        mode = os.environ.get('VAR_STORAGE_MODE', 'db')
    if isinstance(mode, str):
        mode = mode.strip().lower()
    if mode not in ('db', 'csv', 'duo'):
        mode = 'db'
    return mode


def use_sqlite() -> bool:
    """True when SQLite writes/reads are needed (``db`` or ``duo``)."""
    return storage_mode() in ('db', 'duo')


def use_csv() -> bool:
    """True when CSV writes/reads are needed (``csv`` or ``duo``)."""
    return storage_mode() in ('csv', 'duo')
