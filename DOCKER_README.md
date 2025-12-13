# JAVDB AutoSpider Docker Deployment Guide

This guide explains how to deploy JAVDB AutoSpider using Docker.

## Prerequisites

- Docker installed (version 20.10 or higher)
- Docker Compose installed (version 2.0 or higher)
- A valid `config.py` file with your configuration

## Quick Start

### 1. Prepare Configuration

First, copy the example environment file and configure your cron schedules:

```bash
cp .env.example .env
```

Edit `.env` to configure your cron jobs:

```bash
# Example: Run spider at 3 AM daily
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python Javdb_Spider.py --use-proxy >> /var/log/cron.log 2>&1
```

### 2. Ensure Required Files Exist

Make sure you have:
- `config.py` - Your configuration file (copy from `config.py.example` and customize)
- `logs/` - Directory for logs (will be created if doesn't exist)
- `Ad Hoc/` - Directory for ad-hoc reports (will be created if doesn't exist)
- `Daily Report/` - Directory for daily reports (will be created if doesn't exist)

Create directories if they don't exist:

```bash
mkdir -p logs "Ad Hoc" "Daily Report"
```

### 3. Build and Run

Build the Docker image:

```bash
docker-compose build
```

Start the container:

```bash
docker-compose up -d
```

## Usage

### View Logs

View real-time logs:

```bash
docker-compose logs -f
```

View cron job logs:

```bash
docker exec javdb-spider tail -f /var/log/cron.log
```

### Stop Container

```bash
docker-compose down
```

### Restart Container

```bash
docker-compose restart
```

### Run Script Manually

Run a script manually inside the container:

```bash
# Run spider manually
docker exec javdb-spider python Javdb_Spider.py --use-proxy

# Run with specific parameters
docker exec javdb-spider python Javdb_Spider.py --start-page 1 --end-page 5 --use-proxy
```

### Access Container Shell

```bash
docker exec -it javdb-spider bash
```

## Configuration

### Cron Job Configuration

Edit `.env` file to configure cron schedules. Format: `minute hour day month weekday`

Examples:
- `0 3 * * *` - Run at 3:00 AM every day
- `*/30 * * * *` - Run every 30 minutes
- `0 */6 * * *` - Run every 6 hours
- `0 2 * * 0` - Run at 2 AM every Sunday

### Enable/Disable Jobs

Set these variables in `.env`:

```bash
ENABLE_SPIDER=true      # Enable/disable spider job
ENABLE_PIPELINE=true    # Enable/disable pipeline job
ENABLE_QBTORRENT=true   # Enable/disable qBittorrent uploader
ENABLE_PIKPAK=true      # Enable/disable PikPak bridge
```

### Timezone Configuration

Set timezone in `.env`:

```bash
TZ=Asia/Shanghai        # Default timezone
# TZ=America/New_York   # New York time
# TZ=Europe/London      # London time
```

## Volume Mounts

The following directories are mounted from your host:

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./config.py` | `/app/config.py` | Configuration file (read-only) |
| `./logs` | `/app/logs` | Log files (read-write) |
| `./Ad Hoc` | `/app/Ad Hoc` | Ad-hoc reports (read-write) |
| `./Daily Report` | `/app/Daily Report` | Daily reports (read-write) |

## Troubleshooting

### Container Won't Start

Check if `config.py` exists:

```bash
ls -la config.py
```

If not, create it from the example:

```bash
cp config.py.example config.py
# Edit config.py with your settings
```

### Cron Jobs Not Running

Check crontab configuration:

```bash
docker exec javdb-spider crontab -l
```

Check cron logs:

```bash
docker exec javdb-spider cat /var/log/cron.log
```

### Permission Issues

Ensure directories have correct permissions:

```bash
chmod -R 755 logs "Ad Hoc" "Daily Report"
```

### Python Dependencies

If you add new dependencies, rebuild the image:

```bash
docker-compose build --no-cache
docker-compose up -d
```

## Advanced Configuration

### Custom Commands

You can add custom cron jobs by editing `.env`:

```bash
# Custom cleanup job
CRON_CLEANUP=0 1 * * 0
CLEANUP_COMMAND=cd /app && /usr/local/bin/python cleanup_script.py >> /var/log/cron.log 2>&1
```

### Resource Limits

Uncomment and adjust in `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
```

### Health Checks

The container includes a health check that monitors the cron process:

```bash
docker inspect --format='{{json .State.Health}}' javdb-spider
```

## Maintenance

### Update Image

Pull latest code and rebuild:

```bash
git pull
docker-compose build --no-cache
docker-compose up -d
```

### Backup Data

Backup important directories:

```bash
tar -czf backup-$(date +%Y%m%d).tar.gz \
  config.py \
  logs/ \
  "Ad Hoc/" \
  "Daily Report/" \
  parsed_movies_history.csv
```

### Clean Up Old Logs

```bash
# Inside container
docker exec javdb-spider find /app/logs -name "*.log" -mtime +30 -delete
```

## Security Notes

1. **Never commit** your `config.py` or `.env` files with sensitive information
2. Use read-only mount for `config.py` to prevent accidental modification
3. Regularly update the base Python image for security patches
4. Use secrets management for production deployments

## Support

For issues and questions:
- Check logs: `docker-compose logs`
- Check cron logs: `docker exec javdb-spider cat /var/log/cron.log`
- Refer to main README.md for application-specific help

