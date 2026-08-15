param(
    [string]$AppName = "Solucione Nordeste",
    [string]$IconPath = "",
    [string]$PythonPath = ""
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

function Resolve-BuildPython {
    param(
        [string]$RequestedPythonPath
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedPythonPath)) {
        $ResolvedPython = Resolve-Path -LiteralPath $RequestedPythonPath -ErrorAction SilentlyContinue
        if (-not $ResolvedPython) {
            throw "PYTHON_BUILD_ENV_INVALID: python informado nao encontrado: $RequestedPythonPath"
        }
        return $ResolvedPython.Path
    }

    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
            throw "PYTHON_BUILD_ENV_INVALID: py -3.12 nao encontrado para criar .venv."
        }
        py -3.12 -m venv .venv
    }
    return $VenvPython
}

function Assert-BuildPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    try {
        & $Path --version | Out-Null
    }
    catch {
        throw "PYTHON_BUILD_ENV_INVALID: python do build nao executa: $Path"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "PYTHON_BUILD_ENV_INVALID: python do build retornou codigo $LASTEXITCODE`: $Path"
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

$BuildPython = Resolve-BuildPython -RequestedPythonPath $PythonPath
Assert-BuildPython -Path $BuildPython
& $BuildPython -m pip install --upgrade pip
& $BuildPython -m pip install -r requirements-build.txt

& $BuildPython -m PyInstaller `
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
