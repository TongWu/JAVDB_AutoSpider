#!/usr/bin/env python3
"""
Config Generator for JAVDB AutoSpider

Generates config.py from environment variables.
This module can be used by GitHub Actions workflow or run locally.

Usage:
    # In GitHub Actions (reads from VAR_* environment variables)
    python3 -m apps.cli.config_generator

    # With custom output path
    python3 -m apps.cli.config_generator --output /path/to/config.py

    # Dry run (print config without writing)
    python3 -m apps.cli.config_generator --dry-run

    # For GitHub Actions mode (empty GIT_PASSWORD, hardcoded values)
    python3 -m apps.cli.config_generator --github-actions
"""

import os
import re
import json
import argparse
from typing import Any, Callable, List, Tuple, Dict, Optional


# =============================================================================
# Environment Variable Helpers
# =============================================================================

# Placeholder values that represent "empty" (use these in GitHub when you want an empty value)
EMPTY_PLACEHOLDERS = ('__EMPTY__', '__NULL__', 'null', 'none', 'NULL', 'NONE')


def get_env(name: str, default: str = '') -> str:
    """Get environment variable with default value.
    
    Supports VAR_ prefix for GitHub Actions compatibility.
    Use __EMPTY__ or __NULL__ in GitHub Variables/Secrets to represent empty string.
    
    Args:
        name: Environment variable name (without VAR_ prefix)
        default: Default value if not set
    
    Returns:
        Environment variable value or default
    """
    # Try with VAR_ prefix first (GitHub Actions mode)
    val = os.environ.get(f'VAR_{name}', None)
    if val is None:
        # Try without prefix (local/direct mode)
        val = os.environ.get(name, default)
    
    if val in EMPTY_PLACEHOLDERS:
        return ''
    return val or default


def get_env_int(name: str, default: int) -> int:
    """Get environment variable as integer.
    
    Args:
        name: Environment variable name
        default: Default value if not set or invalid
    
    Returns:
        Integer value
    """
    val = get_env(name, str(default))
    if val in EMPTY_PLACEHOLDERS or val == '':
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_env_float(name: str, default: float) -> float:
    """Get environment variable as float.
    
    Args:
        name: Environment variable name
        default: Default value if not set or invalid
    
    Returns:
        Float value
    """
    val = get_env(name, str(default))
    if val in EMPTY_PLACEHOLDERS or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_env_range_min(name: str, default: int) -> int:
    """Get the minimum value from an 'A,B' range environment variable.

    Falls back to interpreting as a single integer when no comma is present.
    """
    val = get_env(name, str(default))
    if val in EMPTY_PLACEHOLDERS or val == '':
        return default
    if ',' in val:
        try:
            return int(val.split(',')[0].strip())
        except (ValueError, TypeError):
            return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_env_range_max(name: str, default: int) -> int:
    """Get the maximum value from an 'A,B' range environment variable.

    Falls back to interpreting as a single integer when no comma is present.
    """
    val = get_env(name, str(default))
    if val in EMPTY_PLACEHOLDERS or val == '':
        return default
    if ',' in val:
        try:
            return int(val.split(',')[1].strip())
        except (ValueError, TypeError):
            return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_env_bool(name: str, default: bool) -> bool:
    """Get environment variable as boolean.
    
    Args:
        name: Environment variable name
        default: Default value if not set or invalid
    
    Returns:
        Boolean value
    """
    val = get_env(name, str(default)).lower()
    if val in ('__empty__', '__null__'):
        return default
    if val in ('true', '1', 'yes'):
        return True
    elif val in ('false', '0', 'no'):
        return False
    return default


def get_env_json(name: str, default: Any) -> Any:
    """Get environment variable as JSON.
    
    Args:
        name: Environment variable name
        default: Default value if not set or invalid JSON
    
    Returns:
        Parsed JSON value or default
    """
    val = get_env(name, '')
    if val.strip():
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return default


