#!/usr/bin/env bash
set -e
MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
OUTPUT_FILE="$MODEL_DIR/qwen2.5-0.5b-instruct-q4_k_m.gguf"

if [ -f "$OUTPUT_FILE" ]; then
    echo "Model already downloaded: $OUTPUT_FILE"
    exit 0
fi

echo "Downloading Qwen 2.5 0.5B Instruct (Q4_K_M GGUF, ~398 MB)..."
curl -L "$MODEL_URL" -o "$OUTPUT_FILE"
echo "Download complete: $OUTPUT_FILE"
