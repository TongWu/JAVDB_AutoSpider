"""Pure rclone remote/root path helpers."""

from javdb.infra.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# rclone config helper
# ============================================================================

def has_remote_prefix(path: str) -> bool:
    """True when *path* has a leading rclone remote (first ``:`` before first ``/``).

    Examples: ``remote:rest``, ``a:b`` (no slash) → True; ``dir/file:name`` → False.
    """
    if not path or ':' not in path:
        return False
    ci = path.index(':')
    si = path.find('/')
    if si == -1:
        return True
    return ci < si


def strip_drive_name(path: str) -> str:
    """Remove rclone drive name prefix (e.g. ``'gdrive:path'`` → ``'path'``)."""
    if has_remote_prefix(path):
        return path.split(':', 1)[1]
    return path


def get_configured_drive_name() -> str:
    """Read the rclone drive name from ``RCLONE_FOLDER_PATH`` config."""
    from javdb.infra.config import cfg as _cfg
    path = _cfg('RCLONE_FOLDER_PATH', None)
    if path and has_remote_prefix(str(path)):
        return str(path).split(':', 1)[0].strip()
    drive = _cfg('RCLONE_DRIVE_NAME', None)
    if drive:
        return str(drive).strip()
    return ''


def get_configured_root_folder() -> str:
    """Return the configured root folder path (without drive prefix).

    Priority:
    1) ``RCLONE_FOLDER_PATH`` (e.g. ``gdrive:/剧集/不可以色色/JAV-Sync``)
    2) ``RCLONE_ROOT_FOLDER`` (e.g. ``/剧集/不可以色色/JAV-Sync``)

    The returned value is normalised: no leading/trailing ``/``.
    """
    from javdb.infra.config import cfg as _cfg

    folder_path = _cfg('RCLONE_FOLDER_PATH', None)
    if folder_path and has_remote_prefix(str(folder_path)):
        # keep only the path part (after ':') and normalise slashes
        raw = str(folder_path).split(':', 1)[1]
        return str(raw).strip().strip('/')

    root = _cfg('RCLONE_ROOT_FOLDER', None)
    if root is None:
        return ''
    return str(root).strip().strip('/')


def strip_root_folder(path: str, root: str = '') -> str:
    """Strip the configured root folder prefix from *path* and return a relative path.

    - Accepts paths with or without a drive prefix (``gdrive:...``).
    - Accepts paths with or without a leading ``/``.
    - Idempotent: already-relative paths are returned unchanged.
    """
    if not path:
        return ''
    root_norm = (root or get_configured_root_folder()).strip().strip('/')
    raw = strip_drive_name(path).strip()
    if not raw:
        return ''
    raw = raw.lstrip('/')
    if not root_norm:
        return raw
    if raw == root_norm:
        return ''
    prefix = root_norm + '/'
    if raw.startswith(prefix):
        return raw[len(prefix):]
    return raw


def prepend_root_folder(rel_path: str, root: str = '') -> str:
    """Prepend root folder to a relative path.

    If *rel_path* already contains a drive prefix or already starts with the root
    folder, it is returned unchanged.
    """
    if rel_path is None:
        return ''
    p = str(rel_path).strip()
    if not p:
        return ''
    if has_remote_prefix(p):
        return p
    root_norm = (root or get_configured_root_folder()).strip().strip('/')
    if not root_norm:
        return p.lstrip('/')
    p2 = p.lstrip('/')
    if p2 == root_norm or p2.startswith(root_norm + '/'):
        return p2
    return f"{root_norm}/{p2}"


def to_full_remote_path(rel_path: str, drive: str = '', root: str = '') -> str:
    """Build a full rclone remote path from a stored relative path."""
    with_root = prepend_root_folder(rel_path, root=root)
    return prepend_drive_name(with_root, drive_name=drive)


def prepend_drive_name(folder_path: str, drive_name: str = '') -> str:
    """Prepend rclone drive name to a relative folder path.

    If *folder_path* already has a leading remote prefix it is returned as-is.
    When *drive_name* is not given, :func:`get_configured_drive_name` is used.
    """
    if has_remote_prefix(folder_path):
        return folder_path
    dn = drive_name or get_configured_drive_name()
    if not dn:
        return folder_path
    return f"{dn}:{folder_path}"
