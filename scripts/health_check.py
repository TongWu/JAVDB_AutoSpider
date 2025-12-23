#!/usr/bin/env python3
"""
Health Check Script for JAVDB AutoSpider

This script performs pre-flight health checks before running the main pipeline.
It verifies connectivity to services:

Critical checks (will fail the pipeline if not passed):
- Proxy pool availability (when PROXY_MODE='pool' and all proxies banned)

Non-critical checks (warnings only, pipeline continues):
- qBittorrent Web UI (uploader step may fail, but spider can still run)
- SMTP server (optional, email notification may fail)

Usage:
    python3 scripts/health_check.py [--check-smtp] [--use-proxy]

Exit codes:
    0: All critical checks passed
    1: One or more critical checks failed
"""

import os
import sys
import re
import argparse
import socket
import logging
from typing import Tuple, List
from datetime import datetime

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)


# Import masking utilities
from utils.masking import mask_ip_address, mask_username, mask_server, mask_full

# Import configuration
try:
    from config import (
        QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD,
        SMTP_SERVER, SMTP_PORT,
        PROXY_POOL, PROXY_MODE,
        LOG_LEVEL
    )
except ImportError:
    print("ERROR: config.py not found. Please run config generator first.")
    sys.exit(1)

# Setup basic logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_qbittorrent_connection() -> Tuple[bool, str]:
    """
    Check qBittorrent Web UI connectivity and authentication.
    
    Returns:
        Tuple of (success, message)
    """
    masked_host = mask_ip_address(QB_HOST)
    
    try:
        import requests
        
        base_url = f"http://{QB_HOST}:{QB_PORT}"
        login_url = f"{base_url}/api/v2/auth/login"
        
        # Test connection with timeout
        logger.info(f"Testing qBittorrent connection to {masked_host}:{QB_PORT}...")
        
        session = requests.Session()
        # Disable environment proxy to avoid interference
        session.trust_env = False
        response = session.post(
            login_url,
            data={'username': QB_USERNAME, 'password': QB_PASSWORD},
            timeout=10
        )
        
        if response.status_code == 200 and response.text == 'Ok.':
            # Try to get version to confirm full access
            version_response = session.get(
                f"{base_url}/api/v2/app/version",
                timeout=5
            )
            if version_response.status_code == 200:
                version = version_response.text
                return True, f"Connected successfully (version: {version})"
            return True, "Connected and authenticated"
        elif response.status_code == 403:
            return False, "Authentication failed - check QB_USERNAME and QB_PASSWORD"
        else:
            return False, f"Unexpected response: {response.status_code} - {response.text}"
            
    except requests.exceptions.ConnectionError:
        return False, f"Cannot connect to {masked_host}:{QB_PORT} - connection refused"
    except requests.exceptions.Timeout:
        return False, f"Connection timeout to {masked_host}:{QB_PORT}"
    except Exception as e:
        return False, f"Error: {str(e)}"


def check_proxy_pool_status() -> Tuple[bool, str]:
    """
    Check proxy pool status - verify proxies are configured and not all banned.
    
    Returns:
        Tuple of (success, message)
    """
    try:
        from utils.proxy_ban_manager import get_ban_manager
        
        if not PROXY_POOL:
            if PROXY_MODE == 'pool':
                return False, "PROXY_MODE is 'pool' but PROXY_POOL is empty"
            return True, "No proxies configured (direct connection mode)"
        
        total_proxies = len(PROXY_POOL)
        ban_manager = get_ban_manager()
        banned_proxies = ban_manager.get_banned_proxies()
        banned_names = [p.proxy_name for p in banned_proxies]
        
        available_count = 0
        for proxy in PROXY_POOL:
            proxy_name = proxy.get('name', 'Unnamed')
            if proxy_name not in banned_names:
                available_count += 1
        
        if available_count == 0:
            return False, f"All {total_proxies} proxies are banned! Pipeline cannot proceed."
        elif available_count < total_proxies:
            banned_count = total_proxies - available_count
            return True, f"{available_count}/{total_proxies} proxies available ({banned_count} banned)"
        else:
            return True, f"All {total_proxies} proxies available"
            
    except Exception as e:
        return False, f"Error checking proxy status: {str(e)}"


