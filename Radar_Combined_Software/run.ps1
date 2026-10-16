# Launch the mmWave Soil Moisture Sensor GUI.
# Use this for every run after the initial .\setup.ps1.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "No .venv found -- run .\setup.ps1 first (one-time setup)."
    exit 1
}

& ".venv\Scripts\python.exe" gui.py
