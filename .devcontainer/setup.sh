#!/bin/bash
# One-time setup for GitHub Codespaces (CPU only).
set -e
sudo apt-get update
sudo apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core
# The NVIDIA CUDA libraries are useless without a GPU; skip the ~1 GB download.
grep -v '^nvidia-' requirements.txt > /tmp/requirements-cpu.txt
pip install --no-cache-dir -r /tmp/requirements-cpu.txt
echo "Setup complete. The web UI starts automatically on port 7860."
