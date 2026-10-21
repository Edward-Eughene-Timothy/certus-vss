#!/usr/bin/env bash
set -e
echo "Setting up Python environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
echo "Environment setup complete."
