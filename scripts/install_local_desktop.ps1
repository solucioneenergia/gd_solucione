param(
    [string]$BundlePath = "",
    [string]$InstallRoot = "",
    [switch]$CreateShortcut
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$AppName = "Solucione Nordeste"
$ExecutableName = "$AppName.exe"

if ([string]::IsNullOrWhiteSpace($BundlePath)) {
    $BundlePath = Join-Path $ProjectRoot "dist\$AppName"
}
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA $AppName
}

$ResolvedBundle = Resolve-Path -LiteralPath $BundlePath -ErrorAction SilentlyContinue
if (-not $ResolvedBundle) {
    throw "LOCAL_INSTALL_VALIDATION_FAILED: bundle nao encontrado: $BundlePath"
}

$SourceExecutable = Join-Path $ResolvedBundle.Path $ExecutableName
if (-not (Test-Path -LiteralPath $SourceExecutable)) {
    throw "LOCAL_INSTALL_VALIDATION_FAILED: executavel nao encontrado: $SourceExecutable"
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Copy-Item -Path (Join-Path $ResolvedBundle.Path "*") -Destination $InstallRoot -Recurse -Force

$InstalledExecutable = Join-Path $InstallRoot $ExecutableName
if (-not (Test-Path -LiteralPath $InstalledExecutable)) {
    throw "LOCAL_INSTALL_VALIDATION_FAILED: executavel instalado nao encontrado: $InstalledExecutable"
}

if ($CreateShortcut) {
    $ShortcutScript = Join-Path $PSScriptRoot "create_desktop_shortcut.ps1"
    $ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Solucione Nordeste.lnk"
    $IconPath = Join-Path $InstallRoot "_internal\apps\desktop\resources\app_icon.ico"
    if (-not (Test-Path -LiteralPath $IconPath)) {
        $IconPath = Join-Path $InstallRoot "apps\desktop\resources\app_icon.ico"
    }
    if (-not (Test-Path -LiteralPath $IconPath)) {
        $IconPath = $InstalledExecutable
    }
    & $ShortcutScript `
        -TargetPath $InstalledExecutable `
        -ShortcutPath $ShortcutPath `
        -WorkingDirectory $InstallRoot `
        -IconPath $IconPath
}

Write-Host "Instalacao local concluida: $InstallRoot"
