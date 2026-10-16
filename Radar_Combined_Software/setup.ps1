# One-shot setup for the mmWave Soil Moisture Sensor on Windows.
# Run this once after transferring the folder: .\setup.ps1
# Safe to re-run -- it skips steps that are already done.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "== Creating virtual environment (.venv) =="
    py -m venv .venv
} else {
    Write-Host "== Virtual environment already exists, reusing it =="
}

Write-Host "== Installing Python dependencies =="
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "== Installing acconeer-exptool (for the A121 range sensor source) =="
& ".venv\Scripts\python.exe" -m pip install --upgrade "acconeer-exptool[app]"

Write-Host ""
Write-Host "Setup complete. Launching the GUI..."
Write-Host ""

& ".venv\Scripts\python.exe" gui.py
