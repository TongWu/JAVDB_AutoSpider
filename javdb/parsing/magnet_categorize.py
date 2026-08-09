"""Magnet link categorisation (parsing layer).

Categorises a movie's magnets into the four buckets used by the pipeline:
``subtitle``, ``hacked_subtitle``, ``hacked_no_subtitle`` and ``no_subtitle``
(plus their ``size_*`` / ``file_count_*`` / ``resolution_*`` counterparts).

Layer note: this module lives in ``javdb.parsing`` and, like the parsers in
``parsing/__init__.py``, prefers the high-performance Rust implementation
(``javdb.rust_core.extract_magnets``) and transparently falls back to the
frozen pure-Python implementation when the Rust extension is unavailable. It
imports only ``javdb.infra.logging`` and ``javdb.rust_core`` — never anything
from ``javdb.spider`` or ``javdb.pipeline``.

Public API (ADR-024):
    categorize          — Rust-first dispatch; production entry point.
    collect_runner_ups  — Pure-Python runner-up extraction (never alters selection).
"""

__all__ = [
    'categorize',
    'collect_runner_ups',
    'infer_resolution',
    '_parse_size',
    '_sort_key',
    '_python_categorize',
    'RUST_MAGNET_AVAILABLE',
]

from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Try Rust implementation
# ---------------------------------------------------------------------------

try:
    from javdb.rust_core import extract_magnets as _rust_extract_magnets
    RUST_MAGNET_AVAILABLE = True
    logger.debug("✅ Rust magnet extractor available")
except ImportError:
    RUST_MAGNET_AVAILABLE = False
    logger.warning(
        "Rust core unavailable — pure-Python magnet fallback is best-effort "
        "and may diverge from production"
    )


def categorize(magnets, index=None):
    """Categorise magnet links into download buckets (Rust-first dispatch).

    Each magnet in *magnets* is a dict with keys:
    ``href``, ``name``, ``tags`` (list[str]), ``size``, ``timestamp``.

    Returns a dict with keys: ``subtitle``, ``hacked_subtitle``,
    ``hacked_no_subtitle``, ``no_subtitle`` (and their ``size_*`` counterparts).

    Prefers the Rust implementation and falls back to the pure-Python
    implementation transparently when Rust is unavailable or errors.
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

    return _python_categorize(magnets, index)


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


# ADR-024 IMP-10: the four production categories, in a stable order.
_QUALITY_CATEGORIES = (
    "hacked_subtitle",
    "hacked_no_subtitle",
    "subtitle",
    "no_subtitle",
)

# Hacked-content name patterns (mirrors _python_categorize exactly).
_HACKED_SUBTITLE_PATTERNS = ('-UC', '-CU', '-C.无码破解', '-U-C', '-C-U')
_HACKED_PATTERNS_ALL = ('-UC', '-CU', '-C.无码破解', '-U-C', '-C-U', '-U', '.无码破解')


def _bucket_magnets(magnets, index=None):
    """Return per-category candidate lists, each sorted best-first.

    Single source of truth for which magnets fall into each production category
    and in what order. ``_python_categorize`` consumes ``bucket[cat][0]`` (the
    production pick); ``collect_runner_ups`` consumes ``bucket[cat][1:]``. The
    filter predicates and ``_sort_key`` ordering are identical between the two
    consumers, guaranteeing runner-ups are categorised exactly as production
    would categorise them.

    Predicates mirror ``_python_categorize`` verbatim:
    - subtitle: has 字幕/Subtitle tag AND .无码破解 not in name
    - hacked_subtitle: name contains any of -UC/-CU/-C.无码破解/-U-C/-C-U
    - hacked_no_subtitle: name contains -U or .无码破解 (but not hacked_subtitle)
    - no_subtitle: not subtitle and not any hacked pattern; 4K (4k in name) first
    """
    # --- subtitle (mirrors _python_categorize: exclude only .无码破解) ---
    subtitle = [
        m for m in magnets
        if any('字幕' in tag or 'Subtitle' in tag for tag in m['tags'])
        and '.无码破解' not in m['name']
    ]
    subtitle.sort(key=_sort_key, reverse=True)

    # --- hacked (mirrors _python_categorize predicate exactly) ---
    hacked_subtitle = []
    hacked_no_subtitle = []
    for m in magnets:
        name = m['name']
        if any(p in name for p in _HACKED_SUBTITLE_PATTERNS):
            hacked_subtitle.append(m)
        elif '-U' in name or '.无码破解' in name:
            hacked_no_subtitle.append(m)
    hacked_subtitle.sort(key=_sort_key, reverse=True)
    hacked_no_subtitle.sort(key=_sort_key, reverse=True)

    # --- no_subtitle (prefer 4K via '4k' in name.lower(); mirrors _python_categorize) ---
    k4 = []
    normal = []
    for m in magnets:
        name = m['name']
        is_subtitle = any('字幕' in tag for tag in m['tags']) and '.无码破解' not in name
        is_hacked = any(p in name for p in _HACKED_PATTERNS_ALL)
        if not is_subtitle and not is_hacked:
            if '4k' in name.lower():
                k4.append(m)
            else:
                normal.append(m)
    k4.sort(key=_sort_key, reverse=True)
    normal.sort(key=_sort_key, reverse=True)
    no_subtitle = k4 + normal

    return {
        "subtitle": subtitle,
        "hacked_subtitle": hacked_subtitle,
        "hacked_no_subtitle": hacked_no_subtitle,
        "no_subtitle": no_subtitle,
    }


def collect_runner_ups(magnets, index=None, k=2):
    """Return up to ``k`` runner-up magnets per production category.

    Additive and read-only: never alters production selection. Runner-ups are
    the candidates ranked 2nd through (k+1)th in each category bucket —
    everything except the production pick (position 0), capped at ``k``.
    Returns a dict keyed by all four production categories.

    Mirrors ``_python_categorize``'s hacked-category exclusivity: production
    selects ``hacked_no_subtitle`` only in the ``elif`` branch, so when a
    ``hacked_subtitle`` pick exists production leaves ``hacked_no_subtitle``
    empty. In that case ``hacked_no_subtitle`` has no production pick to find
    runner-ups against, so we suppress it — otherwise we would wrongly treat
    ``hacked_no_subtitle[0]`` as a pick and queue the rest as runner-ups for a
    category production never chose.
    """
    buckets = _bucket_magnets(magnets, index)
    result = {cat: buckets[cat][1:k + 1] for cat in _QUALITY_CATEGORIES}
    if buckets["hacked_subtitle"]:
        result["hacked_no_subtitle"] = []
    return result


def _python_categorize(magnets, index=None):
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
        is_hacked = any(p in name for p in ('-UC', '-CU', '-C.无码破解', '-U-C', '-C-U', '-U', '.无码破解'))
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
