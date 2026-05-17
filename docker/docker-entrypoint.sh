#!/bin/bash

# ============================================================
# JAVDB AutoSpider Docker Entrypoint Script
# ============================================================
# Sets up cron jobs from environment variables and starts the
# container.  Runs as root (required for the cron daemon), but
# every cron job is executed as the non-root ``spider`` user.
# ============================================================

set -e

# ============================================================
# Check if we're running cron (default command) or another command
# ============================================================
IS_CRON_CMD=false
if [ $# -eq 0 ] || [ "$1" = "cron" ]; then
    IS_CRON_CMD=true
fi

if [ "$IS_CRON_CMD" = true ]; then
    # ============================================================
    # Cron mode: Validate required files and setup cron
    # ============================================================
    echo "=========================================="
    echo "JAVDB AutoSpider Container Starting"
    echo "=========================================="
    echo "Timestamp: $(date)"
    echo ""

    echo "Checking required files..."

    if [ ! -f "/app/config.py" ]; then
        echo "ERROR: config.py not found!"
        echo "Please mount your config.py file to /app/config.py"
        echo "Example: -v ./config.py:/app/config.py:ro"
        exit 1
    fi

    echo "config.py found"
    echo ""

    # ============================================================
    # Set default values for enable flags
    # ============================================================
    ENABLE_SPIDER=${ENABLE_SPIDER:-true}
    ENABLE_PIPELINE=${ENABLE_PIPELINE:-true}
    ENABLE_QBTORRENT=${ENABLE_QBTORRENT:-true}
    ENABLE_PIKPAK=${ENABLE_PIKPAK:-true}

    # ============================================================
    # Build crontab in /etc/cron.d/ (system cron format).
    #
    # Each line includes the run-user (``spider``) between the time
    # spec and the command — the cron daemon forks the job as that
    # user.  Commands are HARDCODED; we deliberately do not expand
    # operator-supplied *_COMMAND environment variables here to
    # prevent arbitrary shell injection into the crontab.
    # ============================================================
    echo "Setting up cron jobs..."
    CRONTAB_FILE=/etc/cron.d/javdb-spider

    {
        echo "# JAVDB AutoSpider Cron Jobs"
        echo "# Generated at $(date)"
        echo "SHELL=/bin/bash"
        echo "PATH=/usr/local/bin:/usr/bin:/bin"
        echo ""
    } > "$CRONTAB_FILE"

    # ── Spider ────────────────────────────────────────────────
    if [ "$ENABLE_SPIDER" = "true" ] && [ -n "$CRON_SPIDER" ]; then
        echo "Adding Spider cron job: $CRON_SPIDER"
        echo "$CRON_SPIDER spider cd /app && /usr/local/bin/python -m apps.cli.spider >> /var/log/cron.log 2>&1" \
            >> "$CRONTAB_FILE"
    else
        echo "Spider cron job disabled or not configured"
    fi

    # ── Pipeline ──────────────────────────────────────────────
    if [ "$ENABLE_PIPELINE" = "true" ] && [ -n "$CRON_PIPELINE" ]; then
        echo "Adding Pipeline cron job: $CRON_PIPELINE"
        echo "$CRON_PIPELINE spider cd /app && /usr/local/bin/python -m apps.cli.pipeline >> /var/log/cron.log 2>&1" \
            >> "$CRONTAB_FILE"
    else
        echo "Pipeline cron job disabled or not configured"
    fi

    # ── qBittorrent Uploader ──────────────────────────────────
    if [ "$ENABLE_QBTORRENT" = "true" ] && [ -n "$CRON_QBTORRENT" ]; then
        echo "Adding qBittorrent Uploader cron job: $CRON_QBTORRENT"
        echo "$CRON_QBTORRENT spider cd /app && /usr/local/bin/python -m apps.cli.qb.uploader >> /var/log/cron.log 2>&1" \
            >> "$CRONTAB_FILE"
    else
        echo "qBittorrent Uploader cron job disabled or not configured"
    fi

    # ── PikPak Bridge ─────────────────────────────────────────
    if [ "$ENABLE_PIKPAK" = "true" ] && [ -n "$CRON_PIKPAK" ]; then
        echo "Adding PikPak Bridge cron job: $CRON_PIKPAK"
        echo "$CRON_PIKPAK spider cd /app && /usr/local/bin/python -m apps.cli.pikpak.bridge >> /var/log/cron.log 2>&1" \
            >> "$CRONTAB_FILE"
    else
        echo "PikPak Bridge cron job disabled or not configured"
    fi

    echo "" >> "$CRONTAB_FILE"

    # ============================================================
    # Set proper permissions for /etc/cron.d/ entry
    # ============================================================
    chmod 0644 "$CRONTAB_FILE"

    echo ""
    echo "=========================================="
    echo "Crontab Configuration"
    echo "=========================================="
    cat "$CRONTAB_FILE"
    echo ""

    echo "=========================================="
    echo "Starting Cron Service"
    echo "=========================================="
    echo "Container is now running"
    echo "Logs will be written to /var/log/cron.log"
    echo "To view logs: docker logs -f javdb-spider"
    echo "=========================================="
    echo ""
fi

# Execute the command passed to the container
exec "$@"
