import csv
import requests
import logging
from datetime import datetime
import time
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[4]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import unified configuration
from javdb.infra.config import cfg

from javdb.integrations.qb.uploader.options import QbUploaderOptions
from javdb.integrations.qb.uploader.result import QbUploaderResult

QB_HOST = cfg('QB_HOST', 'your_qbittorrent_ip')
QB_PORT = cfg('QB_PORT', 'your_qbittorrent_port')
QB_USERNAME = cfg('QB_USERNAME', 'your_qbittorrent_username')
QB_PASSWORD = cfg('QB_PASSWORD', 'your_qbittorrent_password')
TORRENT_CATEGORY = cfg('TORRENT_CATEGORY', 'JavDB')
TORRENT_CATEGORY_ADHOC = cfg('TORRENT_CATEGORY_ADHOC', 'Ad Hoc')
TORRENT_SAVE_PATH = cfg('TORRENT_SAVE_PATH', '')
AUTO_START = cfg('AUTO_START', True)
SKIP_CHECKING = cfg('SKIP_CHECKING', False)
REQUEST_TIMEOUT = cfg('REQUEST_TIMEOUT', 30)
DELAY_BETWEEN_ADDITIONS = cfg('DELAY_BETWEEN_ADDITIONS', 1)
UPLOADER_LOG_FILE = cfg('UPLOADER_LOG_FILE', 'logs/qb_uploader.log')
DAILY_REPORT_DIR = cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = cfg('AD_HOC_DIR', 'reports/AdHoc')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
PROXY_HTTP = cfg('PROXY_HTTP', None)
PROXY_HTTPS = cfg('PROXY_HTTPS', None)
PROXY_MODULES = cfg('PROXY_MODULES', ['spider'])
GIT_USERNAME = cfg('GIT_USERNAME', 'github-actions')
GIT_PASSWORD = cfg('GIT_PASSWORD', '')
GIT_REPO_URL = cfg('GIT_REPO_URL', '')
GIT_BRANCH = cfg('GIT_BRANCH', 'main')

# Proxy pool
PROXY_MODE = cfg('PROXY_MODE', 'pool')
PROXY_POOL = cfg('PROXY_POOL', [])
PROXY_POOL_MAX_FAILURES = cfg('PROXY_POOL_MAX_FAILURES', 3)

# Import history manager functions
try:
    from javdb.storage.history_manager import is_downloaded_torrent
except ImportError:
    # Fallback function if import fails
    def is_downloaded_torrent(torrent_content):
        """Check if torrent content contains downloaded indicator"""
        return torrent_content.strip().startswith("[DOWNLOADED]")

# Import path helper for dated subdirectories
from javdb.infra.paths import get_dated_report_path, get_dated_subdir, find_latest_report_in_dated_dirs
from javdb.proxy.policy import (
    describe_proxy_override,
    should_proxy_module,
)

