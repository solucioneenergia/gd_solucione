$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$FrontendRoot = Join-Path (Get-Location) "apps\desktop\frontend"
$FrontendDist = Join-Path $FrontendRoot "dist"
$FrontendStatic = Join-Path $FrontendRoot "static"

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm 11.9.0 é obrigatório para o build reproduzível do frontend."
}

Push-Location $FrontendRoot
try {
    pnpm install --frozen-lockfile
    pnpm test
    pnpm build
}
finally {
    Pop-Location
}

if (-not (Test-Path (Join-Path $FrontendDist "index.html"))) {
    throw "Build frontend canônico ausente: apps\desktop\frontend\dist\index.html"
}

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
    --hidden-import "apps.desktop.main" `
    --add-data "$FrontendDist;apps/desktop/frontend/dist" `
    --add-data "$FrontendStatic;apps/desktop/frontend/static" `
    desktop_app.py

Write-Host "Build gerado em dist\AutomacaoGDNeoenergia."
Write-Host "Distribua também um .env configurado fora do executável; nunca inclua storage_state.json."
