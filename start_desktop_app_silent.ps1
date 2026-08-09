$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\CAMINHO\SINTETICO"
$entrypoint = Join-Path $projectRoot "desktop_app.py"

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $python
$startInfo.Arguments = '"' + $entrypoint + '"'
$startInfo.WorkingDirectory = $projectRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true

[System.Diagnostics.Process]::Start($startInfo) | Out-Null
