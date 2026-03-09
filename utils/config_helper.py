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
