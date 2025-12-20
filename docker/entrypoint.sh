#!/bin/bash
# JavDB Auto Spider Docker Entrypoint
# This script handles cron job setup and execution

set -e

# Create log directories
mkdir -p /app/logs_volume

# Setup symbolic links for volume mounts if not already done
if [ ! -L /app/logs ]; then
    rm -rf /app/logs
    ln -sf /app/logs_volume /app/logs
fi

# Create data directories if they don't exist
mkdir -p "/app/data/Daily Report" "/app/data/Ad Hoc"

# Check if config.py exists
if [ ! -f /app/config/config.py ]; then
    echo "ERROR: config.py not found in /app/config/"
    echo "Please mount your config folder containing config.py"
    exit 1
fi

# Function to parse .env file and setup cron jobs
setup_cron() {
    if [ ! -f /app/config/.env ]; then
        echo "WARNING: .env file not found in /app/config/"
        echo "No cron jobs will be configured. Running in manual mode."
        return 1
    fi

    echo "Setting up cron jobs from .env file..."
    
    # Create a new crontab file
    CRON_FILE="/tmp/crontab.txt"
    > "$CRON_FILE"
    
    # Add environment variables to crontab
    echo "# JavDB Auto Spider Cron Jobs" >> "$CRON_FILE"
    echo "SHELL=/bin/bash" >> "$CRON_FILE"
    echo "PATH=/usr/local/bin:/usr/bin:/bin" >> "$CRON_FILE"
    echo "PYTHONUNBUFFERED=1" >> "$CRON_FILE"
    echo "" >> "$CRON_FILE"
    
    # Read .env file line by line
    # Expected format: CRON_SCHEDULE="0 8 * * *" COMMAND="python3 pipeline.py"
    # Or simple format: 0 8 * * * python3 pipeline.py
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        
        # Trim whitespace
        line=$(echo "$line" | xargs)
        
        # Check if line looks like a cron entry (starts with a number or *)
        if [[ "$line" =~ ^[0-9\*] ]]; then
            # Direct cron format: "0 8 * * * python3 pipeline.py"
            # Add working directory prefix
            echo "cd /app && $line >> /app/logs/cron.log 2>&1" >> "$CRON_FILE"
            echo "Added cron job: $line"
        elif [[ "$line" =~ ^CRON_ ]]; then
            # Variable format: CRON_DAILY="0 8 * * * python3 pipeline.py"
            # Extract the value after the first =
            cron_entry=$(echo "$line" | sed 's/^CRON_[^=]*=//' | tr -d '"' | tr -d "'")
            if [ -n "$cron_entry" ]; then
                echo "cd /app && $cron_entry >> /app/logs/cron.log 2>&1" >> "$CRON_FILE"
                echo "Added cron job: $cron_entry"
            fi
        fi
    done < /app/config/.env
    
    # Add empty line at the end (required by cron)
    echo "" >> "$CRON_FILE"
    
    # Install the crontab
    crontab "$CRON_FILE"
    echo "Cron jobs installed successfully."
    
    # Show the installed crontab
    echo "Current crontab:"
    crontab -l
    
    return 0
}

# Main execution logic
case "${1:-cron}" in
    cron)
        echo "Starting JavDB Auto Spider in cron mode..."
        
        if setup_cron; then
            echo "Starting cron daemon..."
            # Start cron in foreground
            exec cron -f
        else
            echo "No cron jobs configured. Container will exit."
            echo "To run manually, use: docker run <image> python3 pipeline.py [args]"
            exit 0
        fi
        ;;
    
    python3|python)
        # Direct Python execution
        echo "Running Python script: ${@:1}"
        cd /app
        exec "$@"
        ;;
    
    pipeline)
        # Shortcut for running the pipeline
        echo "Running pipeline with arguments: ${@:2}"
        cd /app
        exec python3 pipeline.py "${@:2}"
        ;;
    
    spider)
        # Shortcut for running the spider
        echo "Running spider with arguments: ${@:2}"
        cd /app
        exec python3 scripts/spider.py "${@:2}"
        ;;
    
    uploader)
        # Shortcut for running the uploader
        echo "Running uploader with arguments: ${@:2}"
        cd /app
        exec python3 scripts/qb_uploader.py "${@:2}"
        ;;
    
    bash|sh)
        # Shell access
        exec /bin/bash
        ;;
    
    *)
        # Any other command
        exec "$@"
        ;;
esac

