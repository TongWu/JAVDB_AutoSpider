# qBittorrent Configuration
# Modify these settings according to your qBittorrent setup

# qBittorrent Web UI settings
QB_HOST = '192.168.1.2'
QB_PORT = '12301'
QB_USERNAME = 'admin'  # Change to your qBittorrent username
QB_PASSWORD = 'password'  # Change to your qBittorrent password

# Torrent settings
TORRENT_CATEGORY = 'JavDB'  # Category for all JavDB torrents
TORRENT_SAVE_PATH = ''  # Leave empty for default path
AUTO_START = True  # Set to False to add torrents in paused state
SKIP_CHECKING = False  # Set to True to skip hash checking

# Connection settings
REQUEST_TIMEOUT = 30  # Timeout for API requests in seconds
DELAY_BETWEEN_ADDITIONS = 1  # Delay between adding torrents in seconds