def check_smtp_connection() -> Tuple[bool, str]:
    """
    Check SMTP server connectivity (without authentication).
    
    Returns:
        Tuple of (success, message)
    """
    masked_server = mask_ip_address(SMTP_SERVER)
    
    try:
        logger.info(f"Testing SMTP connection to {masked_server}:{SMTP_PORT}...")
        
        # Just test TCP connectivity, don't actually authenticate
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        result = sock.connect_ex((SMTP_SERVER, SMTP_PORT))
        sock.close()
        
        if result == 0:
            return True, f"SMTP server {masked_server}:{SMTP_PORT} is reachable"
        else:
            return False, f"Cannot connect to SMTP server {masked_server}:{SMTP_PORT}"
            
    except socket.timeout:
        return False, f"Connection timeout to SMTP server {masked_server}:{SMTP_PORT}"
    except Exception as e:
        return False, f"Error: {str(e)}"


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Health Check for JavDB Pipeline')
    parser.add_argument('--check-smtp', action='store_true',
                        help='Also check SMTP server connectivity')
    parser.add_argument('--use-proxy', action='store_true',
                        help='Check proxy pool status')
    return parser.parse_args()


def main():
    args = parse_arguments()
    
    logger.info("=" * 60)
    logger.info("HEALTH CHECK - Pre-flight Verification")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    all_passed = True
    results: List[Tuple[str, bool, str]] = []
    
    # Check 1: qBittorrent (non-critical - just informational)
    logger.info("")
    logger.info("[1/3] Checking qBittorrent connectivity...")
    qb_success, qb_message = check_qbittorrent_connection()
    results.append(("qBittorrent", qb_success, qb_message))
    if qb_success:
        logger.info(f"  ✓ {qb_message}")
    else:
        logger.warning(f"  ⚠ {qb_message} (non-critical - uploader step may fail)")
        # qBittorrent failure is non-critical - spider can still run
    
    # Check 2: Proxy Pool (if configured or --use-proxy)
    logger.info("")
    logger.info("[2/3] Checking proxy pool status...")
    proxy_success, proxy_message = check_proxy_pool_status()
    results.append(("Proxy Pool", proxy_success, proxy_message))
    if proxy_success:
        logger.info(f"  ✓ {proxy_message}")
    else:
        logger.error(f"  ✗ {proxy_message}")
        # Proxy failure is critical if PROXY_MODE is 'pool'
        if PROXY_MODE == 'pool':
            all_passed = False
        else:
            logger.warning("  (Non-critical in single/no proxy mode)")
    
    # Check 3: SMTP (optional)
    logger.info("")
    if args.check_smtp:
        logger.info("[3/3] Checking SMTP server connectivity...")
        smtp_success, smtp_message = check_smtp_connection()
        results.append(("SMTP", smtp_success, smtp_message))
        if smtp_success:
            logger.info(f"  ✓ {smtp_message}")
        else:
            logger.warning(f"  ⚠ {smtp_message} (non-critical)")
            # SMTP failure is not critical - just a warning
    else:
        logger.info("[3/3] SMTP check skipped (use --check-smtp to enable)")
        results.append(("SMTP", True, "Skipped"))
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("HEALTH CHECK SUMMARY")
    logger.info("=" * 60)
    
    for name, success, message in results:
        status = "✓ PASS" if success else "✗ FAIL"
        logger.info(f"  {status}: {name} - {message}")
    
    logger.info("")
    if all_passed:
        logger.info("✓ All critical health checks PASSED")
        logger.info("=" * 60)
        return 0
    else:
        logger.error("✗ One or more critical health checks FAILED")
        logger.error("  Pipeline execution may fail. Please fix the issues above.")
        logger.info("=" * 60)
        return 1


if __name__ == '__main__':
    sys.exit(main())