def get_env_bool_optional(name: str) -> Optional[bool]:
    """Get environment variable as an optional boolean.

    Returns ``None`` when the variable is unset or empty, which lets callers
    distinguish "not provided" from an explicit true/false override.
    """
    val = os.environ.get(f'VAR_{name}', None)
    if val is None:
        val = os.environ.get(name, None)
    if val is None:
        return None

    lowered = str(val).strip().lower()
    if not lowered or lowered in ('__empty__', '__null__'):
        return None
    if lowered in ('true', '1', 'yes'):
        return True
    if lowered in ('false', '0', 'no'):
        return False
    return None


def resolve_qb_allow_insecure_http(_n: Optional[str], default: bool) -> bool:
    """Resolve QB_ALLOW_INSECURE_HTTP for generated config.

    Explicit ``VAR_QB_ALLOW_INSECURE_HTTP`` / ``QB_ALLOW_INSECURE_HTTP`` wins.
    Otherwise, if ``QB_URL`` is plain ``http://``, default to True so CI and
    LAN Web UI URLs do not fail import-time validation in ``qb_config``.
    """
    explicit = get_env_bool_optional('QB_ALLOW_INSECURE_HTTP')
    if explicit is not None:
        return explicit
    qb_url = get_env('QB_URL', '').strip()
    if qb_url.lower().startswith('http://'):
        return True
    return default


def resolve_proxy_modules(default: Optional[List[str]] = None) -> List[str]:
    """Resolve proxy-enabled modules from JSON config and per-module overrides."""
    default_modules = default or ['spider']
    configured_modules = get_env_json('PROXY_MODULES_JSON', default_modules)
    if not isinstance(configured_modules, list):
        configured_modules = default_modules

    module_env_map = (
        ('spider', 'PROXY_SPIDER_ENABLED'),
        ('qbittorrent', 'PROXY_QBITTORRENT_ENABLED'),
        ('pikpak', 'PROXY_PIKPAK_ENABLED'),
    )

    has_override = False
    resolved_modules: List[str] = []
    for module_name, env_name in module_env_map:
        enabled = get_env_bool_optional(env_name)
        if enabled is not None:
            has_override = True
        else:
            enabled = 'all' in configured_modules or module_name in configured_modules
        if enabled:
            resolved_modules.append(module_name)

    return resolved_modules if has_override else configured_modules


def format_python_value(value: Any) -> str:
    """Format Python value for config.py output.
    
    Args:
        value: Python value to format
    
    Returns:
        String representation for config.py
    """
    if isinstance(value, str):
        return repr(value)
    elif isinstance(value, bool):
        return 'True' if value else 'False'
    elif isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    elif value is None:
        return 'None'
    else:
        return str(value)


# =============================================================================
# Configuration Mapping
# =============================================================================

