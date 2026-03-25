"""Magnet link extraction and categorisation.

Tries to use the high-performance Rust implementation from
``javdb_rust_core.extract_magnets``.  Falls back to the pure-Python
implementation transparently when the Rust extension is unavailable.
"""

from utils.infra.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Try Rust implementation
# ---------------------------------------------------------------------------

try:
    from javdb_rust_core import extract_magnets as _rust_extract_magnets
    RUST_MAGNET_AVAILABLE = True
    logger.debug("✅ Rust magnet extractor available")
except ImportError:
    RUST_MAGNET_AVAILABLE = False
    logger.debug("⚠️  Rust magnet extractor not available, using Python fallback")


def extract_magnets(magnets, index=None):
    """Extract magnet links based on categories.

    Each magnet in *magnets* is a dict with keys:
    ``href``, ``name``, ``tags`` (list[str]), ``size``, ``timestamp``.

    Returns a dict with keys: ``subtitle``, ``hacked_subtitle``,
    ``hacked_no_subtitle``, ``no_subtitle`` (and their ``size_*`` counterparts).
    """
    if RUST_MAGNET_AVAILABLE:
        try:
            result = _rust_extract_magnets(magnets)
            prefix = f"[{index}]" if index is not None else ""
            if not any(result.get(k) for k in ('subtitle', 'hacked_subtitle', 'hacked_no_subtitle', 'no_subtitle')):
                logger.warning(f"{prefix} No suitable magnet found")
            return result
        except Exception as e:
            logger.debug(f"Rust extract_magnets failed ({e}), falling back to Python")

    return _python_extract_magnets(magnets, index)


# ---------------------------------------------------------------------------
# Pure-Python fallback
# ---------------------------------------------------------------------------

def infer_resolution(name, tags):
    """Infer video resolution from torrent tags and filename.

    Returns an int (720, 1080, 2560, 3840, 7680) or None.
    """
    tag_text = ' '.join(tags) if tags else ''
    if '8K' in tag_text:
        return 7680
    if '4K' in tag_text:
        return 3840
    if '2K' in tag_text:
        return 2560
    if '高清' in tag_text:
        return 1080

    if name:
        low = name.lower()
        if '8k' in low:
            return 7680
        if '4k' in low:
            return 3840
        if '2k' in low:
            return 2560
        if '1080p' in low or '1080' in low:
            return 1080
        if '720p' in low or '720' in low:
            return 720
    return None


def _parse_size(size_str):
    if not size_str:
        return 0
    try:
        s = size_str.strip().upper().replace(',', '')
        for suffix, multiplier in (('GB', 1024**3), ('MB', 1024**2), ('KB', 1024)):
            if suffix in s:
                return float(s.replace(suffix, '').strip()) * multiplier
        return 0
    except (ValueError, TypeError):
        return 0


def _sort_key(m):
    return (m.get('timestamp', ''), _parse_size(m.get('size', '')))


def _python_extract_magnets(magnets, index=None):
    result = {
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': '',
        'file_count_hacked_subtitle': 0,
        'file_count_hacked_no_subtitle': 0,
        'file_count_subtitle': 0,
        'file_count_no_subtitle': 0,
        'resolution_hacked_subtitle': None,
        'resolution_hacked_no_subtitle': None,
        'resolution_subtitle': None,
        'resolution_no_subtitle': None,
    }

    prefix = f"[{index}]" if index is not None else ""

    # --- subtitle ---
    subtitle_magnets = [
        m for m in magnets
        if any('字幕' in tag or 'Subtitle' in tag for tag in m['tags'])
        and '.无码破解' not in m['name']
    ]
    if subtitle_magnets:
        subtitle_magnets.sort(key=_sort_key, reverse=True)
        best = subtitle_magnets[0]
        result['subtitle'] = best['href']
        result['size_subtitle'] = best['size']
        result['file_count_subtitle'] = best.get('file_count', 0)
        result['resolution_subtitle'] = infer_resolution(best['name'], best.get('tags', []))
        logger.debug(f"{prefix} Found subtitle magnet: {best['name']} (size: {best['size']}, time: {best['timestamp']})")

    # --- hacked ---
    hacked_subtitle_magnets = []
    hacked_no_subtitle_magnets = []
    for m in magnets:
        name = m['name']
        if any(p in name for p in ('-UC', '-CU', '-C.无码破解', '-U-C', '-C-U')):
            hacked_subtitle_magnets.append(m)
        elif '-U' in name or '.无码破解' in name:
            hacked_no_subtitle_magnets.append(m)

    if hacked_subtitle_magnets:
        hacked_subtitle_magnets.sort(key=_sort_key, reverse=True)
        best = hacked_subtitle_magnets[0]
        result['hacked_subtitle'] = best['href']
        result['size_hacked_subtitle'] = best['size']
        result['file_count_hacked_subtitle'] = best.get('file_count', 0)
        result['resolution_hacked_subtitle'] = infer_resolution(best['name'], best.get('tags', []))
        logger.debug(f"{prefix} Found hacked_subtitle magnet: {best['name']} (size: {best['size']}, time: {best['timestamp']})")
    elif hacked_no_subtitle_magnets:
        hacked_no_subtitle_magnets.sort(key=_sort_key, reverse=True)
        best = hacked_no_subtitle_magnets[0]
        result['hacked_no_subtitle'] = best['href']
        result['size_hacked_no_subtitle'] = best['size']
        result['file_count_hacked_no_subtitle'] = best.get('file_count', 0)
        result['resolution_hacked_no_subtitle'] = infer_resolution(best['name'], best.get('tags', []))
        logger.debug(f"{prefix} Found hacked_no_subtitle magnet: {best['name']} (size: {best['size']}, time: {best['timestamp']})")

    # --- no_subtitle (prefer 4k) ---
    k4_magnets = []
    normal_magnets = []
    for m in magnets:
        name = m['name']
        is_subtitle = any('字幕' in tag for tag in m['tags']) and '.无码破解' not in name
        is_hacked = any(p in name for p in ('-UC', '-U', '.无码破解'))
        if not is_subtitle and not is_hacked:
            if '4k' in name.lower():
                k4_magnets.append(m)
            else:
                normal_magnets.append(m)

    if k4_magnets:
        k4_magnets.sort(key=_sort_key, reverse=True)
        best = k4_magnets[0]
        result['no_subtitle'] = best['href']
        result['size_no_subtitle'] = best['size']
        result['file_count_no_subtitle'] = best.get('file_count', 0)
        result['resolution_no_subtitle'] = infer_resolution(best['name'], best.get('tags', []))
        logger.debug(f"{prefix} Found 4K magnet for no_subtitle: {best['name']} (size: {best['size']}, time: {best['timestamp']})")
    elif normal_magnets:
        normal_magnets.sort(key=_sort_key, reverse=True)
        best = normal_magnets[0]
        result['no_subtitle'] = best['href']
        result['size_no_subtitle'] = best['size']
        result['file_count_no_subtitle'] = best.get('file_count', 0)
        result['resolution_no_subtitle'] = infer_resolution(best['name'], best.get('tags', []))
        logger.debug(f"{prefix} Found normal magnet for no_subtitle: {best['name']} (size: {best['size']}, time: {best['timestamp']})")

    if not any(result[k] for k in ('subtitle', 'hacked_subtitle', 'hacked_no_subtitle', 'no_subtitle')):
        logger.warning(f"{prefix} No suitable magnet found")

    return result
