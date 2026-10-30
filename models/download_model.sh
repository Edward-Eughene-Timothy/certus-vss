#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_FILE="qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

mkdir -p "${MODEL_DIR}"

if [ -f "${MODEL_DIR}/${MODEL_FILE}" ]; then
    echo "[CertusVSS] Model file already exists: ${MODEL_DIR}/${MODEL_FILE}"
    exit 0
fi

echo "[CertusVSS] Downloading Qwen 2.5 0.5B Instruct (Q4_K_M GGUF)..."
curl -L --progress-bar -o "${MODEL_DIR}/${MODEL_FILE}" "${MODEL_URL}"
echo "[CertusVSS] Model download complete: ${MODEL_DIR}/${MODEL_FILE}"
