FROM nvidia/cuda:13.0.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    cmake \
    curl \
    git \
    iproute2 \
    procps \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv ${VIRTUAL_ENV}

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN uv pip install --python ${VIRTUAL_ENV}/bin/python -e ".[dev]" \
    && uv pip install --python ${VIRTUAL_ENV}/bin/python \
       --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu130 \
       llama-cpp-python==0.3.36

CMD ["/bin/bash"]