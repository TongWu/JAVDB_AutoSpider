# Docker Deployment Guide

This guide explains how to deploy JavDB Auto Spider using Docker.

## Features

- Multi-platform support (arm64 & x86_64)
- Automatic cron job scheduling
- CloudFlare bypass integration
- Persistent data and logs storage

## Quick Start

### 1. Create Directory Structure

```bash
mkdir -p javdb-spider/{config,data,logs}
cd javdb-spider
```

### 2. Create Configuration Files

Copy and customize `config.py`:

```bash
# Download example config
curl -o config/config.py https://raw.githubusercontent.com/YOUR_USERNAME/JAVDB_AutoSpider/main/config.py.example

# Edit with your settings
nano config/config.py
```

Create `.env` for cron scheduling:

```bash
cat > config/.env << 'EOF'
# Run daily at 8:00 AM
CRON_DAILY="0 8 * * * python3 pipeline.py --use-proxy --use-cf-bypass"
EOF
```

### 3. Create docker-compose.yml

```yaml
version: '3.8'

services:
  javdb-spider:
    image: ghcr.io/YOUR_USERNAME/javdb-autospider:latest
    container_name: javdb-spider
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./data:/app/data
      - ./logs:/app/logs_volume
    environment:
      - TZ=Asia/Shanghai
    networks:
      - javdb-network

  cf-bypass:
    image: ghcr.io/sarperavci/cloudflarebypassforscraping:latest
    container_name: cf-bypass
    restart: unless-stopped
    ports:
      - "8000:8000"
    networks:
      - javdb-network

networks:
  javdb-network:
    driver: bridge
```

### 4. Start the Services

```bash
docker compose up -d
```

## Directory Structure

```
javdb-spider/
├── config/
│   ├── config.py      # Main configuration file
│   └── .env           # Cron job schedules
├── data/
│   ├── Daily Report/  # Daily CSV reports
│   └── Ad Hoc/        # Ad-hoc reports
├── logs/              # Log files
└── docker-compose.yml
```

## Configuration

### config.py

This is the main configuration file, equivalent to the local `config.py`. Important settings include:

- **qBittorrent**: Host, port, credentials
- **Email**: SMTP settings for notifications
- **Proxy**: Proxy pool configuration
- **CloudFlare Bypass**: Port setting (default: 8000)

**Important**: When running in Docker, update `CF_BYPASS_SERVICE_PORT` to match your setup, and if using the Docker network, the CF bypass service can be accessed at `cf-bypass:8000`.

### .env (Cron Jobs)

Define scheduled tasks using cron format:

```bash
# Format: MINUTE HOUR DAY MONTH WEEKDAY COMMAND

# Daily at 8:00 AM
CRON_DAILY="0 8 * * * python3 pipeline.py --use-proxy --use-cf-bypass"

# Twice daily at 8:00 AM and 8:00 PM
CRON_MORNING="0 8 * * * python3 pipeline.py --use-proxy --use-cf-bypass"
CRON_EVENING="0 20 * * * python3 pipeline.py --use-proxy --use-cf-bypass"

# Weekly full scan on Sundays at 3:00 AM
CRON_WEEKLY="0 3 * * 0 python3 pipeline.py --use-proxy --use-cf-bypass --ignore-history --all"
```

## Running Commands Manually

### Run Pipeline

```bash
docker exec javdb-spider python3 pipeline.py --use-proxy --use-cf-bypass
```

### Run Spider Only

```bash
docker exec javdb-spider python3 scripts/spider.py --use-proxy
```

### Run Uploader Only

```bash
docker exec javdb-spider python3 scripts/qb_uploader.py --mode daily
```

### Using Shortcuts

The container provides shortcuts for common commands:

```bash
# Run pipeline
docker run --rm -v ./config:/app/config ghcr.io/YOUR_USERNAME/javdb-autospider:latest pipeline --use-proxy

# Run spider
docker run --rm -v ./config:/app/config ghcr.io/YOUR_USERNAME/javdb-autospider:latest spider --use-proxy

# Interactive shell
docker run --rm -it -v ./config:/app/config ghcr.io/YOUR_USERNAME/javdb-autospider:latest bash
```

## Viewing Logs

```bash
# View cron execution logs
tail -f logs/cron.log

# View spider logs
tail -f logs/Javdb_Spider.log

# View pipeline logs
tail -f logs/pipeline_run_and_notify.log

# View all container logs
docker logs -f javdb-spider
```

## Building from Source

If you want to build the image locally:

```bash
git clone https://github.com/YOUR_USERNAME/JAVDB_AutoSpider.git
cd JAVDB_AutoSpider
docker build -t javdb-autospider:local .
```

## Network Configuration

### Accessing Local Services

If your qBittorrent runs on the host machine, you have two options:

**Option 1: Host Network Mode**

```yaml
services:
  javdb-spider:
    network_mode: host
```

**Option 2: Use Host IP**

In `config.py`, use your host machine's IP address instead of `localhost`:

```python
QB_HOST = '192.168.1.100'  # Your host IP
```

### CloudFlare Bypass Integration

When using the CF bypass service in Docker:

1. Ensure both services are on the same network
2. In `config.py`, you may need to configure the CF bypass URL based on your setup

## Troubleshooting

### Config not found

```
ERROR: config.py not found in /app/config/
```

Make sure you have mounted the config directory and it contains `config.py`.

### Permission issues

```bash
# Fix permissions on mounted directories
chmod -R 755 config data logs
```

### Cron jobs not running

Check if `.env` file exists and has correct format:

```bash
docker exec javdb-spider crontab -l
```

---

## Original Requirements

<details>
<summary>Click to expand original requirements</summary>

1. I want to add Docker support, which suppose to add all necessary py files (scripts, utils, pipeline, etc) into a docker image.
2. The expectation is that the docker image can be run on arm64 & x86_64 machines platform.
3. User need to create a docker compose file, to obtain this project's image, and a cloudflare bypass container.
4. User need to create a config folder, to store the config.py and .env file.
    - The config.py file is equavalent to the config.py file in this project's local folder.
    - The .env file is more for the cron job config, should support multiple line to run multiple scripts in different time.
5. User need to create a data folder to store the daily ingestion and adhoc report.
6. User need to create a logs folder to store the log files.
7. When fit the docker, the current method to run the pipeline or other scripts should not be changed.
8. Please create another branch for this feature.
9. For all docker related codes, please put them under the docker folder except the DOCKERFILE.
10. Provide a sample docker compose file.
11. Please come with a github action yml file to build the docker image.

</details>