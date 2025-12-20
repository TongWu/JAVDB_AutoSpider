#!/bin/bash

# ============================================================
# JAVDB AutoSpider Docker Entrypoint Script
# ============================================================
# This script sets up cron jobs based on environment variables
# and starts the container
# ============================================================

set -e

echo "=========================================="
echo "JAVDB AutoSpider Container Starting"
echo "=========================================="
echo "Timestamp: $(date)"
echo ""

# ============================================================
# Validate required files
# ============================================================
echo "Checking required files..."

if [ ! -f "/app/config.py" ]; then
    echo "ERROR: config.py not found!"
    echo "Please mount your config.py file to /app/config.py"
    echo "Example: -v ./config.py:/app/config.py:ro"
    exit 1
fi

echo "✓ config.py found"
echo ""

# ============================================================
# Set default values for enable flags
# ============================================================
ENABLE_SPIDER=${ENABLE_SPIDER:-true}
ENABLE_PIPELINE=${ENABLE_PIPELINE:-true}
ENABLE_QBTORRENT=${ENABLE_QBTORRENT:-true}
ENABLE_PIKPAK=${ENABLE_PIKPAK:-true}

# ============================================================
# Create crontab file
# ============================================================
echo "Setting up cron jobs..."
CRONTAB_FILE=/etc/cron.d/javdb-spider

# Create empty crontab file
echo "# JAVDB AutoSpider Cron Jobs" > $CRONTAB_FILE
echo "# Generated at $(date)" >> $CRONTAB_FILE
echo "SHELL=/bin/bash" >> $CRONTAB_FILE
echo "PATH=/usr/local/bin:/usr/bin:/bin" >> $CRONTAB_FILE
echo "" >> $CRONTAB_FILE

# ============================================================
# Add Spider cron job
# ============================================================
if [ "$ENABLE_SPIDER" = "true" ] && [ ! -z "$CRON_SPIDER" ]; then
    echo "Adding Spider cron job: $CRON_SPIDER"
    SPIDER_CMD=${SPIDER_COMMAND:-"cd /app && /usr/local/bin/python scripts/spider.py >> /var/log/cron.log 2>&1"}
    echo "$CRON_SPIDER $SPIDER_CMD" >> $CRONTAB_FILE
else
    echo "Spider cron job disabled or not configured"
fi

# ============================================================
# Add Pipeline cron job
# ============================================================
if [ "$ENABLE_PIPELINE" = "true" ] && [ ! -z "$CRON_PIPELINE" ]; then
    echo "Adding Pipeline cron job: $CRON_PIPELINE"
    PIPELINE_CMD=${PIPELINE_COMMAND:-"cd /app && /usr/local/bin/python pipeline.py >> /var/log/cron.log 2>&1"}
    echo "$CRON_PIPELINE $PIPELINE_CMD" >> $CRONTAB_FILE
else
    echo "Pipeline cron job disabled or not configured"
fi

# ============================================================
# Add qBittorrent Uploader cron job
# ============================================================
if [ "$ENABLE_QBTORRENT" = "true" ] && [ ! -z "$CRON_QBTORRENT" ]; then
    echo "Adding qBittorrent Uploader cron job: $CRON_QBTORRENT"
    QBTORRENT_CMD=${QBTORRENT_COMMAND:-"cd /app && /usr/local/bin/python scripts/qb_uploader.py >> /var/log/cron.log 2>&1"}
    echo "$CRON_QBTORRENT $QBTORRENT_CMD" >> $CRONTAB_FILE
else
    echo "qBittorrent Uploader cron job disabled or not configured"
fi

# ============================================================
# Add PikPak Bridge cron job
# ============================================================
if [ "$ENABLE_PIKPAK" = "true" ] && [ ! -z "$CRON_PIKPAK" ]; then
    echo "Adding PikPak Bridge cron job: $CRON_PIKPAK"
    PIKPAK_CMD=${PIKPAK_COMMAND:-"cd /app && /usr/local/bin/python scripts/pikpak_bridge.py >> /var/log/cron.log 2>&1"}
    echo "$CRON_PIKPAK $PIKPAK_CMD" >> $CRONTAB_FILE
else
    echo "PikPak Bridge cron job disabled or not configured"
fi

# ============================================================
# Add custom cron jobs (if defined)
# ============================================================
if [ ! -z "$CRON_CLEANUP" ] && [ ! -z "$CLEANUP_COMMAND" ]; then
    echo "Adding Cleanup cron job: $CRON_CLEANUP"
    echo "$CRON_CLEANUP $CLEANUP_COMMAND" >> $CRONTAB_FILE
fi

if [ ! -z "$CRON_MONITOR" ] && [ ! -z "$MONITOR_COMMAND" ]; then
    echo "Adding Monitor cron job: $CRON_MONITOR"
    echo "$CRON_MONITOR $MONITOR_COMMAND" >> $CRONTAB_FILE
fi

# ============================================================
# Add newline at end of crontab (required by cron)
# ============================================================
echo "" >> $CRONTAB_FILE

# ============================================================
# Set proper permissions for crontab
# ============================================================
chmod 0644 $CRONTAB_FILE
crontab $CRONTAB_FILE

echo ""
echo "=========================================="
echo "Crontab Configuration"
echo "=========================================="
cat $CRONTAB_FILE
echo ""

# ============================================================
# Display active cron jobs
# ============================================================
echo "=========================================="
echo "Active Cron Jobs"
echo "=========================================="
crontab -l
echo ""

# ============================================================
# Start cron service
# ============================================================
echo "=========================================="
echo "Starting Cron Service"
echo "=========================================="
echo "Container is now running"
echo "Logs will be written to /var/log/cron.log"
echo "To view logs: docker logs -f javdb-spider"
echo "=========================================="
echo ""

# Execute the command passed to the container
exec "$@"

