param(
    [ValidateSet("Build", "Test", "Export", "All")][string]$Action = "All",
    [string]$BuildHost = "carbon"
)
$ErrorActionPreference = "Stop"
$remote = Join-Path (Split-Path -Parent $PSScriptRoot) "tools\remote-build.ps1"
if ($Action -eq "Export") {
    & $remote -Stage boot -Action Export -BuildHost $BuildHost
} else {
    & $remote -Stage boot -Action Start -BuildAction $Action -BuildHost $BuildHost
}
