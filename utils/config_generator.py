#!/usr/bin/env python3
"""
Config Generator for JAVDB AutoSpider

Generates config.py from environment variables.
This module can be used by GitHub Actions workflow or run locally.

Usage:
    # In GitHub Actions (reads from VAR_* environment variables)
    python3 utils/config_generator.py
    
    # With custom output path
    python3 utils/config_generator.py --output /path/to/config.py
    
    # Dry run (print config without writing)
    python3 utils/config_generator.py --dry-run
    
    # For GitHub Actions mode (empty GIT_PASSWORD, hardcoded values)
    python3 utils/config_generator.py --github-actions
"""

import os
import re
import json
import argparse
from typing import Any, Callable, List, Tuple, Dict


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
        # GitHub Actions mode: GIT_PASSWORD is empty, GIT_BRANCH is hardcoded to 'main'
        git_config = [
            ('GIT_USERNAME', None, lambda n, d: 'github-actions', 'github-actions', 'GIT CONFIGURATION'),
            ('GIT_PASSWORD', None, lambda n, d: '', '', 'GIT CONFIGURATION'),  # Empty - no script commits
            ('GIT_REPO_URL', 'GIT_REPO_URL', get_env, 'https://github.com/user/repo.git', 'GIT CONFIGURATION'),
            ('GIT_BRANCH', None, lambda n, d: 'main', 'main', 'GIT CONFIGURATION'),  # Hardcoded to main
        ]
    else:
        # Local/pipeline mode: use environment variables
        git_config = [
            ('GIT_USERNAME', 'GIT_USERNAME', get_env, 'github-actions', 'GIT CONFIGURATION'),
            ('GIT_PASSWORD', 'GIT_PASSWORD', get_env, '', 'GIT CONFIGURATION'),
            ('GIT_REPO_URL', 'GIT_REPO_URL', get_env, 'https://github.com/user/repo.git', 'GIT CONFIGURATION'),
            ('GIT_BRANCH', 'GIT_BRANCH', get_env, 'main', 'GIT CONFIGURATION'),
        ]
    
    return git_config + [
        # qBittorrent Configuration
        ('QB_HOST', 'QB_HOST', get_env, 'localhost', 'QBITTORRENT CONFIGURATION'),
        ('QB_PORT', 'QB_PORT', get_env, '8080', 'QBITTORRENT CONFIGURATION'),
        ('QB_USERNAME', 'QB_USERNAME', get_env, 'admin', 'QBITTORRENT CONFIGURATION'),
        ('QB_PASSWORD', 'QB_PASSWORD', get_env, '', 'QBITTORRENT CONFIGURATION'),
        ('TORRENT_CATEGORY', 'TORRENT_CATEGORY', get_env, 'Daily Ingestion', 'QBITTORRENT CONFIGURATION'),
        ('TORRENT_CATEGORY_ADHOC', 'TORRENT_CATEGORY_ADHOC', get_env, 'Ad Hoc', 'QBITTORRENT CONFIGURATION'),
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
        ('PROXY_POOL_COOLDOWN_SECONDS', 'PROXY_POOL_COOLDOWN_SECONDS', get_env_int, 691200, 'PROXY CONFIGURATION'),
        ('PROXY_POOL_MAX_FAILURES', 'PROXY_POOL_MAX_FAILURES', get_env_int, 3, 'PROXY CONFIGURATION'),
        ('PROXY_HTTP', None, lambda n, d: None, None, 'PROXY CONFIGURATION'),  # Hardcoded None
        ('PROXY_HTTPS', None, lambda n, d: None, None, 'PROXY CONFIGURATION'),  # Hardcoded None
        ('PROXY_MODULES', 'PROXY_MODULES_JSON', get_env_json, ['spider_index', 'spider_detail'], 'PROXY CONFIGURATION'),
        # Cloudflare Bypass Configuration
        ('CF_BYPASS_SERVICE_PORT', 'CF_BYPASS_SERVICE_PORT', get_env_int, 8000, 'CLOUDFLARE BYPASS CONFIGURATION'),
        ('CF_BYPASS_ENABLED', 'CF_BYPASS_ENABLED', get_env_bool, True, 'CLOUDFLARE BYPASS CONFIGURATION'),
        # Spider Configuration
        ('START_PAGE', 'START_PAGE', get_env_int, 1, 'SPIDER CONFIGURATION'),
        ('END_PAGE', 'END_PAGE', get_env_int, 10, 'SPIDER CONFIGURATION'),
        ('PHASE2_MIN_RATE', 'PHASE2_MIN_RATE', get_env_float, 4.0, 'SPIDER CONFIGURATION'),
        ('PHASE2_MIN_COMMENTS', 'PHASE2_MIN_COMMENTS', get_env_int, 85, 'SPIDER CONFIGURATION'),
        ('BASE_URL', 'BASE_URL', get_env, 'https://javdb.com', 'SPIDER CONFIGURATION'),
        # JavDB Login Configuration
        ('JAVDB_USERNAME', 'JAVDB_USERNAME', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        ('JAVDB_PASSWORD', 'JAVDB_PASSWORD', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        ('JAVDB_SESSION_COOKIE', 'JAVDB_SESSION_COOKIE', get_env, '', 'JAVDB LOGIN CONFIGURATION'),
        ('DETAIL_PAGE_SLEEP', 'DETAIL_PAGE_SLEEP', get_env_int, 30, 'JAVDB LOGIN CONFIGURATION'),
        ('PAGE_SLEEP', 'PAGE_SLEEP', get_env_int, 15, 'JAVDB LOGIN CONFIGURATION'),
        ('MOVIE_SLEEP', 'MOVIE_SLEEP', get_env_int, 15, 'JAVDB LOGIN CONFIGURATION'),
        ('CF_TURNSTILE_COOLDOWN', 'CF_TURNSTILE_COOLDOWN', get_env_int, 30, 'JAVDB LOGIN CONFIGURATION'),
        ('PHASE_TRANSITION_COOLDOWN', 'PHASE_TRANSITION_COOLDOWN', get_env_int, 60, 'JAVDB LOGIN CONFIGURATION'),
        ('FALLBACK_COOLDOWN', 'FALLBACK_COOLDOWN', get_env_int, 30, 'JAVDB LOGIN CONFIGURATION'),
        # Logging Configuration
        ('LOG_LEVEL', 'LOG_LEVEL', get_env, 'INFO', 'LOGGING CONFIGURATION'),
        ('SPIDER_LOG_FILE', 'SPIDER_LOG_FILE', get_env, 'logs/spider.log', 'LOGGING CONFIGURATION'),
        ('UPLOADER_LOG_FILE', 'UPLOADER_LOG_FILE', get_env, 'logs/qb_uploader.log', 'LOGGING CONFIGURATION'),
        ('PIPELINE_LOG_FILE', 'PIPELINE_LOG_FILE', get_env, 'logs/pipeline.log', 'LOGGING CONFIGURATION'),
        ('EMAIL_NOTIFICATION_LOG_FILE', 'EMAIL_NOTIFICATION_LOG_FILE', get_env, 'logs/email_notification.log', 'LOGGING CONFIGURATION'),
        # Parsing Configuration
        ('IGNORE_RELEASE_DATE_FILTER', 'IGNORE_RELEASE_DATE_FILTER', get_env_bool, False, 'PARSING CONFIGURATION'),
        # File Paths
        ('DAILY_REPORT_DIR', 'DAILY_REPORT_DIR', get_env, 'Daily Report', 'FILE PATHS'),
        ('AD_HOC_DIR', 'AD_HOC_DIR', get_env, 'Ad Hoc', 'FILE PATHS'),
        ('PARSED_MOVIES_CSV', 'PARSED_MOVIES_CSV', get_env, 'parsed_movies_history.csv', 'FILE PATHS'),
        # PikPak Configuration
        ('PIKPAK_EMAIL', 'PIKPAK_EMAIL', get_env, '', 'PIKPAK CONFIGURATION'),
        ('PIKPAK_PASSWORD', 'PIKPAK_PASSWORD', get_env, '', 'PIKPAK CONFIGURATION'),
        ('PIKPAK_LOG_FILE', 'PIKPAK_LOG_FILE', get_env, 'logs/pikpak_bridge.log', 'PIKPAK CONFIGURATION'),
        ('PIKPAK_REQUEST_DELAY', 'PIKPAK_REQUEST_DELAY', get_env_int, 3, 'PIKPAK CONFIGURATION'),
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
    # Mask passwords
    masked = re.sub(r"(PASSWORD.*=.*')[^']*(')", r"\1***MASKED***\2", masked)
    masked = re.sub(r'(PASSWORD.*=.*")[^"]*(")', r"\1***MASKED***\2", masked)
    # Mask GitHub tokens
    masked = re.sub(r"(ghp_)[a-zA-Z0-9]+", r"\1***MASKED***", masked)
    # Mask cookies
    masked = re.sub(r"(COOKIE.*=.*')[^']*(')", r"\1***MASKED***\2", masked)
    masked = re.sub(r'(COOKIE.*=.*")[^"]*(")', r"\1***MASKED***\2", masked)
    # Mask proxy pool (may contain IPs and passwords)
    masked = re.sub(r"(PROXY_POOL.*=.*\[)[^\]]*(\])", r"\1***MASKED***\2", masked)
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
    python3 utils/config_generator.py
    
    # Generate config.py for GitHub Actions (empty GIT_PASSWORD)
    python3 utils/config_generator.py --github-actions
    
    # Dry run - show what would be generated
    python3 utils/config_generator.py --dry-run
    
    # Generate to custom path
    python3 utils/config_generator.py --output /path/to/config.py
    
    # Hide masked output
    python3 utils/config_generator.py --quiet
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

