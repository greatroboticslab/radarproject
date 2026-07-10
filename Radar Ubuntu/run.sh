#!/usr/bin/env bash
# Launch the mmWave Soil Moisture Sensor GUI.
# Use this for every run after the initial ./setup.sh.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "No .venv found — run ./setup.sh first (one-time setup)."
    exit 1
fi

source .venv/bin/activate
exec python gui.py
