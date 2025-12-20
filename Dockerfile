# JavDB Auto Spider Docker Image
# Supports both arm64 and x86_64 architectures

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    cron \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone (can be overridden by environment variable)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy requirements first for better Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY scripts/ ./scripts/
COPY utils/ ./utils/
COPY pipeline.py .
COPY config.py.example .

# Copy docker entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create directories for mounted volumes
RUN mkdir -p /app/config /app/data/Daily\ Report /app/data/Ad\ Hoc /app/logs

# Create symbolic links for volume mounts
# Config - will be mounted from host
RUN ln -sf /app/config/config.py /app/config.py

# Data directories - will be mounted from host
RUN rm -rf "/app/Daily Report" "/app/Ad Hoc" && \
    ln -sf "/app/data/Daily Report" "/app/Daily Report" && \
    ln -sf "/app/data/Ad Hoc" "/app/Ad Hoc"

# Note: logs directory symbolic link is created at runtime in entrypoint.sh
# because it needs to point to the mounted volume

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Default command (can be overridden)
CMD ["cron"]

