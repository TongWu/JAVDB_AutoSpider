# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (including build tools for Rust)
RUN apt-get update && apt-get install -y \
    git \
    cron \
    tzdata \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Set timezone (default to Asia/Shanghai, can be overridden)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy requirements.txt first for better layer caching
COPY requirements.txt .

# Install Python dependencies and maturin
RUN pip install --no-cache-dir "maturin[patchelf]" && \
    pip install --no-cache-dir -r requirements.txt

# Copy and build Rust extension (before application code for better caching)
COPY rust_core/ ./rust_core/
RUN cd rust_core && maturin build --release && \
    pip install target/wheels/*.whl && \
    cd .. && rm -rf rust_core/target

# Copy application files
COPY api/ ./api/
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
