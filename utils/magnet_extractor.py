import logging
from utils.logging_config import get_logger, setup_logging
try:
    from config import LOG_LEVEL
except ImportError:
    LOG_LEVEL = 'INFO'
setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)

def extract_magnets(magnets, index=None):
    """Extract magnet links based on categories"""
    result = {
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': ''
    }
    
    prefix = f"[{index}]" if index is not None else ""
    
    # Extract subtitle magnets (current magnet_字幕 logic)
    subtitle_magnets = []
    for m in magnets:
        if any('字幕' in tag or 'Subtitle' in tag for tag in m['tags']):
            # Exclude torrents with ".无码破解" in name (these belong to hacked category)
            if '.无码破解' not in m['name']:
                subtitle_magnets.append(m)
    
    if subtitle_magnets:
        # Sort by timestamp first (latest first), then by size (biggest first)
        def parse_size(size_str):
            """Parse size string to bytes for comparison"""
            if not size_str:
                return 0
            size_str = size_str.upper()
            if 'GB' in size_str:
                return float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024
            elif 'MB' in size_str:
                return float(size_str.replace('MB', '').strip()) * 1024 * 1024
            elif 'KB' in size_str:
                return float(size_str.replace('KB', '').strip()) * 1024
            else:
                return 0
        
        def parse_timestamp(time_str):
            """Parse timestamp string to comparable value"""
            if not time_str:
                return ''
            # Keep timestamp as string for sorting (newer timestamps come first alphabetically)
            return time_str
        
        # Sort by timestamp first (latest first), then by size (biggest first)
        subtitle_magnets.sort(key=lambda x: (parse_timestamp(x['timestamp']), parse_size(x['size'])), reverse=True)
        
        best_subtitle = subtitle_magnets[0]
        result['subtitle'] = best_subtitle['href']
        result['size_subtitle'] = best_subtitle['size']
        logger.debug(f"{prefix} Found subtitle magnet: {best_subtitle['name']} (size: {best_subtitle['size']}, time: {best_subtitle['timestamp']})")
    
    # Extract hacked magnets based on new logic
    hacked_subtitle_magnets = []
    hacked_no_subtitle_magnets = []
    
    for m in magnets:
        # Check for hacked_subtitle: -UC or -C.无码破解 in torrent link
        if '-UC' in m['name'] or '-CU' in m['name'] or '-C.无码破解' in m['name'] or '-U-C' in m['name'] or '-C-U' in m['name']:
            hacked_subtitle_magnets.append(m)
        # Check for hacked_no_subtitle: -U.torrent or -U.无码破解 in torrent link
        elif '-U' in m['name'] or '.无码破解' in m['name']:
            hacked_no_subtitle_magnets.append(m)
    
    # Select best hacked_subtitle if available
    if hacked_subtitle_magnets:
        def parse_size(size_str):
            if not size_str:
                return 0
            size_str = size_str.upper()
            if 'GB' in size_str:
                return float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024
            elif 'MB' in size_str:
                return float(size_str.replace('MB', '').strip()) * 1024 * 1024
            elif 'KB' in size_str:
                return float(size_str.replace('KB', '').strip()) * 1024
            else:
                return 0
        def parse_timestamp(time_str):
            if not time_str:
                return ''
            return time_str
        hacked_subtitle_magnets.sort(key=lambda x: (parse_timestamp(x['timestamp']), parse_size(x['size'])), reverse=True)
        best_hacked_subtitle = hacked_subtitle_magnets[0]
        result['hacked_subtitle'] = best_hacked_subtitle['href']
        result['size_hacked_subtitle'] = best_hacked_subtitle['size']
        logger.debug(f"{prefix} Found hacked_subtitle magnet: {best_hacked_subtitle['name']} (size: {best_hacked_subtitle['size']}, time: {best_hacked_subtitle['timestamp']})")
    
    # Select best hacked_no_subtitle if available and no hacked_subtitle
    elif hacked_no_subtitle_magnets:
        def parse_size(size_str):
            if not size_str:
                return 0
            size_str = size_str.upper()
            if 'GB' in size_str:
                return float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024
            elif 'MB' in size_str:
                return float(size_str.replace('MB', '').strip()) * 1024 * 1024
            elif 'KB' in size_str:
                return float(size_str.replace('KB', '').strip()) * 1024
            else:
                return 0
        def parse_timestamp(time_str):
            if not time_str:
                return ''
            return time_str
        hacked_no_subtitle_magnets.sort(key=lambda x: (parse_timestamp(x['timestamp']), parse_size(x['size'])), reverse=True)
        best_hacked_no_subtitle = hacked_no_subtitle_magnets[0]
        result['hacked_no_subtitle'] = best_hacked_no_subtitle['href']
        result['size_hacked_no_subtitle'] = best_hacked_no_subtitle['size']
        logger.debug(f"{prefix} Found hacked_no_subtitle magnet: {best_hacked_no_subtitle['name']} (size: {best_hacked_no_subtitle['size']}, time: {best_hacked_no_subtitle['timestamp']})")
    
    # For no_subtitle, prefer 4k torrent if available, otherwise normal
    # This should always run to populate no_subtitle when appropriate
    k4_magnets = []
    normal_magnets = []
    for m in magnets:
        # Skip if it's already categorized as subtitle or hacked
        is_subtitle = any('字幕' in tag for tag in m['tags']) and '.无码破解' not in m['name']
        is_hacked = '-UC' in m['name'] or '-U' in m['name'] or '.无码破解' in m['name']
        
        if not is_subtitle and not is_hacked:
            is_4k = '-4k' in m['name'].lower() or '4k' in m['name'].lower()
            if is_4k:
                k4_magnets.append(m)
            else:
                normal_magnets.append(m)
    
    def parse_size(size_str):
        if not size_str:
            return 0
        size_str = size_str.upper()
        if 'GB' in size_str:
            return float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024
        elif 'MB' in size_str:
            return float(size_str.replace('MB', '').strip()) * 1024 * 1024
        elif 'KB' in size_str:
            return float(size_str.replace('KB', '').strip()) * 1024
        else:
            return 0
    def parse_timestamp(time_str):
        if not time_str:
            return ''
        return time_str
    
    if k4_magnets:
        # Prefer 4k torrents for no_subtitle
        k4_magnets.sort(key=lambda x: (parse_timestamp(x['timestamp']), parse_size(x['size'])), reverse=True)
        best_4k = k4_magnets[0]
        result['no_subtitle'] = best_4k['href']
        result['size_no_subtitle'] = best_4k['size']
        logger.debug(f"{prefix} Found 4K magnet for no_subtitle: {best_4k['name']} (size: {best_4k['size']}, time: {best_4k['timestamp']})")
    elif normal_magnets:
        normal_magnets.sort(key=lambda x: (parse_timestamp(x['timestamp']), parse_size(x['size'])), reverse=True)
        best_normal = normal_magnets[0]
        result['no_subtitle'] = best_normal['href']
        result['size_no_subtitle'] = best_normal['size']
        logger.debug(f"{prefix} Found normal magnet for no_subtitle: {best_normal['name']} (size: {best_normal['size']}, time: {best_normal['timestamp']})")
    
    if not result['subtitle'] and not result['hacked_subtitle'] and not result['hacked_no_subtitle'] and not result['no_subtitle']:
        logger.warning(f"{prefix} No suitable magnet found")
    
    return result 