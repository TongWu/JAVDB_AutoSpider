# Docker Deployment

This guide covers running JavDB AutoSpider in Docker, using either standalone containers or Docker Compose.

## Prerequisites

- Docker 20.10+
- Docker Compose v2 (for compose method)
- A configured `config.py` (see [Configuration Reference](configuration.md))

## Quick Start with Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/TongWu/JAVDB_AutoSpider_CICD.git
cd JAVDB_AutoSpider_CICD

# Prepare configuration
cp config.py.example config.py
cp docker/.env.example .env
# Edit config.py with your settings (proxy, qBittorrent, etc.)

# Build and start
docker-compose -f docker/docker-compose.yml build
docker-compose -f docker/docker-compose.yml up -d
```

Or use the automated build script:

```bash
./docker/docker-build.sh
```

## Standalone Docker Run

```bash
docker run -d \
  --name javdb-spider \
  --restart unless-stopped \
  -v $(pwd)/config.py:/app/config.py:ro \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/Ad\ Hoc:/app/Ad\ Hoc \
  -v $(pwd)/Daily\ Report:/app/Daily\ Report \
  --env-file .env \
  ghcr.io/YOUR_USERNAME/javdb-autospider:latest
```

The Docker image uses multi-stage builds: a Rust builder stage compiles the `javdb_rust_core` extension, and the runtime stage only includes the compiled wheel.

## Volume Mounts

| Mount | Purpose | Mode |
|-------|---------|------|
| `config.py:/app/config.py` | Main configuration | Read-only |
| `logs:/app/logs` | Log files | Read-write |
| `reports/AdHoc:/app/reports/AdHoc` | Ad hoc scraping results | Read-write |
| `reports/DailyReport:/app/reports/DailyReport` | Daily report output | Read-write |

## Environment Variables

Set `TZ` to match your timezone (default: `Asia/Shanghai`). Storage and write-mode variables also apply — see [Configuration Reference](configuration.md) for the full list.

## Managing the Container

### Basic Commands

```bash
# View container logs
docker logs -f javdb-spider

# View cron logs
docker exec javdb-spider tail -f /var/log/cron.log

# Run spider manually
docker exec javdb-spider python3 -m apps.cli.spider --use-proxy

# Run pipeline manually
docker exec javdb-spider python3 -m apps.cli.pipeline

# Execute commands inside container
docker exec -it javdb-spider bash

# Stop / Start / Restart
docker stop javdb-spider
docker start javdb-spider
docker restart javdb-spider
```

### Docker Compose Commands

```bash
docker-compose -f docker/docker-compose.yml up -d       # Start
docker-compose -f docker/docker-compose.yml down         # Stop
docker-compose -f docker/docker-compose.yml logs -f      # Logs
docker-compose -f docker/docker-compose.yml restart      # Restart

# Rebuild (after code or Dockerfile changes)
docker-compose -f docker/docker-compose.yml build --no-cache
docker-compose -f docker/docker-compose.yml up -d
```

## Cron Scheduling

Edit the `.env` file to configure scheduled tasks:

```bash
# Spider runs daily at 3:00 AM (container timezone)
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python -m apps.cli.spider --use-proxy >> /var/log/cron.log 2>&1

# Pipeline runs daily at 4:00 AM
CRON_PIPELINE=0 4 * * *
PIPELINE_COMMAND=cd /app && /usr/local/bin/python -m apps.cli.pipeline >> /var/log/cron.log 2>&1
```

After modifying `.env`, restart the container:

```bash
docker-compose -f docker/docker-compose.yml restart
```

See `docker/.env.example` for additional schedule examples and argument reference.

## Health Check

The compose file includes a health check that verifies the cron daemon is running:

```yaml
healthcheck:
  test: ["CMD", "pgrep", "cron"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

## Troubleshooting

**Container exits immediately:**
- Check `docker logs javdb-spider` for errors
- Verify `config.py` is mounted correctly and has valid syntax

**Cron jobs not running:**
- Check `docker exec javdb-spider tail -f /var/log/cron.log`
- Verify `.env` cron expressions are valid
- Ensure container timezone matches your expectations (`TZ` variable)

**Permission errors on mounted volumes:**
- Ensure host directories exist and are writable
- On Linux, check UID/GID matching between host and container