# Configure logging
from javdb.infra.logging import setup_logging, get_logger
setup_logging(UPLOADER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Workflow adapters (IMP-ADR015-01)
from javdb.workflow.artifact_inputs import (
    resolve_qb_uploader_csv_path,
    read_torrent_csv,
)
from javdb.workflow.stats_sink import UploaderStats, save_uploader_stats
from javdb.workflow.git_side_effects import GitCommitRequest, commit_workflow_outputs

# ``has_git_credentials`` mirrors the legacy "Committing…/Skipping…" log gate;
# the actual commit + flush is handled by ``commit_workflow_outputs``.
from javdb.infra.git_helper import has_git_credentials

# Import masking utilities
from javdb.infra.masking import mask_error, mask_username

# Import proxy pool
from javdb.proxy.pool import ProxyPool, create_proxy_pool_from_config

# Import proxy helper from request handler
from javdb.infra.request import ProxyHelper, create_proxy_helper_from_config
from javdb.integrations.qb.config import (
    qb_allow_insecure_http,
    qb_base_url_candidates,
    masked_qb_base_url,
    qb_verify_tls,
)

# Global proxy pool instance
global_proxy_pool = None

# Global proxy helper instance
global_proxy_helper = None

# qBittorrent configuration
QB_ALLOW_INSECURE_HTTP = qb_allow_insecure_http()
QB_BASE_URL_CANDIDATES = qb_base_url_candidates(
    allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
)
QB_BASE_URL = QB_BASE_URL_CANDIDATES[0]
QB_MASKED_URL = masked_qb_base_url(
    QB_BASE_URL,
    allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
)
QB_VERIFY_TLS = qb_verify_tls()


def _set_active_qb_base_url(base_url):
    """Persist the qBittorrent endpoint that proved reachable."""
    global QB_BASE_URL, QB_MASKED_URL, QB_ALLOW_INSECURE_HTTP
    QB_BASE_URL = base_url.rstrip('/')
    # HTTPS primary may fail (e.g. self-signed); HTTP fallback is still plain HTTP — align flag for masking and later calls.
    if urlsplit(QB_BASE_URL).scheme == 'http':
        QB_ALLOW_INSECURE_HTTP = True
    QB_MASKED_URL = masked_qb_base_url(
        QB_BASE_URL,
        allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
    )


def _ordered_qb_base_urls():
    """Try the last known-good URL first, then the remaining candidates."""
    ordered = []
    if QB_BASE_URL:
        ordered.append(QB_BASE_URL)
    for candidate in QB_BASE_URL_CANDIDATES:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def get_proxies_dict(module_name, use_proxy_flag):
    """
    Get proxies dictionary for requests if module should use proxy.
    Delegated to global_proxy_helper.

    Args:
        module_name: Name of the module
        use_proxy_flag: Whether --use-proxy flag is enabled

    Returns:
        dict or None: Proxies dictionary for requests, or None
    """
    if global_proxy_helper is None:
        logger.warning(f"[{module_name}] Proxy helper not initialized")
        return None

    return global_proxy_helper.get_proxies_dict(module_name, use_proxy_flag)

def find_latest_adhoc_csv():
    """
    Find the most recently created/modified AdHoc CSV file.

    This function handles the case where spider generates a custom-named CSV file
    (e.g., Javdb_AdHoc_actors_森日向子_20251224.csv) and qb_uploader needs to find it.

    Note: This function uses wildcard patterns (not date-specific) to handle
    cross-midnight scenarios where spider runs before midnight but qb_uploader
    runs after midnight. It relies on file modification time to find the most
    recent file.

    Returns:
        str or None: Path to the most recent AdHoc CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent AdHoc CSV file
    # Pattern: Javdb_AdHoc_*.csv (any date)
    # This handles cross-midnight scenarios where spider generates file on day N
    # but qb_uploader runs on day N+1
    adhoc_pattern = 'Javdb_AdHoc_*.csv'

    latest_file = find_latest_report_in_dated_dirs(AD_HOC_DIR, adhoc_pattern)

    if latest_file:
        return latest_file

    # Fallback: try to find any CSV file (legacy pattern)
    legacy_pattern = 'Javdb_*.csv'
    return find_latest_report_in_dated_dirs(AD_HOC_DIR, legacy_pattern)


def find_latest_daily_csv():
    """
    Find the most recently created/modified Daily CSV file.

    This function uses wildcard patterns (not date-specific) to handle
    cross-midnight scenarios where spider runs before midnight but qb_uploader
    runs after midnight. It relies on file modification time to find the most
    recent file.

    Returns:
        str or None: Path to the most recent Daily CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent Daily CSV file
    # Pattern: Javdb_TodayTitle_*.csv (any date)
    daily_pattern = 'Javdb_TodayTitle_*.csv'

    return find_latest_report_in_dated_dirs(DAILY_REPORT_DIR, daily_pattern)


def get_csv_filename(mode='daily'):
    """
    Get the CSV filename for the specified mode with dated subdirectory (YYYY/MM).

    This function auto-discovers the most recent CSV file using wildcard patterns,
    which handles cross-midnight scenarios where spider runs before midnight but
    qb_uploader runs after midnight.

    For adhoc mode: looks for Javdb_AdHoc_*.csv files
    For daily mode: looks for Javdb_TodayTitle_*.csv files

    Args:
        mode: 'daily' or 'adhoc'

    Returns:
        str: Path to the CSV file (auto-discovered or fallback to date-based naming)
    """
    current_date = datetime.now().strftime("%Y%m%d")

    if mode == 'adhoc':
        # Try to auto-discover the latest adhoc CSV
        latest_adhoc = find_latest_adhoc_csv()
        if latest_adhoc:
            logger.info(f"Auto-discovered adhoc CSV: {latest_adhoc}")
            return latest_adhoc
        else:
            # Fallback to default naming if no file found
            logger.warning("No adhoc CSV found, using default naming pattern with current date")
            csv_filename = f'Javdb_TodayTitle_{current_date}.csv'
            return get_dated_report_path(AD_HOC_DIR, csv_filename)
    else:
        # Try to auto-discover the latest daily CSV
        latest_daily = find_latest_daily_csv()
        if latest_daily:
            logger.info(f"Auto-discovered daily CSV: {latest_daily}")
            return latest_daily
        else:
            # Fallback to default naming if no file found
            logger.warning("No daily CSV found, using default naming pattern with current date")
            csv_filename = f'Javdb_TodayTitle_{current_date}.csv'
            return get_dated_report_path(DAILY_REPORT_DIR, csv_filename)

def test_qbittorrent_connection(use_proxy=False):
    """Test if qBittorrent is accessible.

    Thin wrapper around ``qb_client.try_ping_base_urls``. ``requests.get``
    is passed in as the HTTP callable so tests that patch
    ``apps.cli.qb.uploader.requests.get`` continue to work. On success the
    resolved base URL is persisted via ``_set_active_qb_base_url`` (which
    also flips ``QB_ALLOW_INSECURE_HTTP`` when an HTTP fallback succeeds)."""
    from javdb.integrations.qb.client import try_ping_base_urls

    proxies = get_proxies_dict('qbittorrent', use_proxy)
    url, _ = try_ping_base_urls(
        _ordered_qb_base_urls(),
        get_fn=requests.get,
        allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
        proxies=proxies,
        timeout=10,
        verify=QB_VERIFY_TLS,
    )
    if url:
        _set_active_qb_base_url(url)
        return True
    return False


def login_to_qbittorrent(session, use_proxy=False):
    """Login to qBittorrent web UI.

    Thin wrapper around ``qb_client.try_login_base_urls``. The ``session``
    argument stays in the signature for backwards compatibility — we pass
    its ``post`` method as the HTTP callable so existing tests that mock
    ``session.post`` continue to work."""
    from javdb.integrations.qb.client import (
        LOGIN_SUCCESS,
        LOGIN_REJECTED,
        try_login_base_urls,
    )

    proxies = get_proxies_dict('qbittorrent', use_proxy)
    outcome, url, _ = try_login_base_urls(
        _ordered_qb_base_urls(),
        QB_USERNAME,
        QB_PASSWORD,
        post_fn=session.post,
        allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
        proxies=proxies,
        timeout=REQUEST_TIMEOUT,
        verify=QB_VERIFY_TLS,
    )
    if outcome == LOGIN_SUCCESS and url:
        _set_active_qb_base_url(url)
        return True
    if outcome == LOGIN_REJECTED:
        logger.error("Please check your username and password in config.py")
    return False


from javdb.integrations.qb.client import (
    extract_hash_from_magnet,
    is_torrent_exists,
)


def _wrap_session_as_client(session, use_proxy=False):
    """Wrap ``qb_uploader``'s already-logged-in ``requests.Session`` into
    a :class:`QBittorrentClient` so it can reuse the shared helpers
    without doing a second login."""
    from javdb.integrations.qb.client import QBittorrentClient
    return QBittorrentClient.from_existing_session(
        session,
        base_url=QB_BASE_URL,
        proxies=get_proxies_dict('qbittorrent', use_proxy),
        request_timeout=REQUEST_TIMEOUT,
    )


def get_existing_torrents(session, use_proxy=False):
    """
    Get all existing torrents from qBittorrent.

    Thin wrapper around :meth:`QBittorrentClient.get_existing_hashes`.

    Args:
        session: Requests session with login cookies
        use_proxy: Whether to use proxy

    Returns:
        set: Set of torrent hashes (lowercase) that are not in error state
    """
    client = _wrap_session_as_client(session, use_proxy=use_proxy)
    existing_hashes = client.get_existing_hashes()
    logger.info(
        f"Found {len(existing_hashes)} existing torrents in qBittorrent "
        f"(excluding errors)"
    )
    return existing_hashes


def add_torrent_to_qbittorrent(
    session, magnet_link, title, mode='daily', use_proxy=False, category_override=None
):
    """Add a torrent to qBittorrent.

    Thin wrapper around :meth:`QBittorrentClient.add_torrent` that wires
    in the uploader's global defaults (save path, auto-TMM, skip
    checking, content layout, ratio/seed-time limits, paused state).
    """
    if category_override:
        category = category_override
    else:
        category = TORRENT_CATEGORY_ADHOC if mode == 'adhoc' else TORRENT_CATEGORY

    client = _wrap_session_as_client(session, use_proxy=use_proxy)

    try:
        logger.debug(f"Adding torrent: {title} with category: {category}")
        ok = client.add_torrent(
            magnet_link=magnet_link,
            name=title,
            category=category,
            save_path=TORRENT_SAVE_PATH,
            auto_tmm=True,
            skip_checking=SKIP_CHECKING,
            content_layout='Original',
            ratio_limit='-2',
            seeding_time_limit='-2',
            paused=not AUTO_START,
        )
        if ok:
            logger.debug(
                f"Successfully added torrent: {title} to category: {category}"
            )
            return True
        logger.error(f"Failed to add torrent '{title}'")
        return False
    except requests.RequestException as e:
        logger.error(f"Error adding torrent '{title}': {e}")
        return False

def read_csv_file(filename):
    """Read the CSV file and extract all magnet links, skipping downloaded torrents.

    Returns:
        tuple: (torrents_list, ok_bool)
            - torrents_list: List of torrent dictionaries (may be partial
              when ``ok_bool`` is False — useful for diagnostics only).
            - ok_bool: True iff the file was read end-to-end without
              raising. False on both "file not found" and "file existed
              but read failed". P1 widened the False case so partial
              CSV reads no longer get treated as success by the caller.
    """
    torrents = []
    skipped_count = 0

    # Delegate the raw row read to the workflow adapter so CSV resolution and
    # reading share one implementation across the workflow. ``read_torrent_csv``
    # returns ``([], False)`` when the file is missing and ``(partial, False)``
    # when an exception is raised mid-read; both map to the legacy ok=False.
    rows, ok = read_torrent_csv(filename)

    if not ok and not rows:
        logger.error(f"CSV file not found: {filename}")
        logger.info("Make sure you have run the spider script first to generate the CSV file")
        return torrents, False  # Return tuple indicating file not found

    # Iterate over the four torrent columns per row.  The earlier
    # implementation used per-column ``continue`` inside this for-loop,
    # which short-circuited the *remaining* columns whenever the first
    # populated column was already-downloaded — so a row that had
    # ``hacked_subtitle`` (downloaded) plus ``subtitle`` (not
    # downloaded) silently dropped the second torrent.  Iterating over
    # a tuple of (column, label, type) lets per-column skip stay local.
    torrent_columns = (
        ('hacked_subtitle', 'Hacked+Subtitle', 'hacked_subtitle'),
        ('hacked_no_subtitle', 'Hacked-NoSubtitle', 'hacked_no_subtitle'),
        ('subtitle', 'Subtitle', 'subtitle'),
        ('no_subtitle', 'NoSubtitle', 'no_subtitle'),
    )
    for row in rows:
        href = row.get('href', '')
        video_code = row.get('video_code', '')

        for col, label, ttype in torrent_columns:
            raw = row.get(col)
            if not raw:
                continue
            magnet = raw.strip()
            if not magnet:
                continue
            if is_downloaded_torrent(magnet):
                logger.debug(
                    f"Skipping downloaded torrent: {video_code} [{label}]"
                )
                skipped_count += 1
                continue
            torrents.append({
                'magnet': magnet,
                'title': f"{video_code} [{label}]",
                'page': row.get('page', 'N/A'),
                'type': ttype,
                'href': href,
                'video_code': video_code,
            })

    if not ok:
        # P1: partial CSV reads (file existed but read raised) surface as
        # ``(partial_rows, False)`` so the workflow can fail-fast and the
        # operator can re-fetch the upstream CSV.
        logger.error("Error reading CSV file: read did not complete")
        return torrents, False

    logger.info(f"Found {len(torrents)} torrent links in {filename}")
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} already downloaded torrents")
    return torrents, True  # File exists

