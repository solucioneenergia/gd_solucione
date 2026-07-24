$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv")) {
    py -3.12 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-build.txt

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "AutomacaoGDNeoenergia" `
    --collect-all playwright `
    desktop_app.py

Write-Host "Build gerado em dist\AutomacaoGDNeoenergia."
Write-Host "Distribua também um .env configurado fora do executável; nunca inclua storage_state.json."
