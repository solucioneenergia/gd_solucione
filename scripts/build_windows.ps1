param(
    [string]$AppName = "Solucione Nordeste",
    [string]$IconPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

function Assert-DesktopIconFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([System.IO.Path]::GetExtension($Path).ToLowerInvariant() -ne ".ico") {
        throw "DESKTOP_ICON_INVALID: o ícone do build deve ser um arquivo .ico: $Path"
    }

    $Stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($Stream.Length -lt 6) {
            throw "DESKTOP_ICON_INVALID: arquivo .ico inválido ou vazio: $Path"
        }
        $Header = [byte[]]::new(4)
        $Read = $Stream.Read($Header, 0, 4)
        if (
            $Read -ne 4 -or
            $Header[0] -ne 0 -or
            $Header[1] -ne 0 -or
            $Header[2] -ne 1 -or
            $Header[3] -ne 0
        ) {
            throw "DESKTOP_ICON_INVALID: arquivo .ico inválido: $Path"
        }
    }
    finally {
        $Stream.Dispose()
    }
}

Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($IconPath)) {
    $IconPath = Join-Path $ProjectRoot "apps\desktop\resources\app_icon.ico"
}

$ResolvedIcon = Resolve-Path -LiteralPath $IconPath -ErrorAction SilentlyContinue
if (-not $ResolvedIcon) {
    throw "DESKTOP_ICON_INVALID: ícone não encontrado: $IconPath"
}
$ResolvedIconPath = $ResolvedIcon.Path
Assert-DesktopIconFile -Path $ResolvedIconPath

$FrontendRoot = Join-Path (Get-Location) "apps\desktop\frontend"
$FrontendDist = Join-Path $FrontendRoot "dist"
$FrontendStatic = Join-Path $FrontendRoot "static"
$DesktopResources = Join-Path (Get-Location) "apps\desktop\resources"

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
    --name "$AppName" `
    --icon "$ResolvedIconPath" `
    --collect-all playwright `
    --hidden-import "apps.desktop.main" `
    --add-data "$FrontendDist;apps/desktop/frontend/dist" `
    --add-data "$FrontendStatic;apps/desktop/frontend/static" `
    --add-data "$DesktopResources;apps/desktop/resources" `
    desktop_app.py

Write-Host "Build gerado em dist\$AppName."
Write-Host "Distribua também um .env configurado fora do executável; nunca inclua storage_state.json."