def initialize_proxy_helper(proxy_override):
    """Initialize global proxy pool and proxy helper."""
    global global_proxy_pool, global_proxy_helper

    if proxy_override is False:
        global_proxy_pool = None
        # When use_proxy=False, don't pass proxy configs to ensure no proxy is used
        global_proxy_helper = create_proxy_helper_from_config(
            proxy_pool=None,
            proxy_modules=PROXY_MODULES,
            proxy_mode=PROXY_MODE,
            proxy_http=None,
            proxy_https=None
        )
        return

    # Check if we have PROXY_POOL configuration
    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Proxy pool initialized successfully")
        elif PROXY_MODE == 'single':
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Single proxy initialized: {PROXY_POOL[0].get('name', 'Main-Proxy')}")
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        legacy_proxy = {
            'name': 'Legacy-Proxy',
            'http': PROXY_HTTP,
            'https': PROXY_HTTPS
        }
        global_proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            max_failures=PROXY_POOL_MAX_FAILURES
        )
    else:
        logger.warning("Proxy enabled but no proxy configuration found")
        global_proxy_pool = None

    # Create proxy helper with the initialized pool
    global_proxy_helper = create_proxy_helper_from_config(
        proxy_pool=global_proxy_pool,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS
    )
    logger.info("Proxy helper initialized successfully")


