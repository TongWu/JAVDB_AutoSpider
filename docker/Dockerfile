# ============================================================
# Stage 1: Build Rust extension wheel
# This layer is cached by Docker BuildKit and only rebuilds
# when javdb/rust_core/ source files change.
# ============================================================
FROM python:3.11-slim AS rust-builder

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN pip install --no-cache-dir maturin[patchelf]

COPY javdb/rust_core/ /build/rust_core/
RUN cd /build/rust_core && maturin build --release --out /wheels

# ============================================================
# Stage 2: Runtime image (no Rust toolchain needed)
# ============================================================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    cron \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set timezone (default to Asia/Shanghai, can be overridden)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy requirements.txt first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install pre-built Rust extension wheel from builder stage
COPY --from=rust-builder /wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -f /tmp/*.whl

# Copy application files
COPY apps/ ./apps/
COPY javdb/ ./javdb/
COPY scripts/ ./scripts/
COPY config.py.example .

# Copy docker entrypoint script
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root runtime user.  The cron DAEMON must run as root so that it can
# fork jobs as a different user (the ``spider`` username in each cron line
# in /etc/cron.d/).  The Python app processes themselves therefore never run
# as root.  We intentionally omit a top-level ``USER`` directive here:
# setting USER=spider would break ``cron -f`` (daemon won't start as
# non-root).  Use ``docker exec -u spider ...`` for interactive debugging.
RUN useradd -m -u 10001 -s /bin/bash spider

# Create necessary directories (will be mounted from host)
RUN mkdir -p /app/logs /app/"Ad Hoc" /app/"Daily Report" \
    && chown -R spider:spider /app \
    && touch /var/log/cron.log \
    && chmod 0622 /var/log/cron.log

# Use entrypoint script
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default command: start cron in foreground
CMD ["cron", "-f"]
