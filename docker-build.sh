#!/bin/bash

# ============================================================
# JAVDB AutoSpider Docker Build & Deploy Script
# ============================================================
# Quick deployment script for Docker setup
# ============================================================

set -e

echo "=========================================="
echo "JAVDB AutoSpider Docker Setup"
echo "=========================================="
echo ""

# ============================================================
# Check if Docker is installed
# ============================================================
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed!"
    echo "Please install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "ERROR: Docker Compose is not installed!"
    echo "Please install Docker Compose first: https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✓ Docker is installed"
echo "✓ Docker Compose is installed"
echo ""

# ============================================================
# Create necessary directories
# ============================================================
echo "Creating necessary directories..."
mkdir -p logs
mkdir -p "Ad Hoc"
mkdir -p "Daily Report"
echo "✓ Directories created"
echo ""

# ============================================================
# Check for config.py
# ============================================================
if [ ! -f "config.py" ]; then
    echo "WARNING: config.py not found!"
    echo ""
    if [ -f "config.py.example" ]; then
        echo "Would you like to copy config.py.example to config.py? (y/n)"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            cp config.py.example config.py
            echo "✓ Copied config.py.example to config.py"
            echo ""
            echo "IMPORTANT: Please edit config.py with your configuration before running!"
            echo "Edit config.py now? (y/n)"
            read -r edit_response
            if [[ "$edit_response" =~ ^[Yy]$ ]]; then
                ${EDITOR:-nano} config.py
            fi
        else
            echo "Please create config.py manually before proceeding"
            exit 1
        fi
    else
        echo "ERROR: config.py.example not found!"
        exit 1
    fi
else
    echo "✓ config.py found"
fi
echo ""

# ============================================================
# Check for .env file
# ============================================================
if [ ! -f ".env" ]; then
    echo "WARNING: .env file not found!"
    echo ""
    if [ -f "env.example" ]; then
        echo "Would you like to copy env.example to .env? (y/n)"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            cp env.example .env
            echo "✓ Copied env.example to .env"
            echo ""
            echo "You can edit .env to configure cron schedules"
            echo "Edit .env now? (y/n)"
            read -r edit_response
            if [[ "$edit_response" =~ ^[Yy]$ ]]; then
                ${EDITOR:-nano} .env
            fi
        else
            echo "Creating default .env file..."
            cp env.example .env
            echo "✓ Created default .env"
        fi
    else
        echo "WARNING: env.example not found, creating minimal .env"
        cat > .env << 'EOF'
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python scripts/spider.py --use-proxy >> /var/log/cron.log 2>&1
ENABLE_SPIDER=true
ENABLE_PIPELINE=false
ENABLE_QBTORRENT=false
ENABLE_PIKPAK=false
EOF
        echo "✓ Created minimal .env"
    fi
else
    echo "✓ .env file found"
fi
echo ""

# ============================================================
# Build Docker image
# ============================================================
echo "=========================================="
echo "Building Docker Image"
echo "=========================================="
echo ""
docker-compose build

echo ""
echo "✓ Docker image built successfully"
echo ""

# ============================================================
# Ask to start container
# ============================================================
echo "=========================================="
echo "Build Complete!"
echo "=========================================="
echo ""
echo "Would you like to start the container now? (y/n)"
read -r start_response

if [[ "$start_response" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Starting container..."
    docker-compose up -d
    echo ""
    echo "✓ Container started successfully!"
    echo ""
    echo "=========================================="
    echo "Quick Commands"
    echo "=========================================="
    echo "View logs:           docker-compose logs -f"
    echo "View cron logs:      docker exec javdb-spider tail -f /var/log/cron.log"
    echo "Stop container:      docker-compose down"
    echo "Restart container:   docker-compose restart"
    echo "Run script manually: docker exec javdb-spider python scripts/spider.py --use-proxy"
    echo "=========================================="
else
    echo ""
    echo "To start the container later, run:"
    echo "  docker-compose up -d"
fi

echo ""
echo "Setup complete!"
echo ""