def run_uploader(options: QbUploaderOptions) -> QbUploaderResult:
    """Upload torrents from a resolved CSV into qBittorrent.

    Behaviour-preserving extraction of the former ``main()`` flow. Instead of
    calling ``sys.exit`` it returns a :class:`QbUploaderResult` whose
    ``exit_code`` reproduces the original CLI return codes.
    """
    import atexit
    from javdb.storage.db import close_db
    atexit.register(close_db)

    mode = options.mode
    proxy_override = options.proxy_override
    use_proxy = proxy_override
    proxy_active = should_proxy_module('qbittorrent', proxy_override, PROXY_MODULES, proxy_mode=PROXY_MODE)
    category_override = options.category
    logger.info("Starting qBittorrent uploader...")
    if category_override:
        logger.info(f"Using custom category: {category_override}")

    # Initialize proxy helper
    initialize_proxy_helper(proxy_override)

    logger.info(f"Proxy policy for qBittorrent: {describe_proxy_override(proxy_override)}")

    if proxy_active:
        if global_proxy_helper is not None:
            stats = global_proxy_helper.get_statistics()

            if PROXY_MODE == 'pool':
                logger.info(f"PROXY POOL MODE for qBittorrent: {stats['total_proxies']} proxies with automatic failover")
            elif PROXY_MODE == 'single':
                logger.info(f"SINGLE PROXY MODE for qBittorrent: Using main proxy only")
                if stats['total_proxies'] > 0 and stats['proxies']:
                    main_proxy_name = stats['proxies'][0]['name']
                    logger.info(f"Main proxy: {main_proxy_name}")
        else:
            logger.warning("PROXY ENABLED: But no proxy configured")
    else:
        logger.info("Proxy disabled for qBittorrent requests")

    # Test qBittorrent connection first
    if not test_qbittorrent_connection(use_proxy):
        logger.error("Cannot connect to qBittorrent. Please check:")
        logger.error("1. qBittorrent is running")
        logger.error("2. Web UI is enabled")
        logger.error("3. Host and port settings in config.py")
        return QbUploaderResult(error_reason="qb-unreachable")

    # Get CSV filename via the shared workflow resolver. The explicit-path /
    # explicit-name branches map 1:1 onto the legacy ``--input-file`` handling;
    # for the "latest" branch we defer to ``get_csv_filename`` which carries the
    # auto-discovery + fallback logging and the date-based fallback path that
    # the adapter intentionally leaves out.
    resolution = resolve_qb_uploader_csv_path(
        mode=mode,
        input_file=options.input_file,
        daily_report_dir=DAILY_REPORT_DIR,
        adhoc_dir=AD_HOC_DIR,
        dated_path_resolver=get_dated_report_path,
        latest_daily_finder=find_latest_daily_csv,
        latest_adhoc_finder=find_latest_adhoc_csv,
    )
    if resolution.source == "latest":
        csv_filename = get_csv_filename(mode)
        logger.info(f"Looking for CSV file: {csv_filename}")
    else:
        csv_filename = resolution.path
        logger.info(f"Using specified input file: {csv_filename}")

    # Read torrent links from CSV. P1: ``csv_ok`` is False for both
    # "file not found" and "file existed but read raised" — the second
    # case used to be silently swallowed and the partial subset was
    # uploaded.
    torrents, csv_ok = read_csv_file(csv_filename)

    if not csv_ok:
        logger.error(
            "CSV %s could not be read end-to-end (missing or partial); "
            "refusing to upload to qBittorrent based on incomplete data.",
            csv_filename,
        )
        logger.error(
            "Re-run the spider step that produces this CSV, or supply "
            "--csv-input with a known-good copy."
        )
        return QbUploaderResult(csv_path=csv_filename, csv_ok=False)

    if not torrents:
        logger.warning("No torrent links found in CSV file")
        # File exists but no torrents to add - this is not an error, just no work to do
        return QbUploaderResult(csv_path=csv_filename, csv_ok=True)

    # Create session for qBittorrent
    session = requests.Session()

    # Login to qBittorrent
    if not login_to_qbittorrent(session, use_proxy):
        logger.error("Failed to login to qBittorrent. Please check username and password.")
        return QbUploaderResult(csv_path=csv_filename, error_reason="qb-login-failed")

    # Get existing torrents to check for duplicates
    existing_hashes = get_existing_torrents(session, use_proxy)

    # Add torrents to qBittorrent
    hacked_subtitle_count = 0
    hacked_no_subtitle_count = 0
    subtitle_count = 0
    no_subtitle_count = 0
    failed_count = 0
    duplicate_count = 0
    total_torrents = len(torrents)

    logger.info(f"Starting to add {total_torrents} torrents to qBittorrent...")

    for i, torrent in enumerate(torrents, 1):
        # Check if torrent already exists in qBittorrent
        if is_torrent_exists(torrent['magnet'], existing_hashes):
            logger.info(f"[{i}/{total_torrents}] Skipping (already in qBittorrent): {torrent['title']}")
            duplicate_count += 1
            continue

        logger.info(f"[{i}/{total_torrents}] Adding: {torrent['title']}")

        success = add_torrent_to_qbittorrent(session, torrent['magnet'], torrent['title'], mode, use_proxy, category_override)

        if success:
            if torrent['type'] == 'hacked_subtitle':
                hacked_subtitle_count += 1
            elif torrent['type'] == 'hacked_no_subtitle':
                hacked_no_subtitle_count += 1
            elif torrent['type'] == 'subtitle':
                subtitle_count += 1
            elif torrent['type'] == 'no_subtitle':
                no_subtitle_count += 1

            # Add newly added torrent hash to existing set to avoid re-adding in same session
            new_hash = extract_hash_from_magnet(torrent['magnet'])
            if new_hash:
                existing_hashes.add(new_hash)
        else:
            failed_count += 1

        # Small delay between additions
        time.sleep(DELAY_BETWEEN_ADDITIONS)

    # Generate summary
    successfully_added = hacked_subtitle_count + hacked_no_subtitle_count + subtitle_count + no_subtitle_count
    attempted = total_torrents - duplicate_count

    logger.info("=" * 50)
    logger.info("UPLOAD SUMMARY")
    logger.info("=" * 50)
    logger.info(f"CSV file: {csv_filename}")
    logger.info(f"Total torrents in CSV: {total_torrents}")
    logger.info(f"Skipped (already in qBittorrent): {duplicate_count}")
    logger.info(f"Attempted to add: {attempted}")
    logger.info(f"Successfully added: {successfully_added}")
    logger.info(f"  - Hacked subtitle torrents: {hacked_subtitle_count}")
    logger.info(f"  - Hacked no subtitle torrents: {hacked_no_subtitle_count}")
    logger.info(f"  - Subtitle torrents: {subtitle_count}")
    logger.info(f"  - No subtitle torrents: {no_subtitle_count}")
    logger.info(f"Failed to add: {failed_count}")
    if attempted > 0:
        logger.info(f"Success rate: {(successfully_added/attempted*100):.1f}%")
    else:
        logger.info("Success rate: N/A (all torrents already existed)")
    logger.info("=" * 50)

    # Save uploader stats to SQLite if session_id provided
    _session_id = options.session_id
    if _session_id:
        _rate = (successfully_added / attempted * 100) if attempted > 0 else 0.0
        sink = save_uploader_stats(_session_id, UploaderStats(
            total_torrents=total_torrents,
            duplicate_count=duplicate_count,
            attempted=attempted,
            successfully_added=successfully_added,
            failed_count=failed_count,
            hacked_sub=hacked_subtitle_count,
            hacked_nosub=hacked_no_subtitle_count,
            subtitle_count=subtitle_count,
            no_subtitle_count=no_subtitle_count,
            success_rate=_rate,
        ))
        if sink.saved:
            logger.info(f"Uploader stats saved to {sink.backend} backend (session_id={_session_id})")
        elif sink.error:
            logger.warning(f"Failed to save uploader stats to db backend: {sink.error}")

    # Git commit uploader results (only if credentials are available).
    # ``commit_workflow_outputs`` re-checks credentials and flushes log
    # handlers internally before committing; we mirror the legacy log lines
    # (the "Committing…" notice is emitted *before* the commit so it lands in
    # the flushed-and-committed log).
    from_pipeline = options.from_pipeline

    if has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing uploader results...")
    else:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")

    commit_message = f"Auto-commit: Uploader results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    commit_workflow_outputs(GitCommitRequest(
        files_to_add=['logs/'],
        commit_message=commit_message,
        from_pipeline=from_pipeline,
        git_username=GIT_USERNAME,
        git_password=GIT_PASSWORD,
        git_repo_url=GIT_REPO_URL,
        git_branch=GIT_BRANCH,
    ))

    result = QbUploaderResult(
        total_torrents=total_torrents,
        duplicate_count=duplicate_count,
        attempted=attempted,
        successfully_added=successfully_added,
        failed_count=failed_count,
        hacked_subtitle_count=hacked_subtitle_count,
        hacked_no_subtitle_count=hacked_no_subtitle_count,
        subtitle_count=subtitle_count,
        no_subtitle_count=no_subtitle_count,
        csv_path=csv_filename,
        csv_ok=True,
    )

    # Log error if all torrent additions failed (when there were attempts);
    # the exit code is carried by ``result.exit_code``.
    if attempted > 0 and successfully_added == 0:
        logger.error("All torrent additions failed!")

    return result
