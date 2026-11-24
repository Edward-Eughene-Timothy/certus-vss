FROM python:3.11-slim

# Install system utilities needed for testing and C++ builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    iproute2 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy package definitions and source required for build
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install dependencies and local package in editable mode
RUN uv pip install --system -e ".[dev]"

# Default working shell
CMD ["/bin/bash"]
