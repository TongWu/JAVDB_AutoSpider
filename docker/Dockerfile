# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    cron \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone (default to Asia/Shanghai, can be overridden)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy requirements.txt first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY utils/ ./utils/
COPY scripts/ ./scripts/
COPY config.py.example .
COPY pipeline.py .

# Copy docker entrypoint script
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create necessary directories (will be mounted from host)
RUN mkdir -p /app/logs /app/"Ad Hoc" /app/"Daily Report"

# Create log file for cron
RUN touch /var/log/cron.log

# Expose any ports if needed (none required for this app)
# EXPOSE 8000

# Use entrypoint script
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default command: start cron in foreground and tail the log
CMD ["cron", "-f"]

