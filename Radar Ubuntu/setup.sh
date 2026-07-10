#!/usr/bin/env bash
# One-shot setup + launch for the mmWave Soil Moisture Sensor on Ubuntu.
# Run this once after transferring the folder: ./setup.sh
# It installs everything needed and then opens the GUI. Safe to re-run —
# it skips steps that are already done.
set -e

cd "$(dirname "$0")"

echo "== Installing system packages (tkinter for the GUI, venv) =="
sudo apt update
sudo apt install -y python3-venv python3-tk

if [ ! -d .venv ]; then
    echo "== Creating virtual environment (.venv) =="
    python3 -m venv .venv
else
    echo "== Virtual environment already exists, reusing it =="
fi
source .venv/bin/activate

echo "== Installing Python dependencies =="
pip install --upgrade pip
pip install -r requirements.txt

NEEDED_GROUP_ADD=false
if ! id -nG "$USER" | grep -qw dialout; then
    echo "== Adding current user to the 'dialout' group (serial port access) =="
    sudo usermod -aG dialout "$USER"
    NEEDED_GROUP_ADD=true
else
    echo "== Already in 'dialout' group =="
fi

echo
echo "Setup complete. Launching the GUI..."
echo

if [ "$NEEDED_GROUP_ADD" = true ]; then
    # Group membership normally requires a fresh login shell to take effect.
    # 'sg' starts a subshell with the new group active immediately, so we
    # can launch straight into the GUI on this same run without forcing a
    # logout/reboot first.
    exec sg dialout -c "python gui.py"
else
    exec python gui.py
fi
