param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,

    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "Solucione Nordeste.lnk"),
    [string]$WorkingDirectory = "",
    [string]$IconPath = ""
)

$ErrorActionPreference = "Stop"

function Assert-DesktopIconFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([System.IO.Path]::GetExtension($Path).ToLowerInvariant() -ne ".ico") {
        throw "DESKTOP_ICON_INVALID: o atalho exige um arquivo .ico: $Path"
    }

    $Stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($Stream.Length -lt 6) {
            throw "DESKTOP_ICON_INVALID: arquivo .ico invalido ou vazio: $Path"
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
            throw "DESKTOP_ICON_INVALID: arquivo .ico invalido: $Path"
        }
    }
    finally {
        $Stream.Dispose()
    }
}

$ResolvedTarget = Resolve-Path -LiteralPath $TargetPath -ErrorAction SilentlyContinue
if (-not $ResolvedTarget) {
    throw "DESKTOP_SHORTCUT_CREATION_FAILED: executavel nao encontrado: $TargetPath"
}
$ResolvedTargetPath = $ResolvedTarget.Path

if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    $WorkingDirectory = Split-Path $ResolvedTargetPath -Parent
}

$ResolvedWorkingDirectory = Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction SilentlyContinue
if (-not $ResolvedWorkingDirectory) {
    throw "DESKTOP_SHORTCUT_CREATION_FAILED: diretorio de trabalho nao encontrado: $WorkingDirectory"
}

$IconLocation = $ResolvedTargetPath
if (-not [string]::IsNullOrWhiteSpace($IconPath)) {
    $ResolvedIcon = Resolve-Path -LiteralPath $IconPath -ErrorAction SilentlyContinue
    if (-not $ResolvedIcon) {
        throw "DESKTOP_ICON_INVALID: icone nao encontrado: $IconPath"
    }
    Assert-DesktopIconFile -Path $ResolvedIcon.Path
    $IconLocation = $ResolvedIcon.Path
}

$ShortcutDirectory = Split-Path $ShortcutPath -Parent
if (-not [string]::IsNullOrWhiteSpace($ShortcutDirectory)) {
    New-Item -ItemType Directory -Force -Path $ShortcutDirectory | Out-Null
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $ResolvedTargetPath
$Shortcut.WorkingDirectory = $ResolvedWorkingDirectory.Path
$Shortcut.IconLocation = $IconLocation
$Shortcut.Save()

Write-Host "Atalho criado: $ShortcutPath"