def get_config_map(github_actions_mode: bool = False) -> List[Tuple[str, str, Callable, Any, str]]:
    """Get the configuration mapping.
    
    Configuration mapping format:
    (config_name, env_name, type_func, default_value, section)
    
    Args:
        github_actions_mode: If True, use GitHub Actions specific values
                            (empty GIT_PASSWORD, hardcoded GIT_BRANCH='main')
    
    Returns:
        List of configuration tuples
    """
    if github_actions_mode:
        # GitHub Actions mode: GIT_PASSWORD is empty (scripts don't commit, workflow handles it)
        git_config = [
            ('GIT_USERNAME', None, lambda n, d: 'github-actions', 'github-actions', 'GIT CONFIGURATION'),
            ('GIT_PASSWORD', None, lambda n, d: '', '', 'GIT CONFIGURATION'),  # Empty - no script commits
            ('GIT_REPO_URL', 'GIT_REPO_URL', get_env, 'https://github.com/user/repo.git', 'GIT CONFIGURATION'),
            ('GIT_BRANCH', 'GIT_BRANCH', get_env, 'main', 'GIT CONFIGURATION'),  # Read from env or default to main
        ]
    else:
        # Local/pipeline mode: use environment variables
        git_config = [
            ('GIT_USERNAME', 'GIT_USERNAME', get_env, 'github-actions', 'GIT CONFIGURATION'),
            ('GIT_PASSWORD', 'GIT_PASSWORD', get_env, '', 'GIT CONFIGURATION'),
            ('GIT_REPO_URL', 'GIT_REPO_URL', get_env, 'https://github.com/user/repo.git', 'GIT CONFIGURATION'),
            ('GIT_BRANCH', 'GIT_BRANCH', get_env, 'main', 'GIT CONFIGURATION'),
        ]
    
    reports_dir = get_env('REPORTS_DIR', 'reports')

    return git_config + [
        # qBittorrent Configuration
        ('QB_URL', 'QB_URL', get_env, 'https://localhost:8080', 'QBITTORRENT CONFIGURATION'),
        ('QB_ALLOW_INSECURE_HTTP', None, resolve_qb_allow_insecure_http, False, 'QBITTORRENT CONFIGURATION'),
        ('QB_VERIFY_TLS', 'QB_VERIFY_TLS', get_env_bool, True, 'QBITTORRENT CONFIGURATION'),
        ('QB_USERNAME', 'QB_USERNAME', get_env, 'admin', 'QBITTORRENT CONFIGURATION'),
        ('QB_PASSWORD', 'QB_PASSWORD', get_env, '', 'QBITTORRENT CONFIGURATION'),
        ('TORRENT_CATEGORY', 'TORRENT_CATEGORY', get_env, 'Daily Ingestion', 'QBITTORRENT CONFIGURATION'),
        ('TORRENT_CATEGORY_ADHOC', 'TORRENT_CATEGORY_ADHOC', get_env, 'Ad Hoc', 'QBITTORRENT CONFIGURATION'),
        # Adhoc qBittorrent instance (optional — empty means disabled)
        ('QB_URL_ADHOC', 'QB_URL_ADHOC', get_env, '', 'QBITTORRENT CONFIGURATION'),
        ('QB_USERNAME_ADHOC', 'QB_USERNAME_ADHOC', get_env, '', 'QBITTORRENT CONFIGURATION'),
        ('QB_PASSWORD_ADHOC', 'QB_PASSWORD_ADHOC', get_env, '', 'QBITTORRENT CONFIGURATION'),
        ('TORRENT_SAVE_PATH', 'TORRENT_SAVE_PATH', get_env, '', 'QBITTORRENT CONFIGURATION'),
        ('AUTO_START', 'AUTO_START', get_env_bool, True, 'QBITTORRENT CONFIGURATION'),
        ('SKIP_CHECKING', 'SKIP_CHECKING', get_env_bool, False, 'QBITTORRENT CONFIGURATION'),
        ('REQUEST_TIMEOUT', 'REQUEST_TIMEOUT', get_env_int, 30, 'QBITTORRENT CONFIGURATION'),
        ('DELAY_BETWEEN_ADDITIONS', 'DELAY_BETWEEN_ADDITIONS', get_env_int, 1, 'QBITTORRENT CONFIGURATION'),
        # SMTP Configuration
        ('SMTP_SERVER', 'SMTP_SERVER', get_env, 'smtp.gmail.com', 'SMTP CONFIGURATION'),
        ('SMTP_PORT', 'SMTP_PORT', get_env_int, 587, 'SMTP CONFIGURATION'),
        ('SMTP_USER', 'SMTP_USER', get_env, '', 'SMTP CONFIGURATION'),
        ('SMTP_PASSWORD', 'SMTP_PASSWORD', get_env, '', 'SMTP CONFIGURATION'),
        ('EMAIL_FROM', 'EMAIL_FROM', get_env, '', 'SMTP CONFIGURATION'),
        ('EMAIL_TO', 'EMAIL_TO', get_env, '', 'SMTP CONFIGURATION'),
        # Proxy Configuration
        ('PROXY_MODE', 'PROXY_MODE', get_env, 'pool', 'PROXY CONFIGURATION'),
        ('PROXY_POOL', 'PROXY_POOL_JSON', get_env_json, [], 'PROXY CONFIGURATION'),
        ('PROXY_POOL_MAX_FAILURES', 'PROXY_POOL_MAX_FAILURES', get_env_int, 3, 'PROXY CONFIGURATION'),
        ('PROXY_HTTP', None, lambda n, d: None, None, 'PROXY CONFIGURATION'),  # Hardcoded None
        ('PROXY_HTTPS', None, lambda n, d: None, None, 'PROXY CONFIGURATION'),  # Hardcoded None
        (
            'PROXY_MODULES',
            None,
            lambda _n, default: resolve_proxy_modules(default),
            ['spider'],
            'PROXY CONFIGURATION',
        ),
        ('LOGIN_PROXY_NAME', 'LOGIN_PROXY_NAME', get_env, '', 'PROXY CONFIGURATION'),
        # Cloudflare Bypass Configuration
        ('CF_BYPASS_SERVICE_PORT', 'CF_BYPASS_SERVICE_PORT', get_env_int, 8000, 'CLOUDFLARE BYPASS CONFIGURATION'),
        ('CF_BYPASS_ENABLED', 'CF_BYPASS_ENABLED', get_env_bool, True, 'CLOUDFLARE BYPASS CONFIGURATION'),
        ('CF_BYPASS_PORT_MAP', 'CF_BYPASS_PORT_MAP_JSON', get_env_json, {}, 'CLOUDFLARE BYPASS CONFIGURATION'),
        # Spider Configuration
        # Fall back to legacy START_PAGE / END_PAGE env vars for backward compatibility
        ('PAGE_START', 'PAGE_START', get_env_int, get_env_int('START_PAGE', 1), 'SPIDER CONFIGURATION'),
        ('PAGE_END', 'PAGE_END', get_env_int, get_env_int('END_PAGE', 10), 'SPIDER CONFIGURATION'),
        ('PHASE2_MIN_RATE', 'PHASE2_MIN_RATE', get_env_float, 4.0, 'SPIDER CONFIGURATION'),
        ('PHASE2_MIN_COMMENTS', 'PHASE2_MIN_COMMENTS', get_env_int, 85, 'SPIDER CONFIGURATION'),
        ('BASE_URL', 'BASE_URL', get_env, 'https://javdb.com', 'SPIDER CONFIGURATION'),
        # JavDB Login Configuration
        ('JAVDB_USERNAME', 'JAVDB_USERNAME', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        ('JAVDB_PASSWORD', 'JAVDB_PASSWORD', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        ('JAVDB_SESSION_COOKIE', 'JAVDB_SESSION_COOKIE', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        # GPT API Configuration (optional - for automatic captcha solving during login)
        ('GPT_API_URL', 'GPT_API_URL', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        ('GPT_API_KEY', 'GPT_API_KEY', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        # Request Timing Configuration
        ('MOVIE_SLEEP_MIN', 'MOVIE_SLEEP', get_env_range_min, None, 'REQUEST TIMING CONFIGURATION'),
        ('MOVIE_SLEEP_MAX', 'MOVIE_SLEEP', get_env_range_max, None, 'REQUEST TIMING CONFIGURATION'),
        # Logging Configuration
        ('LOG_LEVEL', 'LOG_LEVEL', get_env, 'INFO', 'LOGGING CONFIGURATION'),
        ('SPIDER_LOG_FILE', 'SPIDER_LOG_FILE', get_env, 'logs/spider.log', 'LOGGING CONFIGURATION'),
        ('UPLOADER_LOG_FILE', 'UPLOADER_LOG_FILE', get_env, 'logs/qb_uploader.log', 'LOGGING CONFIGURATION'),
        ('PIPELINE_LOG_FILE', 'PIPELINE_LOG_FILE', get_env, 'logs/pipeline.log', 'LOGGING CONFIGURATION'),
        ('EMAIL_NOTIFICATION_LOG_FILE', 'EMAIL_NOTIFICATION_LOG_FILE', get_env, 'logs/email_notification.log', 'LOGGING CONFIGURATION'),
        # Parsing Configuration
        ('IGNORE_RELEASE_DATE_FILTER', 'IGNORE_RELEASE_DATE_FILTER', get_env_bool, False, 'PARSING CONFIGURATION'),
        ('INCLUDE_DOWNLOADED_IN_REPORT', 'INCLUDE_DOWNLOADED_IN_REPORT', get_env_bool, False, 'PARSING CONFIGURATION'),
        # File Paths
        ('REPORTS_DIR', 'REPORTS_DIR', get_env, 'reports', 'FILE PATHS'),
        ('DAILY_REPORT_DIR', 'DAILY_REPORT_DIR', get_env, os.path.join(reports_dir, 'DailyReport'), 'FILE PATHS'),
        ('AD_HOC_DIR', 'AD_HOC_DIR', get_env, os.path.join(reports_dir, 'AdHoc'), 'FILE PATHS'),
        ('PARSED_MOVIES_CSV', 'PARSED_MOVIES_CSV', get_env, 'parsed_movies_history.csv', 'FILE PATHS'),
        ('HISTORY_DB_PATH', 'HISTORY_DB_PATH', get_env, os.path.join(reports_dir, 'history.db'), 'FILE PATHS'),
        ('REPORTS_DB_PATH', 'REPORTS_DB_PATH', get_env, os.path.join(reports_dir, 'reports.db'), 'FILE PATHS'),
        ('OPERATIONS_DB_PATH', 'OPERATIONS_DB_PATH', get_env, os.path.join(reports_dir, 'operations.db'), 'FILE PATHS'),
        # PikPak Configuration
        ('PIKPAK_EMAIL', 'PIKPAK_EMAIL', get_env, '', 'PIKPAK CONFIGURATION'),
        ('PIKPAK_PASSWORD', 'PIKPAK_PASSWORD', get_env, '', 'PIKPAK CONFIGURATION'),
        ('PIKPAK_LOG_FILE', 'PIKPAK_LOG_FILE', get_env, 'logs/pikpak_bridge.log', 'PIKPAK CONFIGURATION'),
        ('PIKPAK_REQUEST_DELAY', 'PIKPAK_REQUEST_DELAY', get_env_int, 2, 'PIKPAK CONFIGURATION'),
        # qBittorrent File Filter Configuration
        ('QB_FILE_FILTER_MIN_SIZE_MB', 'QB_FILE_FILTER_MIN_SIZE_MB', get_env_int, 100, 'QBITTORRENT FILE FILTER CONFIGURATION'),
        ('QB_FILE_FILTER_LOG_FILE', 'QB_FILE_FILTER_LOG_FILE', get_env, 'logs/qb_file_filter.log', 'QBITTORRENT FILE FILTER CONFIGURATION'),
        # Rclone Configuration
        ('RCLONE_CONFIG_BASE64', 'RCLONE_CONFIG_BASE64', get_env, '', 'RCLONE CONFIGURATION'),
        ('RCLONE_FOLDER_PATH', 'RCLONE_FOLDER_PATH', get_env, 'gdrive:', 'RCLONE CONFIGURATION'),
        # Storage Mode
        ('STORAGE_MODE', 'STORAGE_MODE', get_env, 'duo', 'STORAGE MODE'),
        # Dedup Configuration
        ('RCLONE_INVENTORY_CSV', 'RCLONE_INVENTORY_CSV', get_env, 'rclone_inventory.csv', 'DEDUP CONFIGURATION'),
        ('DEDUP_CSV', 'DEDUP_CSV', get_env, 'dedup.csv', 'DEDUP CONFIGURATION'),
        ('DEDUP_DIR', 'DEDUP_DIR', get_env, 'reports/Dedup', 'DEDUP CONFIGURATION'),
        ('DEDUP_LOG_FILE', 'DEDUP_LOG_FILE', get_env, 'logs/rclone_dedup.log', 'DEDUP CONFIGURATION'),
    ]


# =============================================================================
# Config Generation
# =============================================================================

def generate_config_content(github_actions_mode: bool = False) -> str:
    """Generate config.py content from environment variables.
    
    Args:
        github_actions_mode: If True, use GitHub Actions specific values
    
    Returns:
        Config file content as string
    """
    config_map = get_config_map(github_actions_mode)
    
    # Group configs by section
    sections: Dict[str, List[Tuple[str, Any]]] = {}
    for config_name, env_name, type_func, default, section in config_map:
        if section not in sections:
            sections[section] = []
        # Get value from environment or use default
        if env_name:
            value = type_func(env_name, default)
        else:
            value = type_func(None, default) if callable(type_func) else default
        if config_name == 'CF_BYPASS_PORT_MAP' and not isinstance(value, dict):
            value = {}
        sections[section].append((config_name, value))
    
    # Build config file content
    if github_actions_mode:
        config_lines = [
            '# JavDB Auto Spider - Unified Configuration File',
            '# Auto-generated by GitHub Actions from repository variables',
            '# Note: GIT_PASSWORD is empty - commits are handled by workflow, not scripts',
            '',
        ]
    else:
        config_lines = [
            '# JavDB Auto Spider - Unified Configuration File',
            '# Auto-generated from environment variables',
            '',
        ]
    
    for section_name, configs in sections.items():
        config_lines.append('# ' + '=' * 75)
        config_lines.append(f'# {section_name}')
        config_lines.append('# ' + '=' * 75)
        config_lines.append('')
        
        for config_name, value in configs:
            formatted_value = format_python_value(value)
            config_lines.append(f'{config_name} = {formatted_value}')
        
        config_lines.append('')
    
    return '\n'.join(config_lines)


def mask_sensitive_values(content: str) -> str:
    """Mask sensitive values in config content for safe display.
    
    Args:
        content: Config file content
    
    Returns:
        Content with masked sensitive values
    """
    masked = content
    # Mask passwords - use \s* instead of .* to avoid greedy matching across fields
    masked = re.sub(r"(PASSWORD\s*=\s*')[^']*(')", r"\1***MASKED***\2", masked)
    masked = re.sub(r'(PASSWORD\s*=\s*")[^"]*(")', r"\1***MASKED***\2", masked)
    # Mask GitHub tokens
    masked = re.sub(r"(ghp_)[a-zA-Z0-9]+", r"\1***MASKED***", masked)
    # Mask cookies - use \s* instead of .* to avoid greedy matching
    masked = re.sub(r"(COOKIE\s*=\s*')[^']*(')", r"\1***MASKED***\2", masked)
    masked = re.sub(r'(COOKIE\s*=\s*")[^"]*(")', r"\1***MASKED***\2", masked)
    # Mask proxy pool (may contain IPs and passwords)
    masked = re.sub(r"(PROXY_POOL\s*=\s*\[)[^\]]*(\])", r"\1***MASKED***\2", masked)
    # Mask CF bypass port map (may expose internal topology)
    masked = re.sub(r"(CF_BYPASS_PORT_MAP\s*=\s*)\{[^}]*\}", r"\1***MASKED***", masked)
    return masked


def write_config(output_path: str = 'config.py', github_actions_mode: bool = False, 
                 dry_run: bool = False, show_masked: bool = True) -> bool:
    """Write config.py file.
    
    Args:
        output_path: Path to output config file
        github_actions_mode: If True, use GitHub Actions specific values
        dry_run: If True, only print config without writing
        show_masked: If True, print masked config content
    
    Returns:
        True if successful, False otherwise
    """
    try:
        config_content = generate_config_content(github_actions_mode)
        
        if not dry_run:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            print(f"✓ {output_path} generated successfully")
        else:
            print("✓ Dry run - config.py would be generated with the following content:")
        
        if show_masked:
            masked = mask_sensitive_values(config_content)
            print("\nConfig file contents (sensitive values masked):")
            print(masked)
        
        return True
        
    except Exception as e:
        print(f"✗ Failed to generate config.py: {e}")
        return False


# =============================================================================
# CLI Entry Point
# =============================================================================

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Generate config.py from environment variables',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate config.py from environment variables
    python3 -m apps.cli.config_generator
    
    # Generate config.py for GitHub Actions (empty GIT_PASSWORD)
    python3 -m apps.cli.config_generator --github-actions
    
    # Dry run - show what would be generated
    python3 -m apps.cli.config_generator --dry-run
    
    # Generate to custom path
    python3 -m apps.cli.config_generator --output /path/to/config.py
    
    # Hide masked output
    python3 -m apps.cli.config_generator --quiet
        """
    )
    
    parser.add_argument('--output', '-o', type=str, default='config.py',
                        help='Output path for config.py (default: config.py)')
    parser.add_argument('--github-actions', action='store_true',
                        help='Use GitHub Actions mode (empty GIT_PASSWORD, hardcoded GIT_BRANCH)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print config without writing to file')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Do not print masked config content')
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()
    
    success = write_config(
        output_path=args.output,
        github_actions_mode=args.github_actions,
        dry_run=args.dry_run,
        show_masked=not args.quiet
    )
    
    return 0 if success else 1


if __name__ == '__main__':
    exit(main())
